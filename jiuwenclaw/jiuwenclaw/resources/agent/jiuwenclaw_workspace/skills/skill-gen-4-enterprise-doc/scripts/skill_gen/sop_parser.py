"""SOP document ingestion and structured extraction.

Loads plain text from files (see ``_extract_raw_text``), then extracts a full
``SOPStructure`` via the LLM prompts in this module and ``sop_chunk_merge``
(single-shot or chunked, budget-aware).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from .models import SOPStructure
from .sop_chunk_merge import extract_structure_chunked, extract_structure_single_shot

logger = logging.getLogger(__name__)

DEFAULT_SINGLE_SHOT_BUDGET = 12000
DEFAULT_MAX_CHUNK_CHARS = 8000
DEFAULT_CHUNK_OVERLAP = 1200
DEFAULT_MAX_CONTEXT_CHARS = 120_000
DEFAULT_SAFETY_MARGIN = 2000

_SOP_EXTRACTION_PROMPT = """\
你是一名企業 SOP 結構化分析專家，擅長從各種格式的 SOP / 政策 / 流程文件中提取結構化資訊。

**體裁說明**：下文中的行業場景、數字與流程**僅用於示範抽取 JSON 的格式**（提示工程常見做法），為虛構或合成示意，**不代表**產品與任何特定客戶、業務線或內部制度繫結。對真實輸入文件應忠實於原文，勿套用下列範例中的具體數值或域名。

## 任務

給你一份 SOP 原始文字，請完成以下兩步：

### 第一步：判斷 SOP 型別

先通讀全文，判斷該文件屬於以下哪種型別：

- **procedural**（流程型）：文件以**按時間順序執行的操作步驟**為主。大部分內容是"第一步做什麼、第二步做什麼"的指令序列。例：員工入職流程、軟體釋出審批流程。
- **knowledge**（知識/政策型）：文件以**規則、標準、條件、限額、場景分支**為主。可能有一個簡短的流程骨架，但大部分篇幅是各種場景下的具體規定和參考資訊。例：變更視窗與釋出管控規定、客服工單 SLA、合規抽檢比例、**事項登記/辦結超期與階梯扣減**類規定、資料安全管理規定。
- **hybrid**（混合型）：既有清晰的流程步驟，又有大量嵌入式規則和條件分支。例：採購審批流程（流程清晰，但不同金額/類別有不同審批規則）。

### 第二步：按型別提取結構化資訊

#### `steps`（操作步驟）—— 僅提取真正的順序操作

steps 只放**按時間先後執行的具體操作動作**。判斷標準：這件事是某個人/系統在某個時間點**做**的一個動作。

✅ 應放入 steps：
- "運維工程師在變更系統中提交生產釋出申請並附帶回滾方案" —— 這是一個動作
- "客服坐席在工單系統中受理客戶請求並建立工單" —— 這是一個動作
- "經辦人在系統完成流程辦結登記並提交時間戳確認" —— 這是一個動作（僅為跨領域格式示意）

❌ 不應放入 steps：
- "生產環境週五 18:00 至週一 10:00 禁止未經審批的變更" —— 這是一條規則，放 knowledge_items
- "P2 工單須在 4 小時內首次響應" —— 這是一個 SLA 引數，放 knowledge_items
- "審計發現的高風險缺陷須在 30 日內閉環整改" —— 這是一條時限罰則，放 knowledge_items

對於 knowledge 型文件，steps 可能很少（3-5 個骨架步驟），這是正常的。不要為了填充 steps 而把規則偽裝成步驟。

#### `knowledge_items`（知識條目）—— 提取規則、標準、閾值、條件、罰則

knowledge_items 放**非流程性的參考知識**：規則、政策、標準、限額、條件判斷、處罰規定、場景特例、速查資訊。

每條 knowledge_item 必須是**自包含的完整語句**，包含該規則的條件和結論。讀者不需要看上下文就能理解這條規則的含義。

✅ 好的 knowledge_item（具體、自包含、保留原文數字）：
- "生產環境變更須透過 CAB 評審；緊急變更須在事後 24 小時內補錄評審記錄"
- "P1 級故障須在識別後 15 分鐘內通知值班負責人並建立應急溝通渠道"
- "正式登記日晚於基準事件日 90 天至 365 天的，系統按制度實施階梯扣減；超過 365 天關閉補錄或不予受理（僅為跨領域體裁示意）"
- "合規抽檢：高風險流程每年全覆蓋，中風險流程每年隨機抽檢不少於 30% 筆數"
- "客戶投訴類工單須在建立後 24 小時內由客服組長複核處理結論並回復客戶"

