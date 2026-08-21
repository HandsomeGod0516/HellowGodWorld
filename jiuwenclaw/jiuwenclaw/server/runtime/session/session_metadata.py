"""會話後設資料管理模組"""
from __future__ import annotations

import copy
import json
import logging
import queue
import shutil
import threading
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from jiuwenclaw.common.utils import get_agent_sessions_dir

logger = logging.getLogger(__name__)

# ---------- 非同步寫入佇列(與 session_history 保持一致的模式) ----------
_METADATA_QUEUE: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=5000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()

# 記憶體快取: 解決非同步寫入時讀取到陳舊磁碟資料的競態條件
_METADATA_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()

# 會話標題自動生成的擷取長度
_TITLE_MAX_LEN = 50
_DELIVERY_KIND_SERVER_PUSH = "server_push"


def _current_timestamp() -> float:
    """返回顯式使用 UTC 時區的當前時間戳"""
    return datetime.now(timezone.utc).timestamp()


def _metadata_file(session_id: str) -> Path:
    """獲取會話後設資料檔案路徑"""
    session_dir = get_agent_sessions_dir() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "metadata.json"


def _read_metadata(session_id: str) -> dict[str, Any]:
    """讀取會話後設資料(優先從記憶體快取讀取,避免非同步寫入未落盤時讀到陳舊資料)

    讀路徑不應產生副作用：即便 session 目錄不存在，也不觸發 mkdir，
    否則會導致僅查詢(session.rename 無 title 引數時)隱式建立空 session 目錄，
    汙染 session.list 結果。
    """
    with _CACHE_LOCK:
        cached = _METADATA_CACHE.get(session_id)
        if cached is not None:
            return cached.copy()
    fpath = get_agent_sessions_dir() / session_id / "metadata.json"
    if not fpath.exists():
        return {}
    try:
        data = json.loads(fpath.read_text(encoding="utf-8") or '{}')
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("讀取 metadata.json 失敗: %s", exc)
    return {}


def _write_metadata_sync(session_id: str, metadata: dict[str, Any]) -> None:
    """同步寫入會話後設資料(由後臺 worker 或 fallback 呼叫)

    注意: 不更新 _METADATA_CACHE。快取僅由 _enqueue_write 維護,
    避免 gateway 程序的 init_session_metadata 汙染快取導致後續
    讀取不到 agentserver 程序寫入的最新資料。
    """
    fpath = _metadata_file(session_id)
    with _FILE_LOCK:
        fpath.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _ensure_worker_started() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker() -> None:
            while True:
                sid, metadata = _METADATA_QUEUE.get()
                try:
                    _write_metadata_sync(sid, metadata)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("metadata 非同步寫入失敗: %s", exc)
                finally:
                    _METADATA_QUEUE.task_done()

        t = threading.Thread(target=_worker, name="session-metadata-writer", daemon=True)
        t.start()
        _WORKER_STARTED = True


def _enqueue_write(session_id: str, metadata: dict[str, Any]) -> None:
    """將寫入操作放入非同步佇列,佇列滿時退化為同步寫"""
    # 立即更新快取,確保後續讀取能看到最新狀態
    with _CACHE_LOCK:
        _METADATA_CACHE[session_id] = metadata.copy()
    _ensure_worker_started()
    try:
        _METADATA_QUEUE.put_nowait((session_id, metadata))
    except queue.Full:
        _write_metadata_sync(session_id, metadata)


def _auto_title(content: str) -> str:
    """從首條使用者訊息自動生成會話標題"""
    title = content.strip().replace("\n", " ")
    if len(title) > _TITLE_MAX_LEN:
        title = title[:_TITLE_MAX_LEN] + "..."
    return title


def init_session_metadata(
    *,
    session_id: str,
    channel_id: str = "",
    user_id: str = "",
    title: str = "",
    mode: str = "unknown",
) -> None:
    """初始化會話後設資料(同步寫,確保建立後立即可讀)"""
    metadata = {
        "session_id": session_id,
        "channel_id": channel_id,
        "user_id": user_id,
        "created_at": _current_timestamp(),
        "last_message_at": _current_timestamp(),
        "title": title,
        "message_count": 0,
        "mode": mode,
    }
    _write_metadata_sync(session_id, metadata)


