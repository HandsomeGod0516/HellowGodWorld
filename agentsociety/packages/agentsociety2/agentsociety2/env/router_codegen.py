"""
Code Generation Router Implementation
透過程式碼生成的方式呼叫環境模組中的@tool標記的介面

架構說明:
- AskContext: 一次ask呼叫所需的所有狀態
- 觀察者模式: 流程結束後統一將 context 交給所有 observer 記錄
- 管道-過濾器: 整體流程透過 PipelineStage 串聯
- 責任鏈: Code 獲取透過 CodeProvider 鏈實現 (PredefinedCode -> Cache -> LLM)
- CodeStage: 單階段完成程式碼獲取 + 驗證 + 執行
"""

import ast
import asyncio
import inspect
import json
import math
import os
import pickle
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Tuple

if TYPE_CHECKING:
    from agentsociety2.storage import ReplayWriter

import faiss
import numpy as np
from agentsociety2.config import Config
from agentsociety2.env.base import EnvBase
from agentsociety2.env.benchmark import (
    EnvRouterBenchmarkData,
)
from agentsociety2.env.router_base import RouterBase
from agentsociety2.logger import get_logger
from litellm import AllMessageValues, aembedding

__all__ = ["CodeGenRouter", "AskContext"]


@dataclass
class CacheStats:
    """快取統計資訊"""
    request_count: int = 0  # 總請求次數
    predefined_hit_count: int = 0  # 預定義指令命中次數（observe/statistics）
    cache_hit_count: int = 0  # 快取命中次數
    cache_miss_count: int = 0  # 快取未命中次數
    total_input_tokens: int = 0  # 總輸入token數
    total_output_tokens: int = 0  # 總輸出token數
    code_execution_success_count: int = 0  # 程式碼執行成功次數
    code_execution_failure_count: int = 0  # 程式碼執行失敗次數
    total_code_retry_count: int = 0  # 總程式碼重試次數

    @property
    def cache_hit_rate(self) -> float:
        """快取命中率"""
        total = self.cache_hit_count + self.cache_miss_count
        return self.cache_hit_count / total if total > 0 else 0.0

    @property
    def code_execution_success_rate(self) -> float:
        """程式碼執行成功率"""
        total = self.code_execution_success_count + self.code_execution_failure_count
        return self.code_execution_success_count / total if total > 0 else 0.0

    @property
    def avg_input_tokens(self) -> float:
        """平均輸入token數"""
        return self.total_input_tokens / self.request_count if self.request_count > 0 else 0.0

    @property
    def avg_output_tokens(self) -> float:
        """平均輸出token數"""
        return self.total_output_tokens / self.request_count if self.request_count > 0 else 0.0

    @property
    def avg_retry_count(self) -> float:
        """平均程式碼重試次數"""
        return self.total_code_retry_count / self.request_count if self.request_count > 0 else 0.0


@dataclass
class CacheEntry:
    """快取條目"""
    instruction_template: str  # 模板指令
    variable_keys: tuple[str, ...]  # 變數鍵的元組（用於子集檢查）
    variable_types: dict[str, str]  # 變數型別字典 {key: type_name}
    code: str  # 生成的程式碼
    embedding: Optional[np.ndarray] = None  # 指令的embedding（用於相似度計算）
    env_class_type: str = ""  # 接入的env module classes指紋，僅一致時才能使用
    entry_id: Optional[int] = None  # 資料庫中的條目ID（持久化時使用）
    success_count: int = 0  # 成功執行次數
    failure_count: int = 0  # 失敗執行次數
    last_used: datetime = field(default_factory=datetime.now)  # 最後使用時間
    created_at: datetime = field(default_factory=datetime.now)  # 建立時間

    @property
    def total_usage(self) -> int:
        """總使用次數"""
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        """成功率"""
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0


OBSERVE_INSTRUCTION = (
    "Builtin observe has collected all readonly kind='observe' tools into results['observations']. "
    "Summarize the situational information for the agent in clear natural language."
)
STATISTICS_INSTRUCTION = "Collect environment statistics by calling all available statistics tools. Store all statistics results in results['statistics']."


@dataclass
class AskContext:
    """
    一次ask呼叫所需的完整狀態，在管道各階段之間傳遞。
    """
    # === 輸入 ===
    ctx: dict
    instruction: str
    readonly: bool
    template_mode: bool

    # === 預處理後 ===
    variables: dict = field(default_factory=dict)
    instruction_stripped: str = ""
    is_observe_or_statistics: bool = False
    resolved_instruction: str = ""  # <observe>/<statistics> 解析後的實際指令

    # === Code 獲取 (責任鏈) ===
    code: Optional[str] = None
    cache_entry: Optional["CacheEntry"] = None
    cache_miss_reason: Optional[str] = None
    code_source: Optional[str] = None  # "predefined" | "cache" | "llm" | "builtin"

    # === LLM 重試狀態 ===
    retry_count: int = 0
    previous_code: Optional[str] = None
    previous_errors: List[str] = field(default_factory=list)
    dialog_history: List[AllMessageValues] = field(default_factory=list)

    # === 執行結果 ===
    execution_result: Optional[Dict[str, Any]] = None
    execution_attempted: bool = False  # 是否嘗試過執行程式碼（供 observer 統計）
    success_data: Optional[Dict[str, Any]] = None  # 成功時的 {ctx, instruction, results, process_text, status, error, code}

    # === 輸出 ===
    final_answer: str = ""
    results: dict = field(default_factory=dict)
    early_return: Optional[Tuple[dict, str]] = None  # 非 None 表示提前返回，不再繼續管道
    token_usage_responses: List[Dict[str, int]] = field(default_factory=list)  # 每次 LLM 呼叫的 token 使用，供 observer 統計

    def __post_init__(self):
        self.instruction_stripped = self.instruction.strip()
        self.is_observe_or_statistics = self.instruction_stripped in ("<observe>", "<statistics>")
        self.variables = self.ctx.get("variables", {})
        self.resolved_instruction = self.instruction
        if self.instruction_stripped == "<observe>":
            self.resolved_instruction = OBSERVE_INSTRUCTION
        elif self.instruction_stripped == "<statistics>":
            self.resolved_instruction = STATISTICS_INSTRUCTION


def _get_debug_info(description: str = "") -> str:
    """獲取當前檔案、行號和階段描述的除錯資訊"""
    frame = inspect.currentframe()
    if frame and frame.f_back:
        caller_frame = frame.f_back
        filename = os.path.basename(caller_frame.f_code.co_filename)
        lineno = caller_frame.f_lineno
        return f"[{filename}:{lineno}] {description}"
    return description


async def clean_coroutines_from_results(results: dict) -> dict:
    """
    遞迴檢查 results 中的未 await 的 coroutine 並 await 獲取其值。
    供 CodeStage 和 InstructionLogObserver 使用。
    """
    visited: set[int] = set()

    async def clean_value(value: Any) -> Any:
        if inspect.iscoroutine(value):
            try:
                return await value
            except Exception as e:
                get_logger().warning(f"Failed to await coroutine: {str(e)}")
                return f"<unawaited_coroutine: {type(value).__name__}>"
        elif isinstance(value, dict):
            obj_id = id(value)
            if obj_id in visited:
                return "<circular_reference>"
            visited.add(obj_id)
            try:
                return {k: await clean_value(v) for k, v in value.items()}
            finally:
                visited.remove(obj_id)
        elif isinstance(value, (list, tuple)):
            obj_id = id(value)
            if obj_id in visited:
                return "<circular_reference>"
            visited.add(obj_id)
            try:
                cleaned = [await clean_value(item) for item in value]
                return cleaned if isinstance(value, list) else tuple(cleaned)
            finally:
                visited.remove(obj_id)
        return value

    return await clean_value(results)


