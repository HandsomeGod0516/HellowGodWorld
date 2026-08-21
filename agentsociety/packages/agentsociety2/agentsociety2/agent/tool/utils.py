"""工具實用函式。

JSON 處理、字串截斷、分頁、重試邏輯。

JSON 容錯策略：
- 解析：使用 json_repair 自動修復常見 JSON 錯誤（缺少引號、尾隨逗號等）
- 序列化：自動處理 Pydantic 模型、datetime、set、bytes 等型別
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import json_repair


def truncate(text: str, max_len: int) -> str:
    """截斷文字到指定長度。

    :param text: 原始文字。
    :param max_len: 最大長度。
    :return: 截斷後的文字。
    :rtype: str
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...<truncated>"


#: 截斷函式別名
trunc_str = truncate


def _serialize_for_json(obj: Any) -> Any:
    """遞迴轉換物件為 JSON 可序列化格式。

    處理 Pydantic 模型、datetime、set、bytes 等型別。
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_for_json(item) for item in obj]
    # set, frozenset 轉為 list
    if isinstance(obj, (set, frozenset)):
        return [_serialize_for_json(item) for item in obj]
    # bytes 轉為字串
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    # Pydantic 模型
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except TypeError:
            return obj.model_dump()
    # datetime 型別
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except TypeError:
            pass
    # Mapping 型別
    if isinstance(obj, Mapping):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    # 其他型別轉為字串
    return str(obj)


def jr_dumps(obj: Any, indent: int | None = None) -> str:
    """JSON 序列化（帶容錯處理）。

    自動處理不可序列化型別，確保輸出有效 JSON。

    :param obj: 要序列化的物件。
    :param indent: 縮排級別；預設使用緊湊輸出以減少 token 與日誌體積。
    :return: JSON 字串。
    """
    serialized = _serialize_for_json(obj)
    json_text = json.dumps(serialized, indent=indent, ensure_ascii=False)
    return json_repair.repair_json(json_text, indent=indent, ensure_ascii=False)


def jr_parse(text: str) -> Any:
    """容錯 JSON 解析。

    自動修復常見 JSON 錯誤：
    - 缺少引號的鍵名
    - 尾隨逗號
    - 單引號代替雙引號
    - 註釋

    :param text: JSON 文字。
    :return: 解析後的物件。
    """
    if not text or not text.strip():
        return None
    return json_repair.loads(text)


def jr_parse_from_llm(content: str) -> Any:
    """從 LLM 響應中提取並解析 JSON。

    先從文字中提取 JSON 片段（支援 Markdown code fence），
    再用 json_repair 容錯解析。

    :param content: LLM 響應文字。
    :return: 解析後的 Python 物件。
    :raises ValueError: 無法從文字中提取 JSON。
    """
    from agentsociety2.config import extract_json

    json_str = extract_json(content)
    if json_str is None:
        s = content.strip()
        if s.startswith(("{", "[")):
            json_str = s
    if json_str is None or not str(json_str).strip():
        raise ValueError("Failed to extract JSON from LLM response")
    return json_repair.loads(json_str)


def paginate(items: list[Any], page: int, size: int) -> dict[str, Any]:
    """列表分頁。

    :param items: 完整列表。
    :param page: 頁碼（1-indexed）。
    :param size: 每頁數量。
    :return: 分頁結果字典。
    :rtype: dict[str, Any]
    """
    total = len(items)
    total_pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * size
    return {
        "items": items[start : start + size],
        "page": page,
        "size": size,
        "total_pages": total_pages,
        "total": total,
    }


def pagination_from_args(args: dict[str, Any], default_limit: int) -> tuple[int, int]:
    """從工具引數提取分頁引數。

    :param args: 工具引數字典。
    :param default_limit: 預設限制。
    :return: (offset, limit) 元組。
    :rtype: tuple[int, int]
    """
    offset = max(0, int(args.get("offset", 0)))
    limit = max(1, min(default_limit, int(args.get("limit", default_limit))))
    return offset, limit


def slice_text_page(text: str, offset: int, limit: int) -> dict[str, Any]:
    """文字分頁切片。

    :param text: 完整文字。
    :param offset: 字元偏移。
    :param limit: 字元限制。
    :return: 分頁結果字典。
    :rtype: dict[str, Any]
    """
    total = len(text)
    if offset >= total:
        return {
            "content": "",
            "total_chars": total,
            "offset": offset,
            "limit_applied": limit,
            "returned_chars": 0,
            "next_offset": None,
            "has_more": False,
        }
    end = min(offset + limit, total)
    content = text[offset:end]
    next_offset = end if end < total else None
    return {
        "content": content,
        "total_chars": total,
        "offset": offset,
        "limit_applied": limit,
        "returned_chars": len(content),
        "next_offset": next_offset,
        "has_more": next_offset is not None,
    }


def json_dumps_tool_result_for_thread(
    result: dict[str, Any], budget: int = 65536
) -> str:
    """序列化工具結果用於 thread。

    :param result: 工具結果字典。
    :param budget: 字元預算。
    :return: 預算內的 JSON 字串。
    """
    s = jr_dumps(result, indent=None)
    if len(s) <= budget:
        return s
    truncated = {}
    for k, v in result.items():
        if isinstance(v, str) and len(v) > budget // 4:
            truncated[k] = truncate(v, budget // 4)
        else:
            truncated[k] = v
    return jr_dumps(truncated, indent=None)


async def async_retry_on_transient(
    fn: Any, max_retries: int = 2, log_prefix: str = ""
) -> Any:
    """瞬時錯誤重試。

    :param fn: 要呼叫的非同步函式。
    :param max_retries: 最大重試次數。
    :param log_prefix: 日誌字首。
    :return: 函式結果。
    :rtype: Any
    """
    from agentsociety2.logger import get_logger

    logger = get_logger()

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            is_transient = any(
                x in err_str for x in ("rate limit", "429", "timeout", "connection")
            )
            if not is_transient or attempt >= max_retries:
                raise
            delay = 0.5 * (2**attempt)
            if log_prefix:
                logger.warning(
                    f"{log_prefix}transient error (attempt {attempt + 1}/{max_retries + 1}): {e}; retry in {delay}s"
                )
            await asyncio.sleep(delay)
    raise last_err or RuntimeError("Unexpected error in async_retry_on_transient")