❌ 差的 knowledge_item（模糊、缺少數字、需要上下文才能理解）：
- "變更有視窗限制" —— 缺少具體時段或例外條件
- "工單響應要快" —— 沒有保留具體時限
- "超期會有扣減" —— 沒有保留具體比例和時間

**提取原則：寧多勿少。** 寧可多提取幾條規則也不要遺漏重要的標準和閾值。原文中出現的具體數字、金額、比例、時間限制、重量限制等必須原樣保留在 knowledge_item 中。

#### `sections`（章節結構）—— 保留文件的層級組織

提取文件中的章節/小節層級結構。每個 section 包含：
- `id`：原文編號（如 "1.1", "2.3.1", "Part 2"），如無編號則用 "s1", "s2" 等
- `title`：章節標題
- `content_summary`：該章節主要內容的一句話概述

#### 其他欄位

- `decision_points`：流程中的條件分支（"如果A則B，否則C"）
- `exceptions`：異常/邊界場景的處理方式
- `references`：引用的外部文件、系統、URL

如果某欄位在原文中未明確提及，留空字串或空列表，不要編造。

**JSON 硬約束（必須遵守）**：`steps[].step_number` 與 `sections[].id` 必須是**帶雙引號的字串**（例如 `"1"`、`"1.1"`、`"2.3.1"`）。**禁止**輸出未加引號的層級編號如 `1.1.1`——在 JSON 中這是非法數字，會導致解析失敗。

## 輸出 JSON 格式

```json
{{
  "title": "SOP 標題",
  "purpose": "SOP 目的/目標",
  "scope": "適用範圍",
  "sop_type": "procedural | knowledge | hybrid",
  "roles": ["角色1", "角色2"],
  "steps": [
    {{
      "step_number": "1",
      "actor": "執行者/角色",
      "action": "具體操作描述",
      "system": "使用的工具或系統（如有）",
      "output": "預期輸出/交付物",
      "notes": "條件、注意事項"
    }}
  ],
  "knowledge_items": [
    "自包含的規則/標準/閾值/條件語句，保留原文數字"
  ],
  "sections": [
    {{
      "id": "2.1",
      "title": "章節標題",
      "content_summary": "該章節主要內容一句話概述"
    }}
  ],
  "decision_points": [
    "條件分支描述"
  ],
  "exceptions": [
    "異常/邊界場景處理方式"
  ],
  "references": [
    "引用的外部文件或系統"
  ]
}}
```

## 示例 A：流程型 SOP

**輸入片段**：
> # 員工入職流程
> 1. HR 在系統中建立新員工賬號
> 2. IT 部門配置工位和電腦
> 3. 部門經理安排入職培訓
> 4. 員工簽署保密協議

**預期輸出**：
```json
{{
  "sop_type": "procedural",
  "steps": [
    {{"step_number": "1", "actor": "HR", "action": "在系統中建立新員工賬號", "system": "HR系統", "output": "員工賬號", "notes": ""}},
    {{"step_number": "2", "actor": "IT部門", "action": "配置工位和電腦", "system": "", "output": "工位和裝置就緒", "notes": ""}},
    {{"step_number": "3", "actor": "部門經理", "action": "安排入職培訓", "system": "", "output": "", "notes": ""}},
    {{"step_number": "4", "actor": "員工", "action": "簽署保密協議", "system": "", "output": "已簽署的保密協議", "notes": ""}}
  ],
  "knowledge_items": [],
  "sections": []
}}
```

## 示例 B：知識/政策型 SOP（客服工單 SLA 與升級 — 虛構示意）

**輸入片段**：
> # 客服工單處理與升級規範
> 1.1 一線坐席在系統中建立工單並標註優先順序（P1–P4）
> 1.2 升級規則：P2 故障若 4 小時內無二線接手，自動升級到值班主管；投訴類工單須在 24 小時內由組長複核結論
> 2.1 P1：核心業務不可用，須在 15 分鐘內電話通知值班負責人並拉通應急群
> 2.2 P3–P4：工作日按佇列順序處理，單工單無故擱置不得超過 48 小時
> 3.1 知識庫已覆蓋的諮詢類問題，首次響應須在 30 分鐘內給出標準答覆或文件連結

