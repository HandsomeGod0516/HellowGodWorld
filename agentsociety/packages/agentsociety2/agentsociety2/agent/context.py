"""Agent 上下文：配置閾值以外的記憶、thread 壓縮、token 計量。

模組職責
========

- 與 context_config 分工：本檔案負責「記憶 + 壓縮演算法 + 摘要 prompt」；config 僅 dataclass 與 capability 對映
- Token：優先 LiteLLM 的 token_counter（與路由模型名一致），失敗再用 tiktoken，並與字元下界取 max 做保守估計

主要元件
========

- :class:`ThreadTokenCounter`: 訊息 token 計數器
- :class:`AgentMemory`: 持久化記憶（AGENT_MEMORY.md）
- :class:`StructuredSummary`: 結構化摘要
- :func:`run_thread_compaction`: Thread 分層壓縮

壓縮策略
========

分層壓縮機制：

1. **Light pruning**: 去重相鄰工具結果，按優先順序丟棄低優先順序訊息
2. **Medium compression**: 呼叫 LLM 生成結構化摘要
3. **Heavy compression**: 滾動摘要合併，適用於極高利用率

示例
====

基本使用::

    from agentsociety2.agent.context import (
        ThreadTokenCounter,
        AgentMemory,
        run_thread_compaction,
    )

    # Token 計數
    counter = ThreadTokenCounter(litellm_model="claude-3-opus")
    tokens = counter.count_messages(messages)

    # 記憶管理
    memory = AgentMemory(workspace_path)
    memory.add_decision("Decided to go shopping")

    # Thread 壓縮
    result = await run_thread_compaction(
        thread_messages=messages,
        agent_id=1,
        cfg=config.context,
        litellm_model="claude-3-opus",
        ...
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, field_validator
from ruamel.yaml import YAML

from agentsociety2.agent.config import ContextConfig
from agentsociety2.agent.tool.utils import jr_dumps as _jr_dumps, jr_parse
from agentsociety2.logger import get_logger
from litellm import token_counter as _LITELLM_TOKEN_COUNTER

logger = get_logger()


_yaml = YAML(typ="safe")
_yaml.default_flow_style = False

_MIN_OLD_SEGMENTS = 2
_ROLE_OVERHEAD_TOKENS = 4


class ThreadTokenCounter:
    """訊息 token 計數器。

    :class:`ThreadTokenCounter` 優先使用 LiteLLM 的 ``token_counter``（儘量貼近真實路由模型 tokenizer）。
    當計數介面不可用或失敗時，回退到 tiktoken 或字元長度啟發式（保守估計，避免低估）。
    """

    def __init__(
        self,
        litellm_model: str = "",
        encoding_name: Optional[str] = None,
    ):
        """初始化 token 計數器。

        :param litellm_model: 與 LiteLLM 路由一致的模型名（建議完整形如 ``provider/model``）。
        :type litellm_model: str
        :param encoding_name: 可選的 tiktoken 編碼名；不提供則根據模型 id 推斷。
        :type encoding_name: str | None
        """
        self.litellm_model = (litellm_model or "").strip()
        self.encoding_name = encoding_name or "cl100k_base"
        self._encoder: Any = None
        self._approx_chars_per_token = 3.5
        try:
            import tiktoken

            self._encoder = tiktoken.get_encoding(self.encoding_name)
        except Exception as e:
            logger.warning(
                "tiktoken unavailable for encoding %r (%s); using char floor only for local counts",
                self.encoding_name,
                e,
            )
            self.encoding_name = f"approx({self.encoding_name})"

    def _char_floor_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // 4)

    def count_text(self, text: str) -> int:
        """估算一段文字的 token 數。

        :param text: 待計數文字。
        :type text: str
        :return: 估算 token 數（>= 0）。
        :rtype: int
        """
        if not text:
            return 0
        floor = self._char_floor_tokens(text)
        if self._encoder is not None:
            return max(len(self._encoder.encode(text)), floor)
        return max(floor, int(len(text) / self._approx_chars_per_token))

    def count_message(self, m: dict[str, str]) -> int:
        """估算單條 chat message 的 token 數。

        :param m: message，包含 ``role`` 和 ``content``。
        :type m: dict[str, str]
        :return: 估算 token 數。
        :rtype: int
        """
        role = str(m.get("role", "user") or "user")
        content = str(m.get("content", "") or "")
        return _ROLE_OVERHEAD_TOKENS + self.count_text(f"{role}\n{content}")

    def _messages_char_floor(self, messages: list[dict[str, str]]) -> int:
        n = 0
        for m in messages:
            c = str(m.get("content", "") or "")
            n += _ROLE_OVERHEAD_TOKENS + self._char_floor_tokens(c)
        return n

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        """估算一組 messages 的 token 數。

        :param messages: messages 列表。
        :type messages: list[dict[str, str]]
        :return: 估算 token 數。
        :rtype: int
        """
        if self.litellm_model:
            try:
                raw = _LITELLM_TOKEN_COUNTER(
                    model=self.litellm_model, messages=messages
                )
                n: int | None
                if isinstance(raw, int):
                    n = raw
                elif isinstance(raw, dict):
                    v = raw.get("total_tokens")
                    if v is None:
                        v = raw.get("prompt_tokens")
                    try:
                        n = int(v) if v is not None else None
                    except (TypeError, ValueError):
                        n = None
                else:
                    n = None
                if isinstance(n, int) and n > 0:
                    return max(n, self._messages_char_floor(messages))
            except Exception as e:
                logger.debug(
                    "litellm.token_counter failed (%s), fallback to local count", e
                )
        return sum(self.count_message(m) for m in messages)


def estimate_messages_tokens_approx(messages: list[dict[str, str]]) -> int:
    """無 tiktoken/LiteLLM 之外的粗算 token 數（用於最後兜底）。"""
    total = 0
    for m in messages:
        c = m.get("content", "") or ""
        total += (
            _ROLE_OVERHEAD_TOKENS + max(1, len(c) // 3) if c else _ROLE_OVERHEAD_TOKENS
        )
    return total


def default_tiktoken_encoding_for_model(model: str | None) -> str:
    """為給定模型選擇預設 tiktoken 編碼名（用於測試與本地回退）。

    :param model: LiteLLM 模型名（可為空）。
    :returns: tiktoken encoding 名稱。
    """
    _ = model  # 當前實現統一使用 cl100k_base
    return "cl100k_base"


def get_context_utilization(
    messages: list[dict[str, str]],
    context_window: int,
    token_counter: Optional[ThreadTokenCounter] = None,
) -> float:
    """將 messages 的 token 估計為上下文利用率。

    :param messages: messages 列表。
    :type messages: list[dict[str, str]]
    :param context_window: 上下文視窗大小（tokens）。
    :type context_window: int
    :param token_counter: 可選的 token 計數器。
    :type token_counter: ThreadTokenCounter | None
    :return: 利用率，範圍 ``[0.0, 1.0]``。
    :rtype: float
    """
    if context_window <= 0:
        return 1.0
    if token_counter is not None:
        cur = token_counter.count_messages(messages)
    else:
        cur = estimate_messages_tokens_approx(messages)
    return min(1.0, cur / context_window)


def should_compact(
    messages: list[dict[str, str]],
    context_window: int,
    warning_ratio: float = 0.60,
    trigger_ratio: float = 0.70,
    auto_ratio: float = 0.85,
    token_counter: Optional[ThreadTokenCounter] = None,
) -> tuple[bool, float, str]:
    """判斷是否需要進行 thread 壓縮。

    :param messages: messages 列表。
    :type messages: list[dict[str, str]]
    :param context_window: 上下文視窗大小（tokens）。
    :type context_window: int
    :param warning_ratio: 利用率 >= 該值時返回 ``need_compact=False`` 且 status 為 ``warning``。
    :type warning_ratio: float
    :param trigger_ratio: 利用率 >= 該值時返回 ``need_compact=True``。
    :type trigger_ratio: float
    :param auto_ratio: 利用率 >= 該值時返回 ``need_compact=True`` 且 status 為 ``auto_compact``。
    :type auto_ratio: float
    :param token_counter: 可選 token 計數器。
    :type token_counter: ThreadTokenCounter | None
    :return: ``(need_compact, utilization, status)``。
    :rtype: tuple[bool, float, str]
    """
    util = get_context_utilization(messages, context_window, token_counter)
    if util >= auto_ratio:
        return True, util, "auto_compact"
    if util >= trigger_ratio:
        return True, util, "should_compact"
    if util >= warning_ratio:
        return False, util, "warning"
    return False, util, "ok"


def _tool_result_fingerprint(content: str) -> Optional[str]:
    if not content.startswith("TOOL_RESULT_JSON:"):
        return None
    try:
        rest = content.split("\n", 1)[1].strip()
        d = jr_parse(rest)
        if not isinstance(d, dict):
            return None
        action = d.get("action")
        path = d.get("path") or d.get("skill_name")
        ok = d.get("ok")
        return f"{action}|{path}|{ok}"
    except Exception:
        return None


def _message_priority(msg: dict[str, str], index_in_old: int, old_len: int) -> float:
    content = msg.get("content", "") or ""
    role = msg.get("role", "user")
    score = float(index_in_old) * 15.0
    if role == "assistant":
        score += 1200.0
    if not content.startswith("TOOL_RESULT_JSON:"):
        score += 400.0
        return score
    try:
        rest = content.split("\n", 1)[1].strip()
        d = jr_parse(rest)
        if not isinstance(d, dict):
            return score
        if d.get("ok") is False:
            score += 5000.0
        action = str(d.get("action", "") or "")
        if action in ("activate_skill", "execute_skill", "auto_activate_requires"):
            score += 2200.0
        if action == "workspace_write" and d.get("ok"):
            score += 1800.0
        if action in ("workspace_read", "read_skill", "glob", "grep"):
            score += 350.0
        if action in ("codegen", "batch", "bash"):
            score += 600.0
    except Exception:
        pass
    return score


def _dedupe_adjacent_tool_results(
    old: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    if len(old) < 2:
        return old, 0
    out: list[dict[str, str]] = [old[0]]
    dropped = 0
    for m in old[1:]:
        fp_prev = _tool_result_fingerprint(out[-1].get("content", ""))
        fp_cur = _tool_result_fingerprint(m.get("content", ""))
        if fp_prev and fp_cur and fp_prev == fp_cur:
            out[-1] = m
            dropped += 1
            continue
        out.append(m)
    return out, dropped


def _drop_lowest_priority_one(old: list[dict[str, str]]) -> bool:
    """刪除優先順序最低的一條訊息（原地修改）。

    避免每次全量掃描：預先計算所有優先順序，找到最小值後刪除。
    """
    if len(old) <= _MIN_OLD_SEGMENTS:
        return False
    # 一次遍歷找到最小優先順序索引
    worst_i = 0
    worst_score = _message_priority(old[0], 0, len(old))
    for i in range(1, len(old)):
        score = _message_priority(old[i], i, len(old))
        if score < worst_score:
            worst_score = score
            worst_i = i
    del old[worst_i]
    return True


@dataclass
class LightPruneStats:
    dedupe_drops: int = 0
    priority_drops: int = 0


def light_prune_thread_messages(
    messages: list[dict[str, str]],
    keep_recent: int,
    counter: ThreadTokenCounter,
    context_window: int,
    trigger_ratio: float,
) -> tuple[list[dict[str, str]], LightPruneStats]:
    stats = LightPruneStats()
    if len(messages) <= keep_recent + 1:
        return messages[:], stats

    recent = messages[-keep_recent:]
    old = messages[:-keep_recent]
    old, dd = _dedupe_adjacent_tool_results(old)
    stats.dedupe_drops = dd

    target = max(1024, int(context_window * trigger_ratio * 0.92))

    merged = old + recent
    while counter.count_messages(merged) > target and len(old) > _MIN_OLD_SEGMENTS:
        if not _drop_lowest_priority_one(old):
            break
        stats.priority_drops += 1
        merged = old + recent

    return old + recent, stats


def decide_compact_tier(
    util_before: float,
    util_after_light: float,
    trigger_ratio: float,
    auto_ratio: float,
) -> str:
    if util_after_light < trigger_ratio:
        return "light_only"
    if util_before >= auto_ratio or util_after_light >= auto_ratio:
        return "heavy"
    return "medium"


def infer_compact_focus(
    recent_slice: list[dict[str, str]],
    active_skill_scope: str,
) -> str:
    hints: list[str] = []
    if active_skill_scope.strip():
        hints.append(f"Active skill scope: {active_skill_scope.strip()}.")
    err_actions: list[str] = []
    for m in recent_slice[-12:]:
        c = m.get("content", "") or ""
        if not c.startswith("TOOL_RESULT_JSON:"):
            continue
        try:
            d = jr_parse(c.split("\n", 1)[1].strip())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("ok") is False:
            err_actions.append(str(d.get("action", "tool")))
    if err_actions:
        hints.append("Recent failures: " + ", ".join(err_actions[:5]) + ".")
    if hints:
        return " ".join(hints)
    return (
        "Prioritize: tool errors, skill activations, workspace writes, codegen results; "
        "de-emphasize repeated successful reads."
    )


def merge_rolling_summary_local(
    prior: str, digest_snippet: str, max_chars: int = 4000
) -> str:
    snippet = (digest_snippet or "").strip()[:2000]
    if not prior.strip():
        return snippet[:max_chars]
    merged = f"{prior.strip()}\n---\n{snippet}".strip()
    if len(merged) <= max_chars:
        return merged
    return merged[: max_chars - 4] + "\n..."


def build_digest_chunks(
    old_messages: list[dict[str, str]],
    summary_msg_limit: int,
    summary_msg_short_limit: int,
    char_budget: int,
) -> str:
    parts: list[str] = []
    used = 0
    for m in old_messages:
        content = m.get("content", "") or ""
        lim = (
            summary_msg_limit
            if content.startswith("TOOL_RESULT_JSON:")
            else summary_msg_short_limit
        )
        chunk = f"[{m.get('role', 'unknown')}]: {content[:lim]}"
        if used + len(chunk) > char_budget:
            parts.append("... (earlier messages omitted)")
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n---\n".join(parts)


_SCHEMA_BLOCK = """{
  "primary_goal": "The main objective the agent is working toward (one sentence)",
  "current_status": "one of: in_progress, blocked, completed, failed",
  "completed_actions": ["list of successfully completed tool calls"],
  "pending_actions": ["list of actions the agent intended to do next"],
  "key_files_written": ["list of files written to workspace"],
  "active_skill": "name of currently active skill or null",
  "blockers": ["list of blocking issues"],
  "errors_encountered": [{"action": "tool_name", "error": "error message"}]
}"""


def generate_structured_summary_prompt(digest_text: str) -> str:
    return f"""Analyze the conversation and output a structured summary in JSON format.

