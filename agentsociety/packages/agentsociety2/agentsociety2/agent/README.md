# Agent 模組

面向基於 Agent 建模（ABM）研究的 Skills-first 智慧體框架。

## 設計理念

### 核心原則

1. **技能優先架構**
   Agent 能力透過 Skill 模組動態擴充套件，而非硬編碼。使用者可定義自定義 Skill，系統自動發現並整合。

2. **統一配置管理**
   所有配置集中於 `AgentConfig`，支援環境變數覆蓋和執行時調整。

3. **長時間執行支援**
   內建檢查點、預寫日誌（WAL）和工作區清理機制，支援崩潰恢復和長時間模擬。

4. **上下文視窗管理**
   借鑑 Claude Code 最佳實踐：簡潔上下文、漸進式技能披露、自動壓縮。

## 架構

```
┌─────────────────────────────────────────────────────────────┐
│                      PersonAgent                            │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ AgentConfig │  │ SkillRuntime │  │   PromptBuilder  │   │
│  │ (配置)       │  │ (技能執行)    │  │   (模組化Prompt) │   │
│  └─────────────┘  └──────────────┘  └──────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Persistence Layer                       │   │
│  │  Checkpoint │ WriteAheadLog │ WorkspaceCleaner      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Concurrency Control                     │   │
│  │  PriorityScheduler │ RateLimiter │ DeadlockDetector │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 目錄結構

```
agent/
├── person.py          # PersonAgent 實現
├── base.py            # Agent 抽象基類
├── config.py          # 統一配置
├── prompt_builder.py  # 模組化 Prompt 構建
├── persistence.py     # 檢查點、WAL、清理
├── concurrent.py      # 優先順序排程、限流
├── context.py         # 上下文管理、Token 計數
├── tool/              # 工具模組
│   ├── decision.py    # ToolDecision 模型
│   ├── loop_detection.py  # 迴圈檢測
│   ├── security.py    # bash 命令安全檢查（黑名單 token/模式/危險子串）
│   └── utils.py       # 工具函式
├── skills/            # 技能系統
│   ├── __init__.py    # SkillRegistry
│   ├── runtime.py     # AgentSkillRuntime
│   ├── observation/   # 環境感知
│   ├── cognition/     # 情緒、需求、意圖
│   ├── memory/        # 長期記憶、關係
│   └── plan/          # 行動執行
```

## 核心元件

### 1. AgentConfig - 統一配置

```python
from agentsociety2.agent import AgentConfig

config = AgentConfig()
config.model.context_window          # 200000
config.loop.max_rounds               # 24
config.persistence.checkpoint_interval  # 10
```

### 2. Persistence - ACID 保證

```python
from agentsociety2.agent import Checkpoint, WriteAheadLog

checkpoint = Checkpoint(workspace, config)
checkpoint.save(tick=100, state={"step_count": 42})

wal = WriteAheadLog(workspace)
intent_id = wal.log_intent("execute_skill", {"skill": "cognition"}, tick=1)
wal.log_result(intent_id, {"ok": True})
```

### 3. Concurrency - 優先順序排程

```python
from agentsociety2.agent import PriorityScheduler, RateLimiter, DeadlockDetector

scheduler = PriorityScheduler(max_concurrent=5)
await scheduler.submit("task1", my_coro(), Priority.HIGH)

limiter = RateLimiter(rps=10, burst=20)
await limiter.acquire()

detector = DeadlockDetector(timeout=60.0)
detector.register("operation1")
```

### 4. Context Management - AGENT.md

`AGENT.md` 由執行時元件 `AgentSkillRuntime` 自動維護（包含 YAML frontmatter 與自動生成的檔案索引區塊）。
Agent 可透過 `workspace_read("AGENT.md")` 獲取當前上下文與檔案索引。

## 內建技能

| 技能 | 功能 | 輸入 | 輸出 |
|-----|------|-----|------|
| observation | 環境感知 | - | observation.txt |
| cognition | 情緒、需求、意圖生成 | observation.txt | emotion.json, needs.json, intention.json |
| memory | 長期記憶、人際關係 | observation.txt | memory.jsonl, relationships.json |
| plan | 行動執行 | intention.json | plan_state.json |

### 技能後設資料

```yaml
---
name: cognition
description: 核心認知技能，生成情緒、需求和意圖。
inputs:
  - state/observation.txt
outputs:
  - state/emotion.json
  - state/needs.json
  - state/intention.json
---
```

## 工作區結構

```
agent_0001/
├── state/              # 技能狀態檔案
│   ├── emotion.json    # 情緒狀態
│   ├── needs.json      # 生理/社交需求
│   ├── intention.json  # 當前目標
│   └── memory.jsonl    # 長期記憶日誌
├── logs/               # 執行日誌
│   ├── tool_calls.jsonl
│   └── thread_messages.jsonl
├── checkpoints/        # 恢復快照
├── wal/               # 預寫日誌
│   ├── wal.jsonl
│   └── index.json
└── AGENT_CONTEXT.md   # 動態上下文（CLAUDE.md 風格）
```

## AGENT_CONTEXT.md 設計

借鑑 Claude Code 的 CLAUDE.md 最佳實踐：

- **簡潔**：不超過 2000 字元
- **結構化**：YAML frontmatter + Markdown 章節
- **活文件**：每 tick 更新
- **焦點優先**：當前任務醒目展示

示例：

```markdown
---
current_focus: 在咖啡館吃午餐
tick: 42
location: downtown_cafe
energy: 0.65
mood: content
---

# Agent Context

## Current Focus
正在主街的咖啡館吃午餐。

## Key Decisions
- 選擇步行而非乘坐公交
- 點了今日特餐

## Patterns
- 1 公里內的距離偏好步行

## Known Issues
- 錢包現金不足
```

## 快速開始

```python
from agentsociety2.agent import PersonAgent, AgentConfig
from datetime import datetime

agent = PersonAgent(
    id=1,
    profile={"name": "Alice", "age": 25},
)
await agent.init(env)
result = await agent.step(tick=300, t=datetime.now())
```

## 環境變數

| 變數 | 預設值 | 說明 |
|-----|-------|------|
| AGENT_MODEL | "" | 模型名稱 |
| AGENT_CONTEXT_WINDOW | 200000 | 上下文視窗大小 |
| AGENT_MAX_TOOL_ROUNDS | 24 | 最大工具迴圈輪數 |
| AGENT_CHECKPOINT_INTERVAL | 10 | 檢查點間隔（ticks） |

## 測試

```bash
# 執行單元測試
pytest tests/test_agent_modules.py -v

# 執行覆蓋率測試
pytest tests/ --cov=agentsociety2.agent
```