def _get_env_class_type_key(env_modules: list) -> str:
    """
    根據 env module classes 生成可讀的標識字串。
    相同 env 配置（模組型別和名稱）產生相同 key，用於快取隔離。
    返回 human-readable 的 JSON，便於直接檢視快取內容。
    """
    keys = []
    for m in env_modules:
        cls = m.__class__
        full_name = f"{cls.__module__}.{cls.__qualname__}"
        keys.append((m.name, full_name))
    return json.dumps(sorted(keys), sort_keys=True, ensure_ascii=False)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            stripped = "\n".join(lines[1:-1]).strip()
    return stripped


def _compact_results_text(results: Dict[str, Any]) -> str:
    try:
        return json.dumps(results, ensure_ascii=False, separators=(",", ":"), default=str)
    except TypeError:
        return str(results)


def _build_deterministic_final_answer(router: "CodeGenRouter", success_data: Dict[str, Any]) -> str:
    time_tag = f"[{router.t.strftime('%A')}, {router.t.strftime('%Y-%m-%d %H:%M:%S')}]"
    status = success_data.get("status", "unknown")
    results = success_data.get("results", {})
    reason = results.get("reason") or success_data.get("error")
    process_text = _strip_code_fence(success_data.get("process_text", ""))

    if status in {"fail", "error"} and reason:
        body = str(reason).strip()
    elif process_text and process_text != "無輸出":
        body = process_text
    elif reason:
        body = str(reason).strip()
    else:
        body = _compact_results_text(results)

    return f"{time_tag} {body}" if body else time_tag