Required schema:
{_SCHEMA_BLOCK}

Rules:
- Keep completed_actions and pending_actions concise (max 10 items each)
- Only include key files that were actually written
- If status is "blocked", explain why in blockers
- Output ONLY valid JSON, no markdown

Conversation:
{digest_text}"""


def generate_incremental_structured_summary_prompt(
    prior_summary: str, digest_text: str
) -> str:
    return f"""Analyze the conversation and output a structured summary in JSON format.

You are UPDATING a running summary. Merge with the prior summary: do not drop facts that are still relevant; remove contradicted obsolete details.

Required schema:
{_SCHEMA_BLOCK}

Rules:
- Keep completed_actions and pending_actions concise (max 10 items each)
- Output ONLY valid JSON, no markdown

PRIOR_SUMMARY (may be empty):
{prior_summary or "(none)"}

NEW_SEGMENT:
{digest_text}"""


@dataclass
class CompactTelemetry:
    tier: str = ""
    encoding: str = ""
    tokens_before: int = 0
    tokens_after_light: int = 0
    tokens_after: int = 0
    messages_before: int = 0
    messages_after: int = 0
    utilization_before: float = 0.0
    utilization_after: float = 0.0
    dedupe_drops: int = 0
    priority_drops: int = 0
    focus: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def log_line(self, agent_id: int) -> str:
        return (
            f"Agent {agent_id}: compact "
            f"tier={self.tier} enc={self.encoding} "
            f"tok={self.tokens_before}->{self.tokens_after_light}->{self.tokens_after} "
            f"msg={self.messages_before}->{self.messages_after} "
            f"util={self.utilization_before:.1%}->{self.utilization_after:.1%} "
            f"dedupe={self.dedupe_drops} pri_drop={self.priority_drops} "
            f"focus={self.focus[:120]!r}"
        )


class StructuredSummary(BaseModel):
    """結構化摘要（Pydantic 模型）。

    用於驗證 LLM 返回的摘要資料，確保欄位型別正確。
    """

    primary_goal: str = ""
    current_status: Literal["in_progress", "completed", "blocked", "error"] = (
        "in_progress"
    )
    completed_actions: list[str] = []
    pending_actions: list[str] = []
    key_files_written: list[str] = []
    active_skill: Optional[str] = None
    blockers: list[str] = []
    errors_encountered: list[dict[str, str]] = []
    workspace_version: int = 0

    @field_validator("completed_actions", "pending_actions", "blockers")
    @classmethod
    def limit_list_size(cls, v: list[str]) -> list[str]:
        """限制列表最大 10 條。"""
        return v[:10] if len(v) > 10 else v

    @field_validator("errors_encountered")
    @classmethod
    def limit_errors_size(cls, v: list[dict[str, str]]) -> list[dict[str, str]]:
        """限制錯誤列表最大 5 條。"""
        return v[:5] if len(v) > 5 else v

    def to_prompt_content(self) -> str:
        """將結構化摘要轉為可注入上下文的文字。

        若摘要裡沒有可用欄位，返回空字串。

        :return: 可注入文字（可能為空）。
        :rtype: str
        """
        lines = []
        if self.primary_goal:
            lines.append(f"Goal: {self.primary_goal}")
        if self.current_status and self.current_status != "in_progress":
            lines.append(f"Status: {self.current_status}")
        if self.active_skill:
            lines.append(f"Active Skill: {self.active_skill}")
        if self.completed_actions:
            lines.append("Completed:")
            for action in self.completed_actions[-10:]:
                lines.append(f"- {action}")
        if self.pending_actions:
            lines.append("Pending:")
            for action in self.pending_actions[:10]:
                lines.append(f"- {action}")
        if self.blockers:
            lines.append("Blockers:")
            for b in self.blockers[:5]:
                lines.append(f"- {b}")
        if self.errors_encountered:
            lines.append("Errors:")
            for e in self.errors_encountered[-5:]:
                lines.append(
                    f"- {e.get('action', 'unknown')}: {e.get('error', 'unknown')}"
                )
        return "\n".join(lines) if lines else ""


def structured_summary_from_parsed(
    parsed: dict[str, Any],
    workspace_version: int,
) -> StructuredSummary:
    """從已解析的 JSON 構造結構化摘要物件。

    :param parsed: 結構化摘要 JSON 物件。
    :type parsed: dict[str, Any]
    :param workspace_version: workspace 狀態版本。
    :type workspace_version: int
    :return: 構造完成的 :class:`StructuredSummary`。
    :rtype: StructuredSummary
    """
    try:
        return StructuredSummary(
            primary_goal=parsed.get("primary_goal", ""),
            current_status=parsed.get("current_status", "in_progress"),
            completed_actions=parsed.get("completed_actions", []),
            pending_actions=parsed.get("pending_actions", []),
            key_files_written=parsed.get("key_files_written", []),
            active_skill=parsed.get("active_skill"),
            blockers=parsed.get("blockers", []),
            errors_encountered=parsed.get("errors_encountered", []),
            workspace_version=workspace_version,
        )
    except Exception as e:
        logger.warning(f"Failed to validate StructuredSummary: {e}, using defaults")
        return StructuredSummary(workspace_version=workspace_version)


class AgentMemory:
    """持久化記憶（``workspace/AGENT_MEMORY.md``）。

    該記憶以 YAML frontmatter 儲存，用於跨會話儲存關鍵決策、錯誤與當前任務等資訊。
    """

    def __init__(self, workspace_path: Path):
        """初始化 AgentMemory。

        :param workspace_path: agent workspace 根目錄路徑。
        :type workspace_path: pathlib.Path
        """
        self.path = workspace_path / "AGENT_MEMORY.md"
        self._data: dict[str, Any] = {
            "goals": [],
            "decisions": [],
            "patterns": [],
            "errors": [],
            "current_task": "",
            "completed_tasks": [],
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        content = self.path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return
        parts = content.split("---", 2)
        if len(parts) < 3:
            return
        loaded = _yaml.load(StringIO(parts[1]))
        if isinstance(loaded, dict):
            self._data = loaded

    def _save(self) -> None:
        buf = StringIO()
        _yaml.dump(self._data, buf)
        front = buf.getvalue()
        body = "# Agent Memory\n\nSee YAML frontmatter above for structured data.\n"
        self.path.write_text(f"---\n{front}---\n\n{body}", encoding="utf-8")

    def update(self, section: str, content: Any) -> None:
        self._data[section] = content
        self._save()

    def add_decision(self, decision: str) -> None:
        self._data.setdefault("decisions", []).append(
            {"decision": decision, "time": datetime.now(timezone.utc).isoformat()}
        )
        if len(self._data["decisions"]) > 20:
            self._data["decisions"] = self._data["decisions"][-20:]
        self._save()

    def add_error(self, error: dict[str, str]) -> None:
        self._data.setdefault("errors", []).append(
            {**error, "time": datetime.now(timezone.utc).isoformat()}
        )
        if len(self._data["errors"]) > 10:
            self._data["errors"] = self._data["errors"][-10:]
        self._save()

    def set_current_task(self, task: str) -> None:
        self._data["current_task"] = task
        self._save()

    def complete_task(self, task: str) -> None:
        self._data.setdefault("completed_tasks", []).append(
            {"task": task, "time": datetime.now(timezone.utc).isoformat()}
        )
        if self._data.get("current_task") == task:
            self._data["current_task"] = ""
        self._save()

    def to_prompt_context(self) -> str:
        """將記憶轉為可注入上下文的文字。

        :return: 可注入文字（若沒有內容則返回空字串）。
        :rtype: str
        """
        lines = []
        if self._data.get("current_task"):
            lines.append(f"Current Task: {self._data['current_task']}")
        if self._data.get("goals"):
            lines.append("Goals:")
            for g in self._data["goals"][:5]:
                lines.append(f"- {g}")
        if self._data.get("decisions"):
            lines.append("Key Decisions:")
            for d in self._data["decisions"][-5:]:
                lines.append(f"- {d.get('decision', 'unknown')}")
        if self._data.get("errors"):
            lines.append("Known Errors:")
            for e in self._data["errors"][-3:]:
                lines.append(
                    f"- {e.get('action', 'unknown')}: {e.get('error', 'unknown')}"
                )
        return "\n".join(lines) if lines else ""

    def clear(self) -> None:
        """清空當前記憶並寫回磁碟。"""
        self._data = {
            "goals": [],
            "decisions": [],
            "patterns": [],
            "errors": [],
            "current_task": "",
            "completed_tasks": [],
        }
        self._save()


def load_rolling_summary_from_workspace(read_json: Callable[[str, Any], Any]) -> str:
    """從 workspace 讀取滾動摘要。

    :param read_json: workspace 的 JSON 讀取函式簽名（`read_json(path, default)`）。
    :type read_json: Callable[[str, Any], Any]
    :return: 當前滾動摘要字串（可能為空）。
    :rtype: str
    """
    raw = read_json(".runtime/logs/thread_compact_state.json", {})
    if isinstance(raw, dict):
        return str(raw.get("rolling_summary", "") or "")
    return ""


def save_thread_compact_state(
    workspace_write: Callable[[str, str], str],
    *,
    rolling_summary: str,
    tier: str,
    compact_count: int,
) -> None:
    """將壓縮狀態寫回 workspace（thread_compact_state.json）。

    :param workspace_write: workspace 寫入函式簽名（`workspace_write(path, content)`）。
    :type workspace_write: Callable[[str, str], str]
    :param rolling_summary: 更新後的滾動摘要。
    :type rolling_summary: str
    :param tier: 壓縮層級（例如 ``medium``/``heavy``）。
    :type tier: str
    :param compact_count: 已執行壓縮次數累計值。
    :type compact_count: int
    :return: None
    :rtype: None
    """
    workspace_write(
        ".runtime/logs/thread_compact_state.json",
        _jr_dumps(
            {
                "rolling_summary": rolling_summary,
                "last_tier": tier,
                "compact_count": compact_count,
            }
        ),
    )


@dataclass
class ThreadCompactResult:
    messages: list[dict[str, str]]
    rolling_thread_summary: str
    structured_summary: Optional[StructuredSummary]
    last_utilization: float
    compact_count: int
    tier: str = ""


def save_thread_history_before_compact(
    workspace_write: Callable[[str, str], str],
    thread_messages: list[dict[str, str]],
    compact_count: int,
) -> str:
    """壓縮前儲存完整對話歷史到檔案。

    借鑑 Cursor 的做法：壓縮時將完整對話歷史儲存為檔案，
    Agent 可透過搜尋找回關鍵事實，彌補有失真壓縮帶來的資訊丟失。

    :param workspace_write: workspace 寫入函式（簽名：workspace_write(path, content) -> str）。
    :param thread_messages: 當前完整的 thread 訊息列表。
    :param compact_count: 當前壓縮次數（用於檔案命名）。
    :return: 儲存的歷史檔案路徑。
    """
    history_path = f".runtime/logs/thread_history/compact_{compact_count:04d}.jsonl"
    lines = []
    for m in thread_messages:
        lines.append(_jr_dumps(m, indent=None))
    content = "\n".join(lines) + "\n" if lines else ""
    workspace_write(history_path, content)
    logger.debug(
        f"Saved thread history before compact: {history_path} ({len(thread_messages)} messages)"
    )
    return history_path


async def run_thread_compaction(
    thread_messages: list[dict[str, str]],
    *,
    agent_id: int,
    cfg: ContextConfig,
    litellm_model: str,
    tiktoken_encoding: Optional[str],
    focus_instruction: str,
    active_skill_scope: str,
    rolling_thread_summary: str,
    workspace_state_version: int,
    compact_count: int,
    run_summary_llm: Callable[[list[dict[str, str]]], Awaitable[Any]],
    collect_key_state: Callable[[], dict[str, Any]],
    memory_prompt: str,
    workspace_write: Optional[Callable[[str, str], str]] = None,
) -> ThreadCompactResult:
    """執行 thread 分層壓縮並返回緊湊後的 messages。

    該函式不直接讀寫 workspace：呼叫方負責傳入 ``rolling_thread_summary`` 與 ``collect_key_state``，並根據需要把結果持久化。

    :param thread_messages: 當前 thread messages（role/content 結構）。
    :type thread_messages: list[dict[str, str]]
    :param agent_id: Agent ID，用於 telemetry/log。
    :type agent_id: int
    :param cfg: 上下文配置。
    :type cfg: ContextConfig
    :param litellm_model: LiteLLM 路由模型名（用於 token_counter）。
    :type litellm_model: str
    :param tiktoken_encoding: 可選 tiktoken 編碼名覆蓋。
    :type tiktoken_encoding: str | None
    :param focus_instruction: 可選定向壓縮焦點（為空時自動推斷）。
    :type focus_instruction: str
    :param active_skill_scope: 當前啟用 skill 的 scope（用於推斷摘要重點）。
    :type active_skill_scope: str
    :param rolling_thread_summary: 歷史滾動摘要文字。
    :type rolling_thread_summary: str
    :param workspace_state_version: workspace 狀態版本號，寫入結構化摘要。
    :type workspace_state_version: int
    :param compact_count: 壓縮累計次數（用於 KEY_STATE 記錄）。
    :type compact_count: int
    :param run_summary_llm: LLM 執行函式（入參為 summary prompt messages）。
    :type run_summary_llm: Callable[[list[dict[str, str]]], Awaitable[Any]]
    :param collect_key_state: 收集 KEY_STATE_JSON 所需檔案內容的回撥。
    :type collect_key_state: Callable[[], dict[str, Any]]
    :param memory_prompt: 持久化記憶注入用文字（可為空）。
    :type memory_prompt: str
    :param workspace_write: 可選的 workspace 寫入函式，用於儲存壓縮前的對話歷史。
    :type workspace_write: Callable[[str, str], str] | None
    :return: 壓縮後的結果物件。
    :rtype: ThreadCompactResult
    """
    # 壓縮前儲存完整對話歷史（如果提供了 workspace_write）
    if workspace_write is not None and thread_messages:
        save_thread_history_before_compact(
            workspace_write, thread_messages, compact_count
        )

    cw = cfg.model_context_window
    max_chars = cfg.thread_compact_max_chars
    keep_recent = cfg.thread_compact_keep_recent
    counter = ThreadTokenCounter(
        litellm_model=litellm_model,
        encoding_name=tiktoken_encoding,
    )

    need_compact, util_before, status = should_compact(
        thread_messages,
        context_window=cw,
        warning_ratio=cfg.compact_warning_ratio,
        trigger_ratio=cfg.compact_trigger_ratio,
        auto_ratio=cfg.compact_auto_ratio,
        token_counter=counter,
    )
    total_chars = sum(len(m.get("content", "")) for m in thread_messages)
    char_or_len_pressure = (
        total_chars > max_chars or len(thread_messages) > keep_recent + 2
    )

    if not need_compact and not char_or_len_pressure:
        if status == "warning":
            logger.info(
                f"Agent {agent_id}: context at {util_before:.1%} ({counter.encoding_name}), consider compacting soon"
            )
        return ThreadCompactResult(
            messages=thread_messages,
            rolling_thread_summary=rolling_thread_summary,
            structured_summary=None,
            last_utilization=util_before,
            compact_count=compact_count,
            tier="",
        )

    tokens_before = counter.count_messages(thread_messages)
    light_pruned, lp_stats = light_prune_thread_messages(
        thread_messages,
        keep_recent,
        counter,
        cw,
        cfg.compact_trigger_ratio,
    )
    util_after_light = get_context_utilization(light_pruned, cw, counter)
    tokens_after_light = counter.count_messages(light_pruned)

    tier = decide_compact_tier(
        util_before,
        util_after_light,
        cfg.compact_trigger_ratio,
        cfg.compact_auto_ratio,
    )
    if char_or_len_pressure and tier == "light_only":
        tier = "medium"

    new_compact_count = compact_count + 1
    rolling = rolling_thread_summary
    structured_out: Optional[StructuredSummary] = None

    logger.info(
        f"Agent {agent_id}: compact start util={util_before:.1%}->{util_after_light:.1%} "
        f"tier={tier} enc={counter.encoding_name} ({status})"
    )

    recent_messages = light_pruned[-keep_recent:]
    old_messages = light_pruned[:-keep_recent]

    if not old_messages:
        logger.info(
            CompactTelemetry(
                tier="none",
                encoding=counter.encoding_name,
                tokens_before=tokens_before,
                tokens_after_light=tokens_after_light,
                tokens_after=tokens_after_light,
                messages_before=len(thread_messages),
                messages_after=len(light_pruned),
                utilization_before=util_before,
                utilization_after=util_after_light,
                dedupe_drops=lp_stats.dedupe_drops,
                priority_drops=lp_stats.priority_drops,
                extra={"note": "no_old_segment"},
            ).log_line(agent_id)
        )
        return ThreadCompactResult(
            messages=light_pruned,
            rolling_thread_summary=rolling,
            structured_summary=None,
            last_utilization=util_after_light,
            compact_count=new_compact_count,
            tier="none",
        )

    focus = (focus_instruction or "").strip() or infer_compact_focus(
        recent_messages, active_skill_scope
    )
    digest_text = build_digest_chunks(
        old_messages,
        cfg.summary_msg_limit,
        cfg.summary_msg_short_limit,
        cfg.summary_char_budget,
    )
    if focus:
        digest_text = f"FOCUS:\n{focus}\n\n{digest_text}"

    tel = CompactTelemetry(
        tier=tier,
        encoding=counter.encoding_name,
        tokens_before=tokens_before,
        tokens_after_light=tokens_after_light,
        tokens_after=0,
        messages_before=len(thread_messages),
        messages_after=0,
        utilization_before=util_before,
        utilization_after=0.0,
        dedupe_drops=lp_stats.dedupe_drops,
        priority_drops=lp_stats.priority_drops,
        focus=focus,
    )

    if tier == "light_only":
        tel.tokens_after = tokens_after_light
        tel.messages_after = len(light_pruned)
        tel.utilization_after = util_after_light
        logger.info(tel.log_line(agent_id))
        return ThreadCompactResult(
            messages=light_pruned,
            rolling_thread_summary=rolling,
            structured_summary=None,
            last_utilization=util_after_light,
            compact_count=new_compact_count,
            tier=tier,
        )

    summary_text = ""
    assistant_body = ""

    if tier == "heavy":
        rolling = merge_rolling_summary_local(rolling, digest_text, max_chars=4000)
        assistant_body = (
            "ROLLING_SUMMARY:\n" + rolling.strip()[: cfg.summary_char_budget]
        )
        tel.extra["summary_mode"] = "heavy_rolling"
    else:
        prior = rolling.strip()
        prompt_content = (
            generate_incremental_structured_summary_prompt(prior, digest_text)
            if prior
            else generate_structured_summary_prompt(digest_text)
        )
        summary_prompt = [{"role": "user", "content": prompt_content}]

        try:
            response = await run_summary_llm(summary_prompt)
            if response.choices:
                summary_text = (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning(
                f"Agent {agent_id}: LLM compression failed: {e}, rolling fallback"
            )

        if summary_text:
            rolling = summary_text[:8000]
            parsed: Any = None
            try:
                parsed = jr_parse(summary_text)
            except Exception:
                pass
            if isinstance(parsed, dict):
                structured_out = structured_summary_from_parsed(
                    parsed, workspace_state_version
                )
        if structured_out:
            assistant_body = structured_out.to_prompt_content()
            if not assistant_body.strip() and summary_text:
                assistant_body = f"STRUCTURED_SUMMARY_RAW:\n{summary_text[: cfg.summary_char_budget]}"
            tel.extra["summary_mode"] = "structured"
        elif summary_text:
            assistant_body = (
                f"STRUCTURED_SUMMARY_RAW:\n{summary_text[: cfg.summary_char_budget]}"
            )
            tel.extra["summary_mode"] = "raw_json"
        else:
            rolling = merge_rolling_summary_local(rolling, digest_text, max_chars=4000)
            assistant_body = (
                "ROLLING_SUMMARY_FALLBACK:\n" + rolling[: cfg.summary_char_budget]
            )
            tel.extra["summary_mode"] = "rolling_fallback"

    key_state = collect_key_state()
    compacted: list[dict[str, str]] = []
    if assistant_body.strip():
        compacted.append({"role": "assistant", "content": assistant_body.strip()})

    if key_state:
        compacted.append(
            {
                "role": "user",
                "content": "KEY_STATE_JSON:\n"
                + _jr_dumps(
                    {
                        "workspace_state_version": workspace_state_version,
                        "compact_count": new_compact_count,
                        "compact_tier": tier,
                        "files": key_state,
                    },
                    indent=None,
                ),
            }
        )

    if memory_prompt.strip():
        compacted.append({"role": "user", "content": memory_prompt.strip()})

    compacted.extend(recent_messages)
    tel.tokens_after = counter.count_messages(compacted)
    tel.messages_after = len(compacted)
    tel.utilization_after = get_context_utilization(compacted, cw, counter)
    logger.info(tel.log_line(agent_id))

    return ThreadCompactResult(
        messages=compacted,
        rolling_thread_summary=rolling,
        structured_summary=structured_out,
        last_utilization=tel.utilization_after,
        compact_count=new_compact_count,
        tier=tier,
    )