def update_session_metadata(
    *,
    session_id: str,
    channel_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
    clear_title: bool = False,
    increment_message_count: bool = False,
    user_content: str | None = None,
    channel_metadata: dict[str, Any] | None = None,
    mode: str | None = None,
) -> None:
    """更新會話後設資料(非同步寫入,不阻塞呼叫方)

    title 語義(保持歷史防禦契約)：
      - title=None  → 不修改（預設）
      - title="x"   → 設定為 "x"
      - title=""    → 忽略（防禦意外空值覆蓋已有標題）
      - 若需顯式清除標題，請設定 clear_title=True
    """
    metadata = _read_metadata(session_id)

    if not metadata:
        # 如果後設資料不存在,建立新的(外部渠道隱式建立 session 的兜底)
        # 自動生成標題: 當 title 為空且提供了使用者訊息內容時
        auto_title = ""
        if not title and user_content:
            auto_title = _auto_title(user_content)
        metadata = {
            "session_id": session_id,
            "channel_id": channel_id or "",
            "user_id": user_id or "",
            "created_at": _current_timestamp(),
            "last_message_at": _current_timestamp(),
            "title": title or auto_title,
            "message_count": 1 if increment_message_count else 0,
            "mode": mode if mode is not None else "unknown",
        }
        # 首次建立時寫入 channel_metadata
        if channel_metadata:
            metadata["channel_metadata"] = channel_metadata
    else:
        # 更新現有後設資料
        if channel_id is not None:
            metadata["channel_id"] = channel_id
        if user_id is not None:
            metadata["user_id"] = user_id
        if mode is not None:
            metadata["mode"] = mode
        # 顯式清除優先順序高於 title 入參
        if clear_title:
            metadata["title"] = ""
        elif title:
            metadata["title"] = title
        if increment_message_count:
            metadata["message_count"] = metadata.get("message_count", 0) + 1

        # 自動生成標題: 當 title 為空且提供了使用者訊息內容時
        if not metadata.get("title") and user_content:
            metadata["title"] = _auto_title(user_content)

        # channel_metadata 僅在首次為空時補充寫入（不覆蓋）
        if channel_metadata and not metadata.get("channel_metadata"):
            metadata["channel_metadata"] = channel_metadata

        # 總是更新最後訊息時間
        metadata["last_message_at"] = _current_timestamp()

    _enqueue_write(session_id, metadata)


def get_session_metadata(session_id: str) -> dict[str, Any]:
    """獲取會話後設資料"""
    return _read_metadata(session_id)


def set_session_delivery_context(
    *,
    session_id: str,
    channel_id: str | None,
    source_request_id: str | None,
    route_metadata: dict[str, Any] | None,
    delivery_kind: str = _DELIVERY_KIND_SERVER_PUSH,
) -> dict[str, Any]:
    """重新整理 session 級 delivery context，供非同步 server_push 恢復路由上下文。"""
    metadata = _read_metadata(session_id)
    current_context_raw = metadata.get("delivery_context")
    current_context = (
        copy.deepcopy(current_context_raw)
        if isinstance(current_context_raw, dict)
        else {}
    )

    normalized_channel_id = str(
        channel_id
        or current_context.get("channel_id")
        or metadata.get("channel_id")
        or ""
    ).strip()
    normalized_request_id = str(
        source_request_id or current_context.get("source_request_id") or ""
    ).strip()

    previous_route_metadata = current_context.get("route_metadata")
    if not isinstance(previous_route_metadata, dict):
        previous_route_metadata = None

    normalized_route_metadata = (
        copy.deepcopy(route_metadata)
        if isinstance(route_metadata, dict) and route_metadata
        else previous_route_metadata
    )

    if not metadata:
        metadata = {
            "session_id": session_id,
            "channel_id": normalized_channel_id,
            "user_id": "",
            "created_at": _current_timestamp(),
            "last_message_at": _current_timestamp(),
            "title": "",
            "message_count": 0,
            "mode": "unknown",
        }
    else:
        if normalized_channel_id:
            metadata["channel_id"] = normalized_channel_id
        metadata["last_message_at"] = _current_timestamp()

    delivery_context: dict[str, Any] = {
        "delivery_kind": str(delivery_kind or _DELIVERY_KIND_SERVER_PUSH).strip()
        or _DELIVERY_KIND_SERVER_PUSH,
        "session_id": session_id,
        "channel_id": normalized_channel_id,
        "source_request_id": normalized_request_id,
        "updated_at": _current_timestamp(),
    }
    if normalized_route_metadata:
        delivery_context["route_metadata"] = normalized_route_metadata

    metadata["delivery_context"] = delivery_context
    _enqueue_write(session_id, metadata)
    return copy.deepcopy(delivery_context)