class TemplateCacheDB:
    """
    持久化模板快取，支援跨執行共用。
    使用 pickle 檔案儲存，FAISS 進行 embedding 相似度搜尋。
    快取按 env_class_type 隔離，僅 env 型別一致時才能命中。
    """

    def __init__(
        self,
        cache_path: str,
        embedding_dims: int,
        max_size_per_env: int = 1000,
    ):
        self._cache_path = cache_path
        self._embedding_dims = embedding_dims
        self._max_size_per_env = max_size_per_env
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    def _load_data(self) -> dict:
        """從 pickle 檔案載入資料。"""
        if not os.path.exists(self._cache_path):
            return {"next_id": 1, "by_env": {}}
        try:
            with open(self._cache_path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            get_logger().warning(f"Failed to load cache from {self._cache_path}: {e}")
            return {"next_id": 1, "by_env": {}}

    def _save_data(self, data: dict) -> None:
        """儲存資料到 pickle 檔案。"""
        with open(self._cache_path, "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)

    def load_entries(self, env_class_type: str) -> Tuple[List[CacheEntry], Optional["faiss.Index"], List[int]]:
        """
        載入指定 env_class_type 的快取條目並構建 FAISS 索引。
        Returns:
            (entries, faiss_index, faiss_entry_indices)
        """
        data = self._load_data()
        raw = data.get("by_env", {}).get(env_class_type, [])
        raw = sorted(raw, key=lambda e: e.last_used, reverse=True)[: self._max_size_per_env]

        entries: List[CacheEntry] = []
        embeddings: List[np.ndarray] = []
        entry_indices: List[int] = []

        for i, e in enumerate(raw):
            e.env_class_type = env_class_type
            entries.append(e)
            if e.embedding is not None:
                embeddings.append(e.embedding)
                entry_indices.append(i)

        if not embeddings:
            return entries, None, []

        emb_array = np.asarray(embeddings, dtype=np.float32).copy()
        faiss.normalize_L2(emb_array)
        index = faiss.IndexFlatIP(len(embeddings[0]))
        index.add(emb_array)
        return entries, index, entry_indices

    def add_entry(
        self,
        env_class_type: str,
        entry: CacheEntry,
    ) -> int:
        """新增快取條目並持久化。Returns 新條目的 id。"""
        data = self._load_data()
        next_id = data.get("next_id", 1)
        by_env = data.setdefault("by_env", {})
        lst = by_env.setdefault(env_class_type, [])

        entry.entry_id = next_id
        entry.env_class_type = env_class_type
        lst.append(entry)
        lst.sort(key=lambda e: e.last_used, reverse=True)
        by_env[env_class_type] = lst[: self._max_size_per_env]

        data["next_id"] = next_id + 1
        self._save_data(data)
        return next_id

    def update_entry(
        self,
        env_class_type: str,
        entry_id: int,
        success: bool,
        code: str,
    ) -> None:
        """更新已有條目的統計和程式碼。"""
        data = self._load_data()
        lst = data.get("by_env", {}).get(env_class_type, [])
        for e in lst:
            if e.entry_id == entry_id:
                e.last_used = datetime.now()
                e.code = code
                if success:
                    e.success_count += 1
                else:
                    e.failure_count += 1
                break
        self._save_data(data)

    def clear_env_cache(self, env_class_type: str) -> None:
        """清空指定 env_class_type 的所有快取條目。"""
        data = self._load_data()
        data.setdefault("by_env", {}).pop(env_class_type, None)
        self._save_data(data)

    def find_by_instruction(
        self,
        env_class_type: str,
        instruction_template: str,
    ) -> Optional[CacheEntry]:
        """按 instruction_template 查詢已有條目（用於更新而非新增）。"""
        data = self._load_data()
        lst = data.get("by_env", {}).get(env_class_type, [])
        for e in lst:
            if e.instruction_template == instruction_template:
                return e
        return None


# ==================== 觀察者模式 ====================
# 所有 observer 僅在流程結束時被呼叫一次，根據完整 context 記錄必要內容


class AskObserver(Protocol):
    """Ask流程觀察者協議：流程結束後接收最終 context"""

    async def on_final(self, context: AskContext) -> None:
        ...


# ==================== 責任鏈：Code 獲取 ====================


class CodeProvider(Protocol):
    """Code 獲取責任鏈節點協議，Predefined 最優先"""

    @property
    def name(self) -> str:
        """提供者名稱，用於 context.code_source"""
        ...

    async def get_code(self, context: AskContext, router: "CodeGenRouter") -> Optional[str]:
        """返回程式碼則鏈終止，返回 None 則傳遞至下一節點"""
        ...


# ==================== 管道-過濾器 ====================


class PipelineStage(Protocol):
    """管道階段協議，接收 AskContext，返回可能被修改的 AskContext"""

    async def process(self, context: AskContext, router: "CodeGenRouter") -> AskContext:
        ...


# --- 觀察者具體實現：統一在 on_final 中根據 context 記錄 ---


class InstructionLogObserver:
    """記錄 instruction 到 instruction_log"""

    def __init__(self, router: "CodeGenRouter"):
        self._router = router

    async def on_final(self, context: AskContext) -> None:
        # 執行階段已清理 ctx；未執行時（如 early_return）需清理以便 pickle
        ctx = context.ctx if context.execution_attempted else await clean_coroutines_from_results(context.ctx)
        log_entry = EnvRouterBenchmarkData(
            instruction=context.instruction,
            context=ctx,
            readonly=context.readonly,
        )
        async with self._router._instruction_log_lock:
            self._router._instruction_log.append(log_entry)
            try:
                with open(self._router._log_path, "wb") as f:
                    pickle.dump(self._router._instruction_log, f)
            except Exception as e:
                get_logger().warning(f"Failed to pickle instruction log: {str(e)}, skipping file write")


class CacheStatsObserver:
    """根據最終 context 更新 CacheStats 統計"""

    def __init__(self, router: "CodeGenRouter"):
        self._router = router

    async def on_final(self, context: AskContext) -> None:
        async with self._router._cache_stats_lock:
            self._router._cache_stats.request_count += 1
            if context.code_source in ("predefined", "builtin"):
                self._router._cache_stats.predefined_hit_count += 1
            elif context.cache_entry:
                self._router._cache_stats.cache_hit_count += 1
            elif context.template_mode and not context.is_observe_or_statistics:
                self._router._cache_stats.cache_miss_count += 1
            if context.execution_attempted:
                if context.success_data:
                    self._router._cache_stats.code_execution_success_count += 1
                else:
                    self._router._cache_stats.code_execution_failure_count += 1
                self._router._cache_stats.total_code_retry_count += context.retry_count
            for tu in context.token_usage_responses:
                self._router._cache_stats.total_input_tokens += tu.get("input_tokens", 0)
                self._router._cache_stats.total_output_tokens += tu.get("output_tokens", 0)


class CacheAddObserver:
    """執行成功後根據條件寫入快取"""

    def __init__(self, router: "CodeGenRouter"):
        self._router = router

    @staticmethod
    async def _add_to_cache(router: "CodeGenRouter", instruction: str, variables: dict, code: str, success: bool = True) -> None:
        if not router._template_cache_enabled:
            return
        async with router._template_cache_lock:
            variable_keys = tuple(sorted(variables.keys()))
            variable_types = {k: type(v).__name__ for k, v in variables.items()}
            embedding = await CacheCodeProvider._compute_embedding(router, instruction)
            existing = router._cache_db.find_by_instruction(router._env_class_type_key, instruction)
            if existing and existing.entry_id is not None:
                router._cache_db.update_entry(router._env_class_type_key, existing.entry_id, success, code)
                for e in router._cache_entries:
                    if e.instruction_template == instruction:
                        e.last_used = datetime.now()
                        e.code = code
                        if success:
                            e.success_count += 1
                        else:
                            e.failure_count += 1
                        break
                return
            entry = CacheEntry(
                instruction_template=instruction, variable_keys=variable_keys, variable_types=variable_types,
                code=code, embedding=embedding, env_class_type=router._env_class_type_key,
                success_count=1 if success else 0, failure_count=0 if success else 1,
            )
            new_id = router._cache_db.add_entry(router._env_class_type_key, entry)
            entry.entry_id = new_id
            idx = len(router._cache_entries)
            router._cache_entries.append(entry)
            if embedding is not None:
                router._cache_faiss_entry_indices.append(idx)
                emb = embedding.astype(np.float32).reshape(1, -1).copy()
                faiss.normalize_L2(emb)
                if router._cache_faiss_index is None:
                    router._cache_faiss_index = faiss.IndexFlatIP(emb.shape[1])
                assert router._cache_faiss_index is not None
                router._cache_faiss_index.add(emb)

    async def on_final(self, context: AskContext) -> None:
        if context.success_data is None:
            return
        get_logger().info(f"Try to cache: {context.instruction[:100]}...")
        get_logger().info(f"context.template_mode: {context.template_mode}")
        get_logger().info(f"context.cache_entry: {context.cache_entry}")
        get_logger().info(f"context.is_observe_or_statistics: {context.is_observe_or_statistics}")
        sd = context.success_data
        if context.template_mode and not context.cache_entry and not context.is_observe_or_statistics:
            get_logger().info(f"Adding to cache: {context.instruction[:100]}...")
            await self._add_to_cache(self._router, context.instruction, context.variables, sd["code"], success=True)


# --- Code Provider 責任鏈具體實現 ---


class PredefinedCodeProvider:
    """預定義程式碼提供者，優先順序最高（<observe> / <statistics>）"""

    @property
    def name(self) -> str:
        return "predefined"

    async def get_code(self, context: AskContext, router: "CodeGenRouter") -> Optional[str]:
        if not context.is_observe_or_statistics:
            return None
        if context.instruction_stripped == "<statistics>":
            return router._statistics_code if router._statistics_code else None
        return None


class CacheCodeProvider:
    """模板快取提供者，優先順序第二"""

    @property
    def name(self) -> str:
        return "cache"

    @staticmethod
    async def _compute_embedding(router: "CodeGenRouter", text: str) -> Optional[np.ndarray]:
        try:
            async with router._embedding_cache_lock:
                if text in router._embedding_cache:
                    return router._embedding_cache[text]
            response = await aembedding(
                model=f"openai/{router._embedding_model}", input=[text],
                api_key=router._embedding_api_key, api_base=router._embedding_api_base,
            )
            if response and hasattr(response, "data") and len(response.data) > 0:
                emb = np.array(response.data[0]["embedding"], dtype=np.float32)
                async with router._embedding_cache_lock:
                    if len(router._embedding_cache) < 10000:
                        router._embedding_cache[text] = emb
                return emb
            return None
        except Exception as e:
            get_logger().warning(f"Failed to compute embedding: {e}")
            return None

    @staticmethod
    async def _lookup(
        router: "CodeGenRouter", instruction: str, variables: dict
    ) -> Tuple[Optional[CacheEntry], Optional[str]]:
        if not router._template_cache_enabled:
            return None, "template_cache_disabled"
        async with router._template_cache_lock:
            emb = await CacheCodeProvider._compute_embedding(router, instruction)
            if emb is None:
                return None, "embedding_unavailable"
            current_keys = set(variables.keys())
            best_match, best_sim = None, 0.0
            saw_compatible_candidate = False
            saw_key_incompatible_candidate = False
            if router._cache_faiss_index and router._cache_faiss_entry_indices:
                query = np.asarray([emb], dtype=np.float32).copy()
                faiss.normalize_L2(query)
                k = min(32, router._cache_faiss_index.ntotal)
                scores, indices = router._cache_faiss_index.search(query, k)
                for score, idx in zip(scores[0], indices[0]):
                    if idx < 0:
                        break
                    entry_idx = router._cache_faiss_entry_indices[idx]
                    entry = router._cache_entries[entry_idx]
                    if entry.env_class_type != router._env_class_type_key:
                        continue
                    cached_keys = set(entry.variable_keys)
                    if not (current_keys.issubset(cached_keys) or cached_keys.issubset(current_keys)):
                        saw_key_incompatible_candidate = True
                        continue
                    saw_compatible_candidate = True
                    sim = float(score)
                    if sim >= router._template_cache_similarity_threshold and sim > best_sim:
                        best_sim, best_match = sim, entry
            if best_match:
                best_match.last_used = datetime.now()
                return best_match, None
            if saw_compatible_candidate:
                return None, "below_similarity_threshold"
            if saw_key_incompatible_candidate:
                return None, "variable_keys_incompatible"
            return None, "no_similar_entry"

    async def get_code(self, context: AskContext, router: "CodeGenRouter") -> Optional[str]:
        if not router._template_cache_enabled or not context.template_mode or context.is_observe_or_statistics:
            return None
        cache_entry, miss_reason = await self._lookup(router, context.instruction, context.variables)
        if cache_entry is not None:
            context.cache_entry = cache_entry
            return cache_entry.code
        context.cache_miss_reason = miss_reason
        get_logger().info(
            "Template cache miss: reason=%s instruction=%s",
            miss_reason,
            context.instruction[:100],
        )
        return None


class LLMCodegenProvider:
    """LLM 程式碼生成提供者，責任鏈最後一環，負責呼叫 LLM 生成程式碼"""

    @property
    def name(self) -> str:
        return "llm"

    @staticmethod
    def _build_prompt(router: "CodeGenRouter", instruction: str, ctx: dict, readonly: bool, kind: str | None = None) -> str:
        key = (readonly, kind)
        tools_pyi = router._tools_pyi_dict[key]
        template_note = ""
        if isinstance(ctx.get("variables"), dict) and ctx["variables"]:
            template_note = (
                "\n## Template Mode Hint\n"
                "- Runtime values are available in ctx['variables'].\n"
                "- Prefer reading changing values from ctx['variables'] instead of hard-coding literals.\n"
                "- This keeps the instruction reusable and improves router cache hits.\n"
            )
        return f"""# Code Generation Task
You are a code generation assistant. Your task is to generate Python code that calls environment module tools based on the given agent input.

## Available Environment Modules and Tools
```python
{tools_pyi}
```
```python
modules = {repr(router._modules)}
```

## Agent Input
<instruction>{instruction}</instruction>
```python
ctx = {repr(ctx)}
```
{template_note}

## Code Generation Requirements
1. Generate Python code that accomplishes the instruction by calling appropriate tools.
2. Store results in the `results` dictionary and MUST set results['status'] at the end: 'success', 'in_progress', 'fail', or 'error'.
3. Provide semantic print statements to explain what you're doing.

## Important Notes
- Use `ctx`, `modules`, `results`, `print()`. Allowed modules: collections, itertools, functools, operator, copy, decimal, fractions, statistics, string, re, datetime, json, math, random, numpy (as np).
- Do NOT use dangerous operations. ALWAYS USE `await` TO CALL TOOLS (ASYNC FUNCTIONS).
- NEVER forget to set results['status'] at the END of your code!

## CRITICAL: Status Handling
When calling environment tools:
- Check the return value for a 'status' field
- If the tool returns successfully, set results['status'] = 'success'
- If the tool indicates an error or failure, set results['status'] = 'fail'
- If the operation is ongoing, set results['status'] = 'in_progress'
- Example code pattern:
  response = await modules["ModuleName"].some_tool(arg1, arg2)
  print("Tool response:", response)
  results["response"] = response
  results["status"] = response.get("status", "success") if isinstance(response, dict) else "success"

## Output Format
Generate ONLY the Python code, without markdown. Start directly with Python statements.

Your generated code:"""

    @staticmethod
    def _build_error_message(previous_errors: List[str]) -> str:
        errors_text = "\n".join(f"- {i+1}. {e}" for i, e in enumerate(previous_errors))
        return f"""The code I generated failed during execution. Here's what went wrong:

## Errors
{errors_text}

Please analyze the errors and fix the code. Common issues: incorrect function/module names, wrong parameter types, async/await usage, type mismatches, missing imports, logic errors.

Please generate the corrected code:"""

    @staticmethod
    async def _call_llm(router: "CodeGenRouter", context: AskContext) -> Tuple[str, Optional[Dict[str, int]]]:
        try:
            response = await router.acompletion_with_system_prompt(model="coder", messages=context.dialog_history)
            raw = response.choices[0].message.content or ""  # type: ignore[union-attr]
            pattern = r"```(?:python)?\s*\n(.*?)```"
            matches = re.findall(pattern, raw, re.DOTALL)
            code = matches[0].strip() if matches else raw.strip()
            usage = getattr(response, "usage", None)
            token_usage = None
            if usage is not None:
                token_usage = {"input_tokens": getattr(usage, "prompt_tokens", 0), "output_tokens": getattr(usage, "completion_tokens", 0)}
            return code, token_usage
        except Exception as e:
            get_logger().error(f"{_get_debug_info('LLM程式碼生成失敗')} - {e}")
            return "", None

    async def get_code(self, context: AskContext, router: "CodeGenRouter") -> Optional[str]:
        if context.retry_count == 0:
            prompt = self._build_prompt(router, context.resolved_instruction, context.ctx, context.readonly, None)
            context.dialog_history = [{"role": "user", "content": prompt}]
        else:
            if context.previous_code:
                context.dialog_history.append({"role": "assistant", "content": context.previous_code})
            context.dialog_history.append({"role": "user", "content": self._build_error_message(context.previous_errors)})
        code, token_usage = await self._call_llm(router, context)
        if token_usage:
            context.token_usage_responses.append(token_usage)
        return code.strip() if code else None


# --- Pipeline 階段具體實現 ---


class InitStage:
    """初始化：新增時間到 ctx，檢查 env_modules"""

    async def process(self, context: AskContext, router: "CodeGenRouter") -> AskContext:
        router._add_current_time_to_ctx(context.ctx)
        if not router.env_modules:
            context.early_return = (context.ctx, "No environment modules available to handle the request.")
        return context


class CodeStage:
    """
    程式碼獲取（責任鏈）+ 驗證 + 執行，含重試迴圈。
    Predefined -> Cache -> LLM；驗證或執行失敗時透過 LLM 重試。
    """

    @staticmethod
    async def _acquire_code(router: "CodeGenRouter", context: AskContext, retry_only: bool) -> bool:
        providers = router._code_provider_chain[-1:] if retry_only else router._code_provider_chain
        for provider in providers:
            code = await provider.get_code(context, router)
            if code is not None:
                context.code = code
                context.code_source = provider.name
                return True
        return False

    @staticmethod
    def _validate_code_safety(router: "CodeGenRouter", code: str) -> Tuple[bool, str]:
        violations = []
        try:
            tree = ast.parse(code, mode="exec")
            dangerous_functions = {"eval", "exec", "compile", "__import__", "open", "input"}

            def is_dangerous_module(name: str) -> bool:
                if name in router.ALLOWED_MODULES:
                    return False
                return name in router.DANGEROUS_MODULES or (name.startswith("_") and name != "__future__")

            for node in ast.walk(tree):
                if type(node) in router.FORBIDDEN_AST_NODES:
                    violations.append(f"Forbidden AST node type: {type(node).__name__}")
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in dangerous_functions:
                        violations.append(f"Dangerous function call: {node.func.id}()")
                    elif isinstance(node.func, ast.Attribute) and node.func.attr in {"eval", "exec", "compile"}:
                        violations.append(f"Dangerous method call: {node.func.attr}()")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if is_dangerous_module(alias.name):
                            violations.append(f"Dangerous import: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module and is_dangerous_module(node.module):
                    violations.append(f"Dangerous import: from {node.module} import ...")

            if violations:
                return False, "Code safety check failed. Violations found:\n" + "\n".join(f"- {v}" for v in violations)
            return True, ""
        except SyntaxError as e:
            return False, f"Code syntax error: {str(e)}"
        except Exception as e:
            return False, f"Code validation error: {str(e)}"

    @staticmethod
    async def _execute_code(router: "CodeGenRouter", code: str, ctx: dict, readonly: bool) -> Dict[str, Any]:
        results = {}

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name not in router.ALLOWED_MODULES:
                return None
            return __import__(name, globals, locals, fromlist, level)

        import collections
        import copy
        import decimal
        import fractions
        import functools
        import itertools
        import operator
        import statistics
        import string
        allowed_modules = {
            "collections": collections, "itertools": itertools, "functools": functools, "operator": operator,
            "copy": copy, "decimal": decimal, "fractions": fractions, "statistics": statistics, "string": string,
            "re": re, "datetime": datetime, "json": json, "math": math, "random": random, "numpy": np, "np": np,
        }
        restricted_builtins = {k: v for k, v in __builtins__.items() if k in router.ALLOWED_BUILTINS}
        restricted_builtins["__import__"] = safe_import
        exec_globals = {
            "__builtins__": restricted_builtins, "ctx": ctx, "modules": router._modules, "results": results,
            "print": print, **allowed_modules,
            "Exception": Exception, "RuntimeError": RuntimeError, "ValueError": ValueError, "TypeError": TypeError,
            "SyntaxError": SyntaxError, "NameError": NameError, "AttributeError": AttributeError,
            "IndexError": IndexError, "KeyError": KeyError,
        }
        exec_locals = {}
        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()
        try:
            is_async = "async" in code or "await" in code
            async def run():
                if is_async:
                    indented = "\n".join("    " + line if line.strip() else "" for line in code.split("\n"))
                    async_code = f"async def _generated_main():\n{indented}"
                    exec(compile(async_code, "<generated_async>", "exec"), exec_globals, exec_locals)
                    await exec_locals["_generated_main"]()
                else:
                    exec(compile(code, "<generated>", "exec"), exec_globals, exec_locals)
            await asyncio.wait_for(run(), timeout=10)
            output = captured_output.getvalue()
            print_outputs = [line.strip() for line in output.split("\n") if line.strip()]
            return {"results": results, "output": output, "print_outputs": print_outputs, "success": True}
        except asyncio.TimeoutError:
            raise TimeoutError("Code execution timeout: exceeded 10 seconds limit")
        except Exception as e:
            return {
                "results": results, "output": captured_output.getvalue(), "print_outputs": [],
                "error": str(e), "success": False,
            }
        finally:
            sys.stdout = old_stdout

    async def process(self, context: AskContext, router: "CodeGenRouter") -> AskContext:
        if context.early_return:
            return context
        if context.instruction_stripped == "<observe>":
            context.execution_attempted = True
            context.code_source = "builtin"
            try:
                async with router._execute_lock:
                    execution_result = await router._run_builtin_observe(context.ctx)
            except Exception as e:
                execution_result = {
                    "success": False,
                    "error": str(e),
                    "results": {},
                    "print_outputs": [],
                    "output": "",
                }
            context.execution_result = execution_result
            context.ctx = await clean_coroutines_from_results(context.ctx)
            if execution_result.get("results"):
                execution_result["results"] = await clean_coroutines_from_results(
                    execution_result["results"]
                )
            if not execution_result.get("success", False):
                err = execution_result.get("error", "observe failed")
                context.early_return = (context.ctx, err)
                return context
            results = execution_result.get("results", {})
            print_outputs = execution_result.get("print_outputs", [])
            proc_err = execution_result.get("error", "")
            if print_outputs:
                process_text = "\n".join(print_outputs)
            else:
                process_text = json.dumps(results, ensure_ascii=False, default=str)[:8000]
            if proc_err:
                process_text += f"\n\nError: {proc_err}"
            process_text = f"```\n{process_text}\n```"
            context.success_data = {
                "ctx": context.ctx,
                "instruction": context.resolved_instruction,
                "results": results,
                "process_text": process_text,
                "status": results.get("status", "unknown"),
                "error": proc_err,
                "code": "<builtin observe>",
            }
            context.results = results
            return context
        max_retries = router.max_llm_call_retry if context.code is None else 0
        while context.retry_count <= max_retries:
            if context.code is None:
                ok = await self._acquire_code(router, context, retry_only=bool(context.previous_errors))
                if not ok:
                    context.retry_count += 1
                    context.previous_errors.append("Failed to generate code from LLM.")
                    context.previous_code = None
                    if context.retry_count > max_retries:
                        context.early_return = ({}, "Failed to generate code after retries.")
                        return context
                    continue
                if context.code_source == "llm":
                    context.dialog_history.append({"role": "assistant", "content": context.code})

                assert context.code is not None  # set by _acquire_code when ok=True
                is_safe, safety_violation = self._validate_code_safety(router, context.code)
                if not is_safe:
                    context.retry_count += 1
                    context.previous_code = context.code
                    context.previous_errors.append(safety_violation)
                    if context.retry_count > max_retries:
                        context.early_return = ({}, f"Generated code failed safety check after retries: {safety_violation}")
                        return context
                    context.code = None
                    continue

            code = context.code
            if code is None:
                return context
            context.execution_attempted = True
            try:
                async with router._execute_lock:
                    execution_result = await self._execute_code(router, code, context.ctx, context.readonly)
            except Exception as e:
                execution_result = {"success": False, "error": str(e), "results": {}, "print_outputs": [], "output": ""}

            context.execution_result = execution_result
            # 程式碼執行後必須清理 ctx 和 results 中的 coroutine，以便序列化
            context.ctx = await clean_coroutines_from_results(context.ctx)
            if execution_result.get("results"):
                execution_result["results"] = await clean_coroutines_from_results(execution_result["results"])
            if not execution_result.get("success", False):
                error = execution_result.get("error", "Unknown error")
                context.retry_count += 1
                context.previous_code = code
                context.previous_errors.append(error)
                if context.retry_count > max_retries:
                    context.early_return = (context.ctx, f"Code execution failed after retries: {error}")
                    return context
                context.code = None
                continue

            print_outputs = execution_result.get("print_outputs", [])
            results = execution_result.get("results", {})
            status = results.get("status", "unknown")
            error = execution_result.get("error", "")
            process_text = "\n".join(print_outputs) if print_outputs else "無輸出"
            if error:
                process_text += f"\n\nError: {error}"
            process_text = f"```\n{process_text}\n```"
            context.success_data = {
                "ctx": context.ctx,
                "instruction": context.resolved_instruction,
                "results": results,
                "process_text": process_text,
                "status": status,
                "error": error,
                "code": code,
            }
            context.results = results
            break
        return context


class SummaryStage:
    """生成最終答案"""

    async def process(self, context: AskContext, router: "CodeGenRouter") -> AskContext:
        if context.early_return:
            return context
        if not context.success_data:
            context.early_return = ({}, "Failed to generate and execute code after all retries.")
            return context
        sd = context.success_data
        context.results = sd["results"]

        if router._final_summary_enabled:
            final_answer, determined_status = await router.generate_final_answer(
                sd["ctx"], sd["instruction"], sd["results"],
                sd["process_text"], sd["status"], sd["error"]
            )
        else:
            final_answer = _build_deterministic_final_answer(router, sd)
            determined_status = sd["status"]

        context.results["status"] = determined_status
        context.final_answer = final_answer
        if determined_status == "unknown":
            context.results["status"] = "fail"
            context.results["reason"] = "Generated code did not set results['status'], which is mandatory"
        return context


class ObserveFinalStage:
    """流程結束後統一將 context 交給所有 observer 記錄"""

    async def process(self, context: AskContext, router: "CodeGenRouter") -> AskContext:
        await router._notify_observers_final(context)
        return context


class CodeGenRouter(RouterBase):
    """
    程式碼生成式Router：透過生成Python程式碼的方式呼叫環境模組中的@tool標記的介面。

    工作流程：
    1. 收集所有環境模組的工具資訊
    2. 使用類似 pyi 檔案的 Python 程式碼格式向LLM提供環境模組描述和工具資訊
       （包含 pydantic BaseModel 定義和模組類定義）
    3. LLM生成Python程式碼來呼叫工具
    4. 使用AST解析檢查程式碼安全性
    5. 透過compile和exec執行程式碼，捕獲列印輸出
    6. 根據執行結果和列印輸出生成最終響應
    """

    # 允許的內建函式
    ALLOWED_BUILTINS = {
        "print",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "tuple",
        "set",
        "range",
        "enumerate",
        "zip",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "sorted",
        "reversed",
        "any",
        "all",
        "isinstance",
        "type",
        "getattr",
        "hasattr",
        "dir",
    }

    # 禁止的AST節點型別（黑名單）
    FORBIDDEN_AST_NODES = {
        ast.ClassDef,  # 禁止定義類
        ast.Delete,  # 禁止del語句
        ast.Global,
        ast.Nonlocal,  # 禁止全域性/非區域性變數宣告
        ast.With,
        ast.AsyncWith,  # 禁止with語句
        ast.Assert,  # 禁止assert
    }

    # 允許匯入的模組白名單
    ALLOWED_MODULES = {
        "collections",
        "itertools",
        "functools",
        "operator",
        "copy",
        "decimal",
        "fractions",
        "statistics",
        "string",
        "re",
        "datetime",
        "json",
        "math",
        "random",
        "numpy",
        "np",  # numpy的別名
    }

    # 危險模組列表
    DANGEROUS_MODULES = {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "pickle",
        "marshal",
        "ctypes",
        "socket",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "__builtin__",
        "__builtins__",
        "builtins",
    }

    OBSERVE_INSTRUCTION = OBSERVE_INSTRUCTION
    STATISTICS_INSTRUCTION = STATISTICS_INSTRUCTION

    def __init__(
        self,
        env_modules: list[EnvBase],
        max_body_code_lines: int = 10,
        max_steps: int = 10,
        max_llm_call_retry: int = 10,
        log_path: str = "logs/instruction_log.pkl",
        replay_writer: Optional["ReplayWriter"] = None,
        final_summary_enabled: bool = True,
        # Template cache configuration
        template_cache_enabled: bool = True,  # 是否啟用模板快取
        template_cache_similarity_threshold: float = 0.85,  # 快取相似度閾值
        template_cache_max_size: int = 1000,  # 單 env 型別最大快取條目數
        template_cache_dir: Optional[str] = None,  # 快取資料庫目錄，None 則用 {Config.HOME_DIR}/codegen_router_cache
    ):
        super().__init__(
            env_modules=env_modules,
            max_steps=max_steps,
            max_llm_call_retry=max_llm_call_retry,
            replay_writer=replay_writer,
        )

        # Pre-generate all tools pyi code in a dictionary: key is (readonly, kind)
        # kind can be None, "observe", "statistics", etc.
        self._tools_pyi_dict: Dict[Tuple[bool, str | None], str] = {}
        self._log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        # Collect all tools info once
        all_tools_info = self._collect_tools_info()

        # Generate writable tools pyi code (kind=None)
        all_tools_info = self._filter_tools_info(
            all_tools_info, readonly=None, kind=None
        )
        self._tools_pyi_dict[(False, None)] = self._format_tools_pyi(
            all_tools_info, max_body_code_lines
        )

        # Generate readonly tools pyi code (kind=None)
        readonly_tools_info = self._filter_tools_info(
            all_tools_info, readonly=True, kind=None
        )
        self._tools_pyi_dict[(True, None)] = self._format_tools_pyi(
            readonly_tools_info, max_body_code_lines
        )

        # Generate readonly observe tools pyi code
        readonly_observe_tools_info = self._filter_tools_info(
            all_tools_info, readonly=True, kind="observe"
        )
        self._tools_pyi_dict[(True, "observe")] = self._format_tools_pyi(
            readonly_observe_tools_info, max_body_code_lines
        )

        # Generate readonly statistics tools pyi code
        readonly_statistics_tools_info = self._filter_tools_info(
            all_tools_info, readonly=True, kind="statistics"
        )
        self._tools_pyi_dict[(True, "statistics")] = self._format_tools_pyi(
            readonly_statistics_tools_info, max_body_code_lines
        )

        self._modules = {module.name: module for module in self.env_modules}

        # Statistics 初始化程式碼由 LLM 在 init 中生成；observe 走內建 _run_builtin_observe
        self._statistics_code = ""

        # Flag to track if LLM code generation has been attempted
        self._llm_code_generated = False

        # 記錄所有agent的指令、context和生成的程式碼
        self._instruction_log: List[EnvRouterBenchmarkData] = []
        self._instruction_log_lock: asyncio.Lock = asyncio.Lock()

        # ==================== Template快取相關 ====================
        self._final_summary_enabled = final_summary_enabled
        self._template_cache_enabled = template_cache_enabled
        self._template_cache_similarity_threshold = template_cache_similarity_threshold
        self._template_cache_max_size = template_cache_max_size

        # Env class type 指紋，用於快取隔離
        self._env_class_type_key = _get_env_class_type_key(env_modules)

        # 持久化快取（pickle 檔案，跨執行共用）
        cache_dir = template_cache_dir or os.path.join(Config.HOME_DIR, "codegen_router_cache")
        cache_path = os.path.join(cache_dir, "cache.pkl")
        self._cache_dir = cache_dir  # 與 cache 同目錄，用於儲存 cache_stats.jsonl
        self._cache_db = TemplateCacheDB(
            cache_path=cache_path,
            embedding_dims=Config.EMBEDDING_DIMS,
            max_size_per_env=template_cache_max_size,
        )
        # 每步統計：用於計算 delta 並寫入 JSONL
        self._cache_stats_jsonl_path = os.path.join(cache_dir, "cache_stats.jsonl")
        self._prev_step_stats: Optional[CacheStats] = None
        self._step_index: int = 0
        self._run_id: Optional[str] = None  # 在 init() 時設定，用於區分不同次模擬

        # 當前 env 的快取集（從 DB 載入，在 init() 時填充）
        self._cache_entries: List[CacheEntry] = []
        self._cache_faiss_index: Optional[faiss.Index] = None
        self._cache_faiss_entry_indices: List[int] = []
        self._template_cache_lock: asyncio.Lock = asyncio.Lock()

        # 快取統計資訊
        self._cache_stats = CacheStats()

        # Embedding快取
        self._embedding_cache: Dict[str, np.ndarray] = {}

        # Embedding模型配置（從Config獲取）
        self._embedding_model = Config.EMBEDDING_MODEL
        self._embedding_api_key = Config.EMBEDDING_API_KEY
        self._embedding_api_base = Config.EMBEDDING_API_BASE
        self._embedding_dims = Config.EMBEDDING_DIMS

        # 併發鎖：僅程式碼執行需序列（會修改 env 狀態），其餘（快取、codegen、generate_final_answer）可並行
        self._execute_lock: asyncio.Lock = asyncio.Lock()
        self._cache_stats_lock: asyncio.Lock = asyncio.Lock()
        self._embedding_cache_lock: asyncio.Lock = asyncio.Lock()

        # 觀察者（快取統計、instruction log、快取寫入）
        self._observers: List[AskObserver] = [
            InstructionLogObserver(self),
            CacheStatsObserver(self),
            CacheAddObserver(self),
        ]

        # Code 獲取責任鏈：Predefined -> Cache -> LLM（LLM 在 pipeline 中單獨處理）
        self._code_provider_chain: List[CodeProvider] = [
            PredefinedCodeProvider(),
            CacheCodeProvider(),
            LLMCodegenProvider(),
        ]

    @staticmethod
    def _resolve_subject_id_from_ctx(ctx: dict) -> Optional[int]:
        for k in ("id", "agent_id", "person_id"):
            if k not in ctx:
                continue
            v = ctx[k]
            if v is None or isinstance(v, bool):
                continue
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip():
                try:
                    return int(v.strip(), 10)
                except ValueError:
                    continue
        return None

    @staticmethod
    async def _call_single_observe_tool(module: Any, fn: Any, subject_id: Optional[int]) -> Any:
        # tool 裝飾器把真實方法包在 *args,**kwargs 裡；對 wrapper 做 signature 會得到 param 名 "args"，誤傳成 observe(args=…)。
        impl = getattr(fn, "_original_func", fn)
        sig = inspect.signature(impl)
        params = list(sig.parameters.values())
        non_self = [p for p in (params[1:] if params else []) if p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )]
        if len(non_self) == 0:
            if inspect.iscoroutinefunction(fn):
                return await fn(module)
            return fn(module)
        if subject_id is None:
            raise ValueError(
                "missing subject id in ctx (need id, agent_id, or person_id; integer 0 is valid)"
            )
        pname = non_self[0].name
        kw = {pname: subject_id}
        if inspect.iscoroutinefunction(fn):
            return await fn(module, **kw)
        return fn(module, **kw)

    async def _run_builtin_observe(self, ctx: dict) -> Dict[str, Any]:
        observe_info = self._filter_tools_info(
            self._collect_tools_info(), readonly=True, kind="observe"
        )
        n_tools = sum(len(m.tools) for m in observe_info.values())
        if n_tools == 0:
            return {
                "success": False,
                "results": {"observations": {}, "status": "fail"},
                "print_outputs": [],
                "output": "",
                "error": "no observe tools registered",
            }
        subject_id = self._resolve_subject_id_from_ctx(ctx)
        observations: Dict[str, Any] = {}
        errors: List[str] = []
        for module_name, module_data in observe_info.items():
            module = self._modules.get(module_name)
            if module is None:
                errors.append(f"{module_name}: module not mounted")
                continue
            reg = getattr(module.__class__, "_registered_tools", {})
            for ti in module_data.tools:
                tname = ti.name
                tool_obj = reg.get(tname)
                fn = getattr(tool_obj, "fn", None) if tool_obj else None
                if not fn:
                    errors.append(f"{module_name}.{tname}: tool missing")
                    continue
                try:
                    out = await self._call_single_observe_tool(module, fn, subject_id)
                    observations[f"{module_name}.{tname}"] = out
                except Exception as e:
                    errors.append(f"{module_name}.{tname}: {e}")
        n_ok = len(observations)
        if n_ok and not errors:
            status = "success"
        elif n_ok and errors:
            status = "partial"
        else:
            status = "fail"
        results: Dict[str, Any] = {"observations": observations, "status": status}
        if errors:
            results["observe_errors"] = errors
        success = n_ok > 0
        return {
            "success": success,
            "results": results,
            "print_outputs": [],
            "output": "",
            "error": "; ".join(errors) if not success else "",
        }

    async def _notify_observers_final(self, context: AskContext) -> None:
        """流程結束後，將最終 context 交給所有觀察者記錄"""
        for obs in self._observers:
            await obs.on_final(context)

    async def ask(
        self, ctx: dict, instruction: str, readonly: bool = False, template_mode: bool = False
    ) -> Tuple[dict, str]:
        """
        使用程式碼生成方式處理指令。透過管道-過濾器架構執行。

        Args:
            ctx: 上下文字典（在template模式下，應包含'variables'鍵）
            instruction: 指令字串（在template模式下，為模板指令，包含{variable_name}佔位符）
            readonly: 是否只讀模式
            template_mode: 是否啟用模板模式（啟用後使用快取機制）

        Returns:
            (ctx, answer) 元組
        """
        context = AskContext(ctx=ctx, instruction=instruction, readonly=readonly, template_mode=template_mode)
        stages: List[PipelineStage] = [
            InitStage(),
            CodeStage(),  # 程式碼獲取 + 驗證 + 執行
            SummaryStage(),
            ObserveFinalStage(),  # 無論是否 early_return，最後統一交給 observer 記錄
        ]
        for stage in stages:
            context = await stage.process(context, self)
        if context.early_return:
            return context.early_return
        return context.results, context.final_answer

    async def init(self, start_datetime: datetime):
        """
        Initialize the router with the start datetime and generate code using LLM.
        從本地快取資料庫載入當前 env 型別的快取集，構建 FAISS 索引。
        """
        await super().init(start_datetime)

        # 在async上下文中初始化鎖
        get_logger().debug("Initialized instruction log lock")

        # 從持久化 DB 載入當前 env_class_type 的快取，用於 embedding 相似度檢索
        if self._template_cache_enabled:
            self._load_cache_from_db()

        # 為本次模擬生成 run_id，便於在 JSONL 中區分不同次模擬
        self._run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Generate code using LLM if not already done
        if not self._llm_code_generated:
            # Generate statistics code using LLM (using same logic as regular code generation)
            if (True, "statistics") in self._tools_pyi_dict:
                llm_statistics_code = await self._generate_statistics_code()
                if llm_statistics_code:
                    self._statistics_code = llm_statistics_code
                    get_logger().info("Generated statistics code using LLM")
                else:
                    raise ValueError("Failed to generate statistics code")
            self._llm_code_generated = True

    async def step(self, tick: int, t: datetime):
        """
        Run forward one step for all simulation modules, then output cache statistics.
        每步結束後將本步的統計指標以 JSONL 形式追加寫入快取目錄，便於分析快取帶來的效能提升。
        """
        await super().step(tick, t)

        # 快照當前累計統計，計算本步增量，寫入 JSONL（追加模式，支援多次模擬聚合）
        async with self._cache_stats_lock:
            current = CacheStats(
                request_count=self._cache_stats.request_count,
                predefined_hit_count=self._cache_stats.predefined_hit_count,
                cache_hit_count=self._cache_stats.cache_hit_count,
                cache_miss_count=self._cache_stats.cache_miss_count,
                total_input_tokens=self._cache_stats.total_input_tokens,
                total_output_tokens=self._cache_stats.total_output_tokens,
                code_execution_success_count=self._cache_stats.code_execution_success_count,
                code_execution_failure_count=self._cache_stats.code_execution_failure_count,
                total_code_retry_count=self._cache_stats.total_code_retry_count,
            )
        prev = self._prev_step_stats or CacheStats()
        delta = CacheStats(
            request_count=current.request_count - prev.request_count,
            predefined_hit_count=current.predefined_hit_count - prev.predefined_hit_count,
            cache_hit_count=current.cache_hit_count - prev.cache_hit_count,
            cache_miss_count=current.cache_miss_count - prev.cache_miss_count,
            total_input_tokens=current.total_input_tokens - prev.total_input_tokens,
            total_output_tokens=current.total_output_tokens - prev.total_output_tokens,
            code_execution_success_count=current.code_execution_success_count - prev.code_execution_success_count,
            code_execution_failure_count=current.code_execution_failure_count - prev.code_execution_failure_count,
            total_code_retry_count=current.total_code_retry_count - prev.total_code_retry_count,
        )
        exec_total = delta.code_execution_success_count + delta.code_execution_failure_count
        code_execution_success_rate = (
            delta.code_execution_success_count / exec_total if exec_total > 0 else None
        )
        record = {
            "run_id": self._run_id or "",
            "step": self._step_index,
            "request_count": delta.request_count,
            "predefined_hit_count": delta.predefined_hit_count,
            "cache_hit_count": delta.cache_hit_count,
            "cache_miss_count": delta.cache_miss_count,
            "cached_entries_count": len(self._cache_entries),  # 當前已快取資料條目標數
            "total_input_tokens": delta.total_input_tokens,
            "total_output_tokens": delta.total_output_tokens,
            "code_execution_success_rate": code_execution_success_rate,
        }
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            with open(self._cache_stats_jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            get_logger().warning(f"Failed to write cache stats JSONL: {e}")

        self._prev_step_stats = current
        self._step_index += 1
        get_logger().info(self.get_cache_stats_summary())

    async def dump(self) -> dict:
        """
        Dump router state to a serializable dict, including instruction logs and cache stats.
        """
        # 呼叫父類的dump方法獲取基礎狀態
        base_dump = await super().dump()

        # 新增指令日誌
        async with self._instruction_log_lock:
            base_dump["instruction_log"] = self._instruction_log.copy()

        # 新增快取統計資訊
        async with self._cache_stats_lock:
            base_dump["cache_stats"] = {
                "request_count": self._cache_stats.request_count,
                "predefined_hit_count": self._cache_stats.predefined_hit_count,
                "cache_hit_count": self._cache_stats.cache_hit_count,
                "cache_miss_count": self._cache_stats.cache_miss_count,
                "total_input_tokens": self._cache_stats.total_input_tokens,
                "total_output_tokens": self._cache_stats.total_output_tokens,
                "code_execution_success_count": self._cache_stats.code_execution_success_count,
                "code_execution_failure_count": self._cache_stats.code_execution_failure_count,
                "total_code_retry_count": self._cache_stats.total_code_retry_count,
            }

        return base_dump

    async def load(self, dump_data: dict):
        """
        Load router state from a dict produced by dump().
        """
        # 呼叫父類的load方法恢復基礎狀態
        await super().load(dump_data)

        # 恢復指令日誌
        try:
            instruction_log = dump_data.get("instruction_log", [])
            if isinstance(instruction_log, list):
                async with self._instruction_log_lock:
                    self._instruction_log = instruction_log.copy()
                get_logger().debug(
                    f"Loaded {len(instruction_log)} instruction log entries"
                )
        except Exception as e:
            get_logger().warning(f"Failed to load instruction log: {str(e)}")

    async def _generate_initialization_code_with_retry(
        self, instruction: str, ctx: dict, kind: str,
    ) -> str:
        """生成 observe/statistics 初始化程式碼，使用 LLMCodegenProvider 與 CodeStage。"""
        llm_provider = LLMCodegenProvider()
        code_stage = CodeStage()
        prompt = llm_provider._build_prompt(self, instruction, ctx, True, kind)
        dialog_history: List[AllMessageValues] = [{"role": "user", "content": prompt}]
        previous_code: Optional[str] = None
        previous_errors: List[str] = []
        retry_count = 0

        while retry_count <= self.max_llm_call_retry:
            # 構造臨時 context 供 _call_llm 使用
            tmp_ctx = AskContext(ctx=ctx, instruction=instruction, readonly=True, template_mode=False)
            tmp_ctx.dialog_history = dialog_history
            tmp_ctx.retry_count = retry_count
            tmp_ctx.previous_code = previous_code
            tmp_ctx.previous_errors = previous_errors

            code, token_usage = await llm_provider._call_llm(self, tmp_ctx)
            if token_usage:
                async with self._cache_stats_lock:
                    self._cache_stats.total_input_tokens += token_usage.get("input_tokens", 0)
                    self._cache_stats.total_output_tokens += token_usage.get("output_tokens", 0)

            if not code:
                previous_errors.append("Failed to generate code from LLM.")
                previous_code = None
                if retry_count >= self.max_llm_call_retry:
                    raise ValueError(f"Failed to generate {kind} code after retries.")
                retry_count += 1
                if previous_code:
                    dialog_history.append({"role": "assistant", "content": previous_code})
                dialog_history.append({"role": "user", "content": llm_provider._build_error_message(previous_errors)})
                continue

            dialog_history.append({"role": "assistant", "content": code})
            is_safe, safety_violation = code_stage._validate_code_safety(self, code)
            if not is_safe:
                previous_errors.append(safety_violation)
                previous_code = code
                if retry_count >= self.max_llm_call_retry:
                    raise ValueError(f"Generated {kind} code failed safety check after retries: {safety_violation}")
                retry_count += 1
                dialog_history.append({"role": "user", "content": llm_provider._build_error_message(previous_errors)})
                continue

            try:
                async with self._execute_lock:
                    execution_result = await code_stage._execute_code(self, code, ctx, True)
                if not execution_result.get("success", False):
                    previous_errors.append(f"Execution failed: {execution_result.get('error', 'Unknown error')}")
                    previous_code = code
                    if retry_count >= self.max_llm_call_retry:
                        raise ValueError(f"Generated {kind} code failed execution after retries.")
                    retry_count += 1
                    dialog_history.append({"role": "user", "content": llm_provider._build_error_message(previous_errors)})
                    continue
            except Exception as e:
                previous_errors.append(f"Execution exception: {str(e)}")
                previous_code = code
                if retry_count >= self.max_llm_call_retry:
                    raise ValueError(f"Generated {kind} code failed execution after retries: {str(e)}")
                retry_count += 1
                dialog_history.append({"role": "user", "content": llm_provider._build_error_message(previous_errors)})
                continue

            return code.strip()
        raise ValueError(f"Failed to generate {kind} code after retries.")

    async def _generate_statistics_code(self) -> str:
        """
        使用LLM生成統計程式碼，用於呼叫所有statistics型別的工具。
        使用與其他普通文字相同的程式碼生成邏輯，失敗時透過多輪對話讓LLM修正。

        Returns:
            生成的Python程式碼字串，如果生成失敗則返回空字串
        """
        get_logger().debug(f"{_get_debug_info('開始生成statistics程式碼')}")

        if (True, "statistics") not in self._tools_pyi_dict:
            get_logger().debug(
                f"{_get_debug_info('statistics工具不存在')} - 跳過程式碼生成"
            )
            return ""

        instruction = self.STATISTICS_INSTRUCTION
        ctx = {}  # 測試執行用的最小上下文
        return await self._generate_initialization_code_with_retry(
            instruction=instruction, ctx=ctx, kind="statistics"
        )

    def _load_cache_from_db(self) -> None:
        """從本地快取資料庫載入當前 env_class_type 的快取集，構建 FAISS 索引。"""
        entries, index, indices = self._cache_db.load_entries(self._env_class_type_key)
        self._cache_entries = entries
        self._cache_faiss_index = index
        self._cache_faiss_entry_indices = indices
        env_preview = self._env_class_type_key[:80] + ("..." if len(self._env_class_type_key) > 80 else "")
        get_logger().info(
            f"Loaded {len(entries)} cache entries (env={env_preview}), FAISS index size: {len(indices)}"
        )

    def get_cache_stats(self) -> CacheStats:
        """
        獲取快取統計資訊。

        Returns:
            CacheStats物件，包含所有快取統計資訊
        """
        # 同步方法無法用 async with，暫不加鎖；step 中呼叫 get_cache_stats_summary 為最佳-effort
        return self._cache_stats

    def get_cache_stats_summary(self) -> str:
        """
        獲取快取統計資訊的摘要字串。

        Returns:
            格式化的統計資訊字串
        """
        stats = self._cache_stats
        return f"""Cache Statistics Summary:
- Total Requests: {stats.request_count}
- Predefined Hits (observe/statistics): {stats.predefined_hit_count}
- Cache Hits: {stats.cache_hit_count}
- Cache Misses: {stats.cache_miss_count}
- Cache Hit Rate: {stats.cache_hit_rate:.2%}
- Total Input Tokens: {stats.total_input_tokens}
- Total Output Tokens: {stats.total_output_tokens}
- Average Input Tokens: {stats.avg_input_tokens:.2f}
- Average Output Tokens: {stats.avg_output_tokens:.2f}
- Code Execution Success: {stats.code_execution_success_count}
- Code Execution Failure: {stats.code_execution_failure_count}
- Code Execution Success Rate: {stats.code_execution_success_rate:.2%}
- Total Code Retries: {stats.total_code_retry_count}
- Average Code Retries per Request: {stats.avg_retry_count:.2f}
- Cache Size: {len(self._cache_entries)} entries (env={self._env_class_type_key[:60] + ('...' if len(self._env_class_type_key) > 60 else '')})"""

    async def clear_cache(self) -> None:
        """清空當前 env 的快取（記憶體 + 持久化 DB）"""
        async with self._template_cache_lock:
            self._cache_db.clear_env_cache(self._env_class_type_key)
            self._cache_entries = []
            self._cache_faiss_index = None
            self._cache_faiss_entry_indices = []
            self._embedding_cache.clear()
            get_logger().info("Template cache cleared")
