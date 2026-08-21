"""Agent持久化模組。

整合檢查點、預寫日誌、清理和會話恢復功能。

模組結構
========

- :class:`Checkpoint`: ACID檢查點管理，支援崩潰恢復
- :class:`WriteAheadLog`: 預寫日誌（追加寫入 + 記憶體索引 + fsync）
- :class:`WorkspaceCleaner`: 工作區清理
- :class:`SessionRecovery`: 會話恢復上下文構建

ACID保證
========

Checkpoint 實現原子寫入：

1. **原子性**: 使用臨時檔案 + 原子重新命名
2. **一致性**: 包含版本號和校驗和
3. **永續性**: 寫入後呼叫 fsync 重新整理到磁碟

WAL 特性
========

- 追加寫入，不重寫檔案
- 記憶體索引追蹤 intent_id -> offset
- 每次寫入後 fsync 確保持久化
- 壓縮時保護 pending 狀態

示例
====

基本使用::

    from agentsociety2.agent.persistence import Checkpoint, WriteAheadLog

    # 檢查點
    checkpoint = Checkpoint(workspace, config)
    checkpoint.save(tick=100, state={"step_count": 42})
    data = checkpoint.restore(100)

    # 預寫日誌
    wal = WriteAheadLog(workspace, max_entries=1000)
    intent_id = wal.log_intent("workspace_write", {"path": "test.txt"}, tick=1)
    wal.log_result(intent_id, {"ok": True})
    pending = wal.get_pending()
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .config import AgentConfig
from .tool.utils import jr_dumps as _jr_dumps, jr_parse

RUNTIME_DIR = ".runtime"
RUNTIME_LOG_DIR = f"{RUNTIME_DIR}/logs"


def _compute_checksum(data: dict[str, Any]) -> str:
    """計算資料的校驗和。"""
    content = _jr_dumps(data, indent=None)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _fsync_path(path: Path) -> None:
    """確保檔案內容重新整理到磁碟。"""
    if path.exists():
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class IntentStatus(str, Enum):
    """意圖狀態列舉。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Checkpoint:
    """ACID檢查點管理器。

    支援儲存和恢復Agent在特定tick的完整狀態。
    使用臨時檔案 + 原子重新命名實現原子寫入。
    """

    VERSION = 1

    def __init__(self, workspace: Path, config: AgentConfig):
        self.workspace = workspace
        self.config = config
        self.dir = workspace / RUNTIME_DIR / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, tick: int) -> Path:
        return self.dir / f"checkpoint_{tick}.json"

    def _temp_path(self, tick: int) -> Path:
        return self.dir / f"checkpoint_{tick}.tmp"

    def save(self, tick: int, state: dict[str, Any]) -> Path:
        """儲存檢查點（原子寫入）。

        :param tick: 時間步。
        :param state: 狀態資料。
        :return: 檢查點檔案路徑。
        """
        data = {
            "version": self.VERSION,
            "tick": tick,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }
        data["checksum"] = _compute_checksum(data)

        temp_path = self._temp_path(tick)
        final_path = self._path(tick)

        temp_path.write_text(_jr_dumps(data), encoding="utf-8")
        _fsync_path(temp_path)
        temp_path.replace(final_path)
        _fsync_path(self.dir)

        self._cleanup()
        return final_path

    def restore(self, tick: int) -> Optional[dict[str, Any]]:
        """恢復檢查點。

        :param tick: 時間步。
        :return: 檢查點資料，不存在返回 None。
        """
        path = self._path(tick)
        if not path.exists():
            return None

        try:
            data = jr_parse(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None

            # 驗證校驗和
            expected = data.pop("checksum", None)
            if expected and expected != _compute_checksum(data):
                return None

            return data
        except Exception:
            return None

    def latest_tick(self) -> Optional[int]:
        """獲取最新檢查點的tick。"""
        checkpoints = sorted(self.dir.glob("checkpoint_*.json"))
        if not checkpoints:
            return None
        name = checkpoints[-1].stem
        parts = name.split("_")
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
        return None

    def _cleanup(self) -> None:
        """清理舊檢查點。"""
        checkpoints = sorted(self.dir.glob("checkpoint_*.json"))
        while len(checkpoints) > self.config.persistence.checkpoint_max:
            checkpoints[0].unlink()
            checkpoints = checkpoints[1:]


class WriteAheadLog:
    """預寫日誌管理器。

    在工具執行前記錄意圖，執行後記錄結果。
    使用追加日誌 + 記憶體索引，每次寫入後 fsync 確保持久化。

    :ivar path: 日誌檔案路徑。
    :ivar index_path: 索引檔案路徑。
    :ivar max_entries: 最大保留條目數。
    """

    def __init__(self, workspace: Path, max_entries: int = 1000):
        """初始化 WAL。

        :param workspace: 工作區根目錄。
        :param max_entries: 最大保留條目數。
        """
        self.path = workspace / RUNTIME_DIR / "wal" / "wal.jsonl"
        self.index_path = workspace / RUNTIME_DIR / "wal" / "index.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._counter = 0
        self._index: dict[str, int] = self._load_index()
        self._pending: dict[str, dict[str, Any]] = self._load_pending()

    def _load_index(self) -> dict[str, int]:
        """從磁碟載入索引。"""
        if not self.index_path.exists():
            return {}
        try:
            data = jr_parse(self.index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: int(v) for k, v in data.items() if isinstance(v, (int, str))}
        except Exception:
            pass
        return {}

    def _load_pending(self) -> dict[str, dict[str, Any]]:
        """從 WAL 檔案重建 pending 狀態。"""
        pending = {}
        completed = set()

        if not self.path.exists():
            return pending

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = jr_parse(line)
                        intent_id = data.get("intent_id")
                        status = data.get("status")

                        if intent_id:
                            if status == IntentStatus.PENDING.value:
                                pending[intent_id] = data
                            elif status in (
                                IntentStatus.COMPLETED.value,
                                IntentStatus.FAILED.value,
                            ):
                                completed.add(intent_id)
                    except Exception:
                        pass
        except Exception:
            pass

        # 移除已完成的
        for intent_id in completed:
            pending.pop(intent_id, None)

        return pending

    def _save_index(self) -> None:
        """儲存索引到磁碟。"""
        self.index_path.write_text(
            _jr_dumps(self._index, indent=None), encoding="utf-8"
        )
        _fsync_path(self.index_path)

    def log_intent(self, action: str, arguments: dict[str, Any], tick: int) -> str:
        """記錄執行意圖，返回意圖ID。

        :param action: 工具名稱。
        :param arguments: 工具引數。
        :param tick: 當前 tick。
        :return: 意圖 ID。
        """
        self._counter += 1
        intent_id = f"intent_{tick}_{self._counter}"
        intent = {
            "intent_id": intent_id,
            "action": action,
            "arguments": arguments,
            "tick": tick,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": IntentStatus.PENDING.value,
        }

        entry = _jr_dumps(intent) + "\n"
        offset = self.path.stat().st_size if self.path.exists() else 0

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry)
            f.flush()
            os.fsync(f.fileno())

        self._index[intent_id] = offset
        self._pending[intent_id] = intent
        self._save_index()
        self._maybe_compact()

        return intent_id

    def log_result(
        self, intent_id: str, result: dict[str, Any], success: bool = True
    ) -> None:
        """記錄執行結果。

        :param intent_id: 意圖 ID。
        :param result: 執行結果。
        :param success: 是否成功。
        """
        result_entry = {
            "intent_id": intent_id,
            "status": (
                IntentStatus.COMPLETED.value if success else IntentStatus.FAILED.value
            ),
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        entry = _jr_dumps(result_entry) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry)
            f.flush()
            os.fsync(f.fileno())

        self._pending.pop(intent_id, None)

    def get_pending(self) -> list[dict[str, Any]]:
        """獲取待處理意圖列表。"""
        return list(self._pending.values())

    def get_pending_after_tick(self, tick: int) -> list[dict[str, Any]]:
        """獲取指定 tick 之後的待處理意圖。"""
        return [
            intent for intent in self._pending.values() if intent.get("tick", 0) > tick
        ]

    def _maybe_compact(self) -> None:
        """當條目數超過限制時壓縮檔案。保護 pending 狀態。"""
        total_entries = len(self._index)
        if total_entries <= self.max_entries:
            return

        pending_intent_ids = set(self._pending.keys())

        try:
            lines = self.path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) <= self.max_entries:
                return

            # 保留包含 pending intent 的行 + 最近的行
            kept_lines = []
            for line in reversed(lines[-self.max_entries * 2 :]):
                try:
                    data = jr_parse(line)
                    intent_id = data.get("intent_id")
                    if (
                        intent_id in pending_intent_ids
                        or len(kept_lines) < self.max_entries
                    ):
                        kept_lines.insert(0, line)
                except Exception:
                    pass

            self.path.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
            _fsync_path(self.path)

            # 重建索引
            self._index.clear()
            offset = 0
            for line in kept_lines:
                try:
                    data = jr_parse(line)
                    intent_id = data.get("intent_id")
                    if intent_id:
                        self._index[intent_id] = offset
                except Exception:
                    pass
                offset += len(line.encode("utf-8")) + 1

            self._save_index()
        except Exception:
            pass

    def clear_completed(self) -> int:
        """清理已完成和失敗的意圖記錄。返回清理數量。"""
        if not self.path.exists():
            return 0

        pending_intent_ids = set(self._pending.keys())
        lines_to_keep = []
        cleaned = 0

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = jr_parse(line)
                        intent_id = data.get("intent_id")
                        if intent_id in pending_intent_ids:
                            lines_to_keep.append(line)
                        else:
                            cleaned += 1
                    except Exception:
                        pass

            if cleaned > 0:
                self.path.write_text("\n".join(lines_to_keep) + "\n", encoding="utf-8")
                _fsync_path(self.path)

                # 重建索引
                self._index.clear()
                offset = 0
                for line in lines_to_keep:
                    try:
                        data = jr_parse(line)
                        intent_id = data.get("intent_id")
                        if intent_id:
                            self._index[intent_id] = offset
                    except Exception:
                        pass
                    offset += len(line.encode("utf-8")) + 1
                self._save_index()
        except Exception:
            pass

        return cleaned