def get_session_delivery_context(session_id: str) -> dict[str, Any] | None:
    """讀取 session 級 delivery context。"""
    metadata = _read_metadata(session_id)
    context = metadata.get("delivery_context")
    if not isinstance(context, dict):
        return None
    return copy.deepcopy(context)


def build_server_push_message(
    *,
    session_id: str,
    request_id: str,
    payload: dict[str, Any],
    fallback_channel_id: str | None = None,
) -> dict[str, Any]:
    """基於 session delivery context 構造 evolution watcher 的 server_push 訊息。"""
    delivery_context = get_session_delivery_context(session_id) or {}
    route_metadata = delivery_context.get("route_metadata")
    channel_id = str(
        delivery_context.get("channel_id") or fallback_channel_id or "default"
    ).strip() or "default"

    message: dict[str, Any] = {
        "request_id": request_id,
        "channel_id": channel_id,
        "session_id": session_id,
        "payload": dict(payload),
    }
    if isinstance(route_metadata, dict) and route_metadata:
        message["metadata"] = copy.deepcopy(route_metadata)
    return message


def remove_team_mode_session_dirs_at_startup() -> None:
    """agentserver 啟動時刪除 metadata.json 中 mode 為 team 的會話目錄。"""
    sessions_dir = get_agent_sessions_dir()
    if not sessions_dir.is_dir():
        return

    removed = 0
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        meta_path = session_dir / "metadata.json"
        if not meta_path.is_file():
            continue
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("啟動清理跳過會話 %s: 讀取 metadata.json 失敗: %s", session_dir.name, exc)
            continue
        if not isinstance(raw, dict) or raw.get("mode") != "team":
            continue

        session_id = session_dir.name
        try:
            shutil.rmtree(session_dir)
            with _CACHE_LOCK:
                _METADATA_CACHE.pop(session_id, None)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("啟動清理刪除 team 會話目錄失敗 %s: %s", session_id, exc)

    if removed:
        logger.info("啟動清理: 已刪除 %d 個 team 模式會話目錄", removed)


def get_all_sessions_metadata(
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    獲取所有會話的後設資料。

    Returns:
        (sessions, total): 當前頁的會話列表 和 會話總數
    """
    sessions_dir = get_agent_sessions_dir()
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return [], 0

    sessions = []
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue

        session_id = session_dir.name
        metadata = _read_metadata(session_id)

        if not metadata:
            # 沒有 metadata.json 的舊會話: 只構造最小資訊,不讀取 history.json
            # (避免大量舊會話導致介面變慢,完整推斷由啟動遷移負責)
            metadata = {
                "session_id": session_id,
                "channel_id": "",
                "user_id": "",
                "created_at": session_dir.stat().st_ctime,
                "last_message_at": session_dir.stat().st_mtime,
                "title": "",
                "message_count": 0,
                "mode": "unknown",
            }

        sessions.append(metadata)

    # 按最後訊息時間倒序排序
    sessions.sort(key=lambda x: x.get("last_message_at", 0), reverse=True)

    total = len(sessions)
    return sessions[offset: offset + limit], total