**預期輸出**：
```json
{{
  "sop_type": "hybrid",
  "steps": [
    {{"step_number": "1", "actor": "一線坐席", "action": "在系統中建立工單並標註優先順序", "system": "工單系統", "output": "已分級工單", "notes": ""}},
    {{"step_number": "2", "actor": "二線工程師", "action": "接手故障工單並排查", "system": "", "output": "排查記錄或解決方案", "notes": "按優先順序 SLA"}}
  ],
  "knowledge_items": [
    "P2 級故障工單若 4 小時內無二線工程師接手，系統自動升級並通知值班主管",
    "投訴類工單須在建立後 24 小時內由客服組長複核處理結論並回復客戶",
    "P1 級（核心業務不可用）須在識別後 15 分鐘內電話通知值班負責人並建立應急溝通群",
    "P3、P4 級工單在工作日須按佇列順序處理，單條工單無故擱置不得超過 48 小時",
    "知識庫已覆蓋的諮詢類工單，首次響應須在 30 分鐘內給出標準答覆或有效文件連結"
  ],
  "sections": [
    {{"id": "1.1", "title": "工單建立與分級", "content_summary": "坐席建立工單並標註 P1–P4"}},
    {{"id": "1.2", "title": "升級規則", "content_summary": "超時自動升級與投訴複核時限"}},
    {{"id": "2.1", "title": "P1 應急響應", "content_summary": "15 分鐘內通知負責人並建群"}},
    {{"id": "2.2", "title": "P3–P4 處理", "content_summary": "佇列處理與擱置上限"}},
    {{"id": "3.1", "title": "諮詢類 SLA", "content_summary": "30 分鐘內標準答覆或文件"}}
  ],
  "decision_points": [
    "工單為 P1 時是否立即啟動應急通知流程",
    "投訴類是否必須在 24 小時內經組長複核"
  ],
  "exceptions": [],
  "references": []
}}
```

## 示例 C：登記時效與階梯扣減（節選，僅示範「時限 + 數字」體裁，虛構）

**輸入片段**：
> # 通用流程辦結與登記時效（節選，虛構示意）
> 4.1 正式登記日晚於基準事件日 90 天提交的，系統按制度實施階梯扣減；超過 365 天的不予受理或關閉補錄
> 4.2 缺失必填佐證材料時須在系統選擇「缺件宣告」，並按制度計提扣減（具體比例以原文為準）

**預期輸出**：
```json
{{
  "sop_type": "knowledge",
  "steps": [],
  "knowledge_items": [
    "正式登記日晚於基準事件日 90 天的，系統按制度實施階梯扣減；超過 365 天提交的不予受理",
    "缺失必填佐證材料時須在系統選擇「缺件宣告」，並按制度計提扣減，扣減比例以制度原文記載為準"
  ],
  "sections": [
    {{"id": "4.1", "title": "登記時效", "content_summary": "90 天與 365 天兩條時限及階梯扣減或不予受理"}},
    {{"id": "4.2", "title": "缺件與扣減", "content_summary": "系統宣告選項與扣減依原文"}}
  ],
  "decision_points": [],
  "exceptions": [],
  "references": []
}}
```

## SOP 原始文字

{raw_text}