class WorkspaceCleaner:
    """工作區清理器。"""

    def __init__(self, workspace: Path, config: AgentConfig):
        self.workspace = workspace
        self.config = config

    async def cleanup(self) -> dict[str, Any]:
        """執行清理。"""
        stats = {"files_removed": 0, "bytes_freed": 0}

        # 清理日誌
        log_dir = self.workspace / RUNTIME_LOG_DIR
        if log_dir.exists():
            logs = sorted(
                log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            for log in logs[self.config.persistence.max_log_files :]:
                stats["bytes_freed"] += log.stat().st_size
                log.unlink()
                stats["files_removed"] += 1

        # 清理對話歷史檔案
        history_dir = log_dir / "thread_history"
        if history_dir.exists():
            history_files = sorted(
                history_dir.glob("compact_*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            max_history = getattr(
                self.config.persistence, "thread_history_max_files", 20
            )
            for hf in history_files[max_history:]:
                stats["bytes_freed"] += hf.stat().st_size
                hf.unlink()
                stats["files_removed"] += 1

        # 輪轉 jsonl：避免長跑實驗無限增長（保留最近 N 行）
        # 這些檔案是可裁剪的執行時日誌；關鍵事實應由 thread compaction / AGENT.md 索引承載。
        jsonl_keep = int(getattr(self.config.context, "thread_max_messages", 50) * 50)
        jsonl_targets = [
            log_dir / "thread_messages.jsonl",
            log_dir / "tool_calls.jsonl",
            log_dir / "session_state_history.jsonl",
            log_dir / "step_replay.jsonl",
        ]
        for p in jsonl_targets:
            if not p.exists() or not p.is_file():
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
                if len(lines) <= jsonl_keep:
                    continue
                kept = lines[-jsonl_keep:]
                before = p.stat().st_size
                p.write_text("\n".join(kept) + "\n", encoding="utf-8")
                after = p.stat().st_size
                freed = max(0, before - after)
                if freed:
                    stats["bytes_freed"] += freed
            except Exception:
                # 清理失敗不應影響模擬主流程
                pass

        # 清理檢查點
        cp_dir = self.workspace / RUNTIME_DIR / "checkpoints"
        if cp_dir.exists():
            cps = sorted(
                cp_dir.glob("checkpoint_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for cp in cps[self.config.persistence.checkpoint_max :]:
                stats["bytes_freed"] += cp.stat().st_size
                cp.unlink()
                stats["files_removed"] += 1

        # 清理 WAL 已完成記錄
        wal_dir = self.workspace / RUNTIME_DIR / "wal"
        if wal_dir.exists():
            wal = WriteAheadLog(self.workspace, self.config.persistence.wal_max_entries)
            cleaned = wal.clear_completed()
            stats["wal_cleaned"] = cleaned

        # 歸檔舊檔案
        archive_threshold = datetime.now() - timedelta(
            days=self.config.persistence.archive_after_days
        )
        archive_dir = self.workspace / RUNTIME_DIR / "archive"
        archive_dir.mkdir(exist_ok=True)

        if log_dir.exists():
            for log_file in log_dir.glob("*.log"):
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime < archive_threshold:
                    archive_path = archive_dir / f"{log_file.name}.gz"
                    temp_archive = archive_dir / f"{log_file.name}.tmp"
                    with open(log_file, "rb") as f_in:
                        with gzip.open(temp_archive, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    temp_archive.replace(archive_path)
                    log_file.unlink()

        return stats

    def disk_usage(self) -> dict[str, Any]:
        """獲取磁碟使用情況。"""
        total = 0
        counts: dict[str, int] = {}
        for path in self.workspace.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
                ext = path.suffix or "no_ext"
                counts[ext] = counts.get(ext, 0) + 1
        return {
            "total_bytes": total,
            "total_mb": round(total / 1024 / 1024, 2),
            "counts": counts,
        }


class SessionRecovery:
    """會話恢復上下文構建器。"""

    def __init__(
        self,
        workspace: Path,
        checkpoint: Checkpoint,
        wal: Optional[WriteAheadLog] = None,
    ):
        self.workspace = workspace
        self.checkpoint = checkpoint
        self.wal = wal

    def build_context(self, current_tick: int) -> str:
        """構建恢復上下文。"""
        parts = []

        latest = self.checkpoint.latest_tick()
        if latest is not None:
            parts.append(f"**Last Checkpoint**: tick {latest}")
            if latest < current_tick:
                parts.append(f"**Ticks Since**: {current_tick - latest}")

        ctx_path = self.workspace / "AGENT.md"
        if ctx_path.exists():
            content = ctx_path.read_text(encoding="utf-8")
            if content:
                parts.append(f"**Context**:\n{content[:1000]}")

        # 顯示 WAL pending 意圖
        if self.wal:
            pending = self.wal.get_pending_after_tick(latest or 0)
            if pending:
                parts.append(f"**Pending Intents**: {len(pending)}")

        state_summary = self._state_summary()
        if state_summary:
            parts.append(f"**State**:\n{state_summary}")

        return "\n\n".join(parts) if parts else ""

    def build_recovery_context(self, current_tick: int) -> str:
        """構建恢復上下文（build_context 的別名）。"""
        return self.build_context(current_tick)

    def _state_summary(self) -> str:
        """構建狀態摘要。"""
        summaries = []
        state_dir = self.workspace / "state"
        if not state_dir.exists():
            return ""

        for path in sorted(state_dir.glob("*.json")):
            try:
                data = jr_parse(path.read_text())
                key = path.stem
                for v in data.values():
                    if isinstance(v, str) and v:
                        summaries.append(f"- {key}: {v[:50]}")
                        break
            except Exception:
                pass

        return "\n".join(summaries)
