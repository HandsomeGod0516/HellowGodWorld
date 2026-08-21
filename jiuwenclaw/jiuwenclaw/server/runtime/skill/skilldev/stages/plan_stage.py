# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PLAN 階段處理器.

職責：
- 建立 PLAN 專屬 ReActAgent（配備搜尋工具 + 規劃 Prompt）
- Agent 分析需求，輸出結構化的 JSON 開發計劃
- 將 plan 寫入 state，跳轉到 PLAN_CONFIRM 掛起點
- Pipeline 在掛起點自動推送 CONFIRM_REQUEST 彈框（含 plan 資料）

Agent 工具白名單：["web_search"]（禁止檔案寫入，PLAN 階段只規劃不執行）
"""

from __future__ import annotations

import json
import logging

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevEventType, SkillDevStage
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)

PLAN_SYSTEM_PROMPT = """你是一個 Skill 架構師。根據使用者需求，設計一份結構化的 Skill 開發計劃。

## 第一步：Capture Intent（需求理解）

在設計之前，先從需求中提取以下關鍵資訊：
1. 這個 Skill 要讓模型能做什麼？
2. 什麼使用者場景/措辭應觸發這個 Skill？
3. 預期的輸出格式是什麼？
4. 輸出是否可客觀驗證（適合自動化測試），還是主觀的（更適合人工評審）？

## 第二步：Interview & Research（深入調研）

主動識別並記錄：
- 邊緣案例
- 輸入/輸出格式約束
- 依賴工具或 MCP
- 成功標準
- 可能的領域知識來源

## 第三步：輸出 JSON Plan

```json
{
  "skill_name": "kebab-case 標識名",
  "display_name": "使用者可見名稱",
  "description": "觸發描述——用祈使句，覆蓋觸發場景，稍微'激進'以避免欠觸發",
  "purpose": "這個 skill 解決什麼問題",
  "intent_capture": {
    "what": "Skill 賦予模型的能力",
    "when": "觸發場景",
    "output_format": "預期輸出格式",
    "testable": true
  },
  "directory_structure": {
    "SKILL.md": "主指令檔案",
    "scripts/xxx.py": "檔案職責說明"
  },
  "key_decisions": [
    "決策1：為什麼選擇 X 而不是 Y"
  ],
  "test_strategy": {
    "approach": "測試方法描述",
    "test_cases_outline": ["場景1", "場景2", "場景3"]
  },
  "estimated_complexity": "low | medium | high"
}
```

## 設計原則

### 目錄結構決策
- 有重複性確定步驟 → 放 scripts/（每次呼叫省去重新發明輪子）
- 有領域知識文件 → 放 references/（按需載入，不膨脹主檔案）
- 有模板/圖示/字型 → 放 assets/（輸出時直接引用）
- SKILL.md 目標 <500 行；超過則拆分到 references/ 並標明查閱時機

### 描述的觸發性
當前模型傾向於不夠主動觸發 Skill。description 應略微"推進式"：
- 除了說明功能，還要列舉具體使用場景
- 即使使用者沒有明確提到 skill 名稱也應觸發
- 對標相似能力的區分點

### 修改模式
如果是修改已有 skill，先分析現有結構的優劣，plan 側重差量而非全量重寫。
"""


class PlanStageHandler(StageHandler):
    """PLAN 階段：Agent 生成開發計劃，隨後進入 PLAN_CONFIRM 掛起點."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": "正在分析需求並生成開發計劃..."}
        )

        plan = await self._generate_plan(ctx)
        ctx.state.plan = plan

        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": "開發計劃已生成，等待確認"}
        )
        return StageResult(next_stage=SkillDevStage.PLAN_CONFIRM)

    async def _generate_plan(self, ctx: SkillDevContext) -> dict:
        """呼叫 ReActAgent 生成 plan JSON.

        待實現: 接入 create_stage_agent + Runner.run_agent，流式推送 AGENT_THINKING 事件
        """
        # 待實現:
        # agent = ctx.create_stage_agent(
        #     stage_name="plan",
        #     system_prompt=PLAN_SYSTEM_PROMPT,
        #     tools=["web_search"],
        #     max_iterations=15,
        # )
        # messages = self._build_messages(ctx)
        # plan_text = ""
        # async for chunk in agent.stream(messages):
        #     await ctx.emit(SkillDevEventType.AGENT_THINKING, {"delta": chunk.content})
        #     plan_text += chunk.content
        # plan = self._parse_plan_json(plan_text)
        # if ctx.state.existing_skill_md:
        #     plan["diff_analysis"] = "待實現: 差量分析"
        # return plan

        logger.warning("[PlanStage] _generate_plan 尚未實現，返回佔位 plan")
        query = ctx.state.input.get("query", "")
        return {
            "skill_name": "placeholder-skill",
            "display_name": "佔位 Skill",
            "description": f"根據需求『{query}』生成的 skill（待實現）",
            "purpose": "待實現",
            "directory_structure": {"SKILL.md": "主指令檔案"},
            "key_decisions": [],
            "test_strategy": {"approach": "待實現", "test_cases_outline": []},
            "estimated_complexity": "medium",
        }

    def _build_messages(self, ctx: SkillDevContext) -> list[dict]:
        """構造傳送給 PLAN Agent 的訊息列表."""
        query = ctx.state.input.get("query", "")
        parts = [f"需求：{query}"]

        if ctx.state.reference_texts:
            refs = "\n\n".join(ctx.state.reference_texts[:3])  # 限制上下文長度
            parts.append(f"參考資料：\n{refs}")

        if ctx.state.existing_skill_md:
            parts.append(f"已有 SKILL.md：\n{ctx.state.existing_skill_md}")

        return [{"role": "user", "content": "\n\n".join(parts)}]

    def _parse_plan_json(self, text: str) -> dict:
        """從 Agent 輸出中提取 JSON plan.

        待實現: 加入容錯解析（Agent 可能在 JSON 前後輸出額外文字）
        """
        # 簡單實現：找到第一個 { 到最後一個 }
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("Agent 未輸出有效的 JSON plan")
        return json.loads(text[start:end])