請只輸出 JSON，不要輸出其他內容。"""


def _single_shot_prompt_overhead() -> int:
    """Length of single-shot template with empty raw_text placeholder."""
    return len(_SOP_EXTRACTION_PROMPT) - len("{raw_text}")


def _room_for_sop_single(max_context_chars: int, safety_margin: int) -> int:
    """Max SOP chars that fit in a single-shot prompt."""
    return max(1, max_context_chars - _single_shot_prompt_overhead() - safety_margin)


def _default_parse_options(options: dict[str, Any] | None) -> dict[str, Any]:
    opts = dict(options or {})
    opts.setdefault("sop_parse_mode", "auto")
    opts.setdefault("single_shot_budget", DEFAULT_SINGLE_SHOT_BUDGET)
    opts.setdefault("max_chunk_chars", DEFAULT_MAX_CHUNK_CHARS)
    opts.setdefault("chunk_overlap", DEFAULT_CHUNK_OVERLAP)
    opts.setdefault("max_context_chars", DEFAULT_MAX_CONTEXT_CHARS)
    opts.setdefault("safety_margin", DEFAULT_SAFETY_MARGIN)
    return opts


async def parse_sop_raw_text(
    raw_text: str,
    *,
    invoke_llm_json: Callable[..., Any],
    parse_options: dict[str, Any] | None = None,
    source_label: str = "",
) -> tuple[SOPStructure, dict[str, Any]]:
    """Run SOP structure extraction on already-loaded plain text (file, URL body, etc.).

    ``invoke_llm_json`` is required: full ``SOPStructure`` extraction is LLM-only
    (prompts in this module and ``sop_chunk_merge``).

    ``source_label`` is stored in ``extraction_meta`` (e.g. file path or URL).
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ValueError("SOP 文字為空")

    opts = _default_parse_options(parse_options)
    mode = str(opts["sop_parse_mode"]).strip().lower()
    max_context = int(opts["max_context_chars"])
    safety_margin = int(opts["safety_margin"])
    room_single = _room_for_sop_single(max_context, safety_margin)
    single_shot_cap = int(opts["single_shot_budget"])
    if single_shot_cap > 0 and single_shot_cap < room_single:
        room_single = single_shot_cap
    max_chunk = int(opts["max_chunk_chars"])
    overlap = int(opts["chunk_overlap"])

    use_chunked: bool
    if mode == "chunked":
        use_chunked = True
    elif mode == "single":
        use_chunked = False
    else:
        # auto: chunk only when full prompt (template + SOP) would exceed context
        use_chunked = len(raw_text) > room_single

    extraction_meta: dict[str, Any]

    if use_chunked:
        sop, extraction_meta = await extract_structure_chunked(
            raw_text,
            invoke_llm_json,
            max_context_chars=max_context,
            safety_margin=safety_margin,
            max_chunk_chars=max_chunk if max_chunk > 0 else None,
            chunk_overlap=overlap,
            run_reconcile=True,
        )
        extraction_meta["single_shot_budget"] = room_single
        extraction_meta["source_path"] = source_label

        empty_chunk_warnings = [
            w
            for w in extraction_meta.get("merge_warnings", [])
            if isinstance(w, str) and w.startswith("chunk_") and "empty" in w
        ]
        if len(empty_chunk_warnings) >= 2:
            logger.warning(
                "[SOPParser] Chunked extraction lost %s partials; running single-shot fallback",
                len(empty_chunk_warnings),
            )
            prompt_truncated_ss = len(raw_text) > room_single
            text_for_prompt_ss = raw_text[:room_single] if prompt_truncated_ss else raw_text
            sop_ss, meta_ss = await extract_structure_single_shot(
                text_for_prompt_ss,
                invoke_llm_json,
                full_prompt_template=_SOP_EXTRACTION_PROMPT,
                single_shot_budget=room_single,
                prompt_truncated=prompt_truncated_ss,
            )
            score_c = len(sop.steps) + len(sop.knowledge_items) + len(sop.sections)
            score_s = len(sop_ss.steps) + len(sop_ss.knowledge_items) + len(sop_ss.sections)
            if score_s >= score_c:
                sop = sop_ss
                extraction_meta["replaced_by_single_shot_after_chunk_loss"] = True
                extraction_meta["single_shot_fallback_meta"] = {
                    "mode": meta_ss.get("mode"),
                    "merge_warnings": list(meta_ss.get("merge_warnings", [])),
                }
                if prompt_truncated_ss:
                    extraction_meta.setdefault("merge_warnings", []).append(
                        f"single_shot_fallback_truncated_to_{room_single}_chars"
                    )

        if not sop.steps and not sop.title and not sop.knowledge_items:
            extraction_meta["merge_warnings"].append("extraction_empty_after_chunked")
            raise ValueError(
                "SOP LLM extraction produced no usable structure after chunked pass "
                f"(no steps, title, or knowledge_items). merge_warnings={extraction_meta.get('merge_warnings')}"
            )
    else:
        prompt_truncated = len(raw_text) > room_single
        text_for_prompt = raw_text[:room_single] if prompt_truncated else raw_text
        sop, extraction_meta = await extract_structure_single_shot(
            text_for_prompt,
            invoke_llm_json,
            full_prompt_template=_SOP_EXTRACTION_PROMPT,
            single_shot_budget=room_single,
            prompt_truncated=prompt_truncated,
        )
        extraction_meta["source_path"] = source_label
        if prompt_truncated:
            extraction_meta.setdefault("merge_warnings", []).append(
                f"single_shot_prompt_truncated_to_{room_single}_chars"
            )
        if not sop.steps and not sop.title and not sop.knowledge_items:
            extraction_meta.setdefault("merge_warnings", []).append("extraction_empty_single_shot")
            raise ValueError(
                "SOP LLM extraction produced no usable structure "
                f"(no steps, title, or knowledge_items). merge_warnings={extraction_meta.get('merge_warnings')}"
            )

    weak_reasons: list[str] = []
    if use_chunked:
        if extraction_meta.get("chunk_count", 0) > 1 and not sop.title.strip():
            weak_reasons.append("missing_title_after_chunk_merge")
        if extraction_meta.get("chunk_count", 0) > 1 and not sop.scope.strip() and len(sop.sections) >= 3:
            weak_reasons.append("missing_scope_with_many_sections")
        if extraction_meta.get("chunk_count", 0) > 1 and len(sop.knowledge_items) < 3 and len(raw_text) > room_single:
            weak_reasons.append("too_few_knowledge_items_for_long_sop")
        if extraction_meta.get("chunk_count", 0) > 1 and not extraction_meta.get("reconcile_applied", False):
            weak_reasons.append("reconcile_not_applied")
    extraction_meta["output_counts"] = {
        "steps": len(sop.steps),
        "knowledge_items": len(sop.knowledge_items),
        "sections": len(sop.sections),
        "decision_points": len(sop.decision_points),
        "exceptions": len(sop.exceptions),
    }
    extraction_meta["weak_reasons"] = weak_reasons
    extraction_meta["use_raw_excerpt_draft_fallback"] = bool(weak_reasons)
    sop.raw_text = raw_text
    return sop, extraction_meta


async def parse_sop_file(
    file_path: str | Path,
    *,
    invoke_llm_json: Callable[..., Any],
    parse_options: dict[str, Any] | None = None,
) -> tuple[SOPStructure, dict[str, Any]]:
    """Parse an SOP document and extract structured content.

    Returns ``(SOPStructure, extraction_meta)``. Full document text is always
    stored in ``SOPStructure.raw_text``.

    parse_options keys:
        - sop_parse_mode: ``auto`` | ``single`` | ``chunked``
        - max_context_chars: model context limit (default 120000)
        - safety_margin: buffer (default 2000)
        - single_shot_budget: when > 0 and below computed room, caps single-shot character budget
        - max_chunk_chars: cap per chunk when chunking (default 8000)
        - chunk_overlap: overlap parameter for chunked extraction; surfaced in extraction_meta
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"SOP 檔案不存在: {file_path}")

    raw_text = await _extract_raw_text(file_path)
    if not raw_text.strip():
        raise ValueError(f"SOP 檔案內容為空: {file_path}")

    sop, meta = await parse_sop_raw_text(
        raw_text,
        invoke_llm_json=invoke_llm_json,
        parse_options=parse_options,
        source_label=str(file_path),
    )
    return sop, meta


async def _extract_raw_text(file_path: Path) -> str:
    """Extract raw text from an SOP file using AutoFileParser when available."""
    try:
        from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import (
            AutoFileParser,
        )

        parser = AutoFileParser()
        if parser.supports(str(file_path)):
            documents = await parser.parse(str(file_path))
            chunks = [doc.text for doc in documents if getattr(doc, "text", "")]
            if chunks:
                return "\n\n".join(chunks)
            logger.warning("[SOPParser] AutoFileParser returned empty, falling back to direct read")
    except ImportError:
        logger.info("[SOPParser] agent-core AutoFileParser unavailable, using direct read")
    except Exception as exc:
        logger.warning("[SOPParser] AutoFileParser error: %s, falling back to direct read", exc)

    suffix = file_path.suffix.lower()
    if suffix in {".md", ".txt", ".json", ".yaml", ".yml"}:
        return file_path.read_text(encoding="utf-8")
    raise ValueError(
        f"無法解析 SOP 檔案 {file_path.name}: "
        f"安裝 agent-core 以支援 {suffix} 格式，或提供 .md/.txt 檔案"
    )
