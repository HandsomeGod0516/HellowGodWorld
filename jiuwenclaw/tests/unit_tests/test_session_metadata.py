"""session_metadata 模組單元測試"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: mock get_agent_sessions_dir 指向 tmp_path
# ---------------------------------------------------------------------------
@pytest.fixture()
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(
        "jiuwenclaw.server.runtime.session.session_metadata.get_agent_sessions_dir",
        lambda: d,
    )
    return d



def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# _auto_title
# ===========================================================================
class TestAutoTitle:
    @staticmethod
    def test_normal():
        from jiuwenclaw.server.runtime.session.session_metadata import _auto_title

        assert _auto_title("hello world") == "hello world"

    @staticmethod
    def test_truncate():
        from jiuwenclaw.server.runtime.session.session_metadata import _auto_title, _TITLE_MAX_LEN

        long_text = "a" * 100
        result = _auto_title(long_text)
        assert len(result) == _TITLE_MAX_LEN + 3  # +3 for "..."
        assert result.endswith("...")

    @staticmethod
    def test_strip_and_newline():
        from jiuwenclaw.server.runtime.session.session_metadata import _auto_title

        assert _auto_title("  line1\nline2  ") == "line1 line2"

    @staticmethod
    def test_empty():
        from jiuwenclaw.server.runtime.session.session_metadata import _auto_title

        assert _auto_title("") == ""
        assert _auto_title("   ") == ""


# ===========================================================================
# init_session_metadata
# ===========================================================================
class TestInitSessionMetadata:
    @staticmethod
    def test_creates_metadata_file(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import init_session_metadata

        init_session_metadata(
            session_id="sess_001",
            channel_id="web",
            user_id="user_1",
            title="test title",
        )
        meta_path = sessions_dir / "sess_001" / "metadata.json"
        assert meta_path.exists()

        data = _read_json(meta_path)
        assert data["session_id"] == "sess_001"
        assert data["channel_id"] == "web"
        assert data["user_id"] == "user_1"
        assert data["title"] == "test title"
        assert data["message_count"] == 0
        assert data["mode"] == "unknown"
        assert isinstance(data["created_at"], float)
        assert isinstance(data["last_message_at"], float)

    @staticmethod
    def test_default_empty_fields(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import init_session_metadata

        init_session_metadata(session_id="sess_002")
        data = _read_json(sessions_dir / "sess_002" / "metadata.json")
        assert data["channel_id"] == ""
        assert data["user_id"] == ""
        assert data["title"] == ""
        assert data["mode"] == "unknown"


# ===========================================================================
# update_session_metadata
# ===========================================================================
class TestUpdateSessionMetadata:
    @staticmethod
    def test_update_existing(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_u1", channel_id="web")

        update_session_metadata(
            session_id="sess_u1",
            channel_id="feishu",
            increment_message_count=True,
        )
        # 等待非同步佇列寫入完成
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_u1" / "metadata.json")
        assert data["channel_id"] == "feishu"
        assert data["message_count"] == 1

    @staticmethod
    def test_fallback_create_when_no_metadata(sessions_dir):
        """外部渠道隱式建立 session 時,metadata 不存在,應自動建立"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        # 不呼叫 init,直接 update — 模擬外部渠道場景
        (sessions_dir / "sess_ext").mkdir()
        update_session_metadata(
            session_id="sess_ext",
            channel_id="telegram",
            user_id="tg_user",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_ext" / "metadata.json")
        assert data["session_id"] == "sess_ext"
        assert data["channel_id"] == "telegram"
        assert data["user_id"] == "tg_user"
        assert data["message_count"] == 0

    @staticmethod
    def test_auto_title_on_first_user_message(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_at")  # title 為空

        update_session_metadata(
            session_id="sess_at",
            user_content="幫我寫一個排序演算法",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_at" / "metadata.json")
        assert data["title"] == "幫我寫一個排序演算法"

    @staticmethod
    def test_no_overwrite_existing_title(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_nt", title="原始標題")

        update_session_metadata(
            session_id="sess_nt",
            user_content="新訊息內容",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_nt" / "metadata.json")
        assert data["title"] == "原始標題"  # 不被覆蓋

    @staticmethod
    def test_increment_message_count_multiple(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_mc")
        for _ in range(3):
            update_session_metadata(
                session_id="sess_mc", increment_message_count=True
            )
            _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_mc" / "metadata.json")
        assert data["message_count"] == 3


# ===========================================================================
# get_session_metadata
# ===========================================================================
class TestGetSessionMetadata:
    @staticmethod
    def test_returns_data(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_session_metadata,
        )

        init_session_metadata(session_id="sess_g1", channel_id="web")
        data = get_session_metadata("sess_g1")
        assert data["channel_id"] == "web"

    @staticmethod
    def test_returns_empty_when_missing(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import get_session_metadata

        data = get_session_metadata("nonexistent")
        assert data == {}


# ===========================================================================
# get_all_sessions_metadata
# ===========================================================================
class TestGetAllSessionsMetadata:
    @staticmethod
    def test_basic_list(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_all_sessions_metadata,
        )

        init_session_metadata(session_id="s1", channel_id="web")
        init_session_metadata(session_id="s2", channel_id="feishu")

        sessions, total = get_all_sessions_metadata()
        assert total == 2
        assert len(sessions) == 2
        ids = {s["session_id"] for s in sessions}
        assert ids == {"s1", "s2"}

    @staticmethod
    def test_sorted_by_last_message_at(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            _write_metadata_sync,
            get_all_sessions_metadata,
        )

        now = time.time()
        _write_metadata_sync("old", {
            "session_id": "old", "last_message_at": now - 100,
            "channel_id": "", "user_id": "", "created_at": now - 100,
            "title": "", "message_count": 0,
        })
        _write_metadata_sync("new", {
            "session_id": "new", "last_message_at": now,
            "channel_id": "", "user_id": "", "created_at": now,
            "title": "", "message_count": 0,
        })

        sessions, _ = get_all_sessions_metadata()
        assert sessions[0]["session_id"] == "new"
        assert sessions[1]["session_id"] == "old"

    @staticmethod
    def test_pagination_limit(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            get_all_sessions_metadata,
        )

        for i in range(5):
            init_session_metadata(session_id=f"p{i}")

        sessions, total = get_all_sessions_metadata(limit=2)
        assert total == 5
        assert len(sessions) == 2

    @staticmethod
    def test_pagination_offset(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            _write_metadata_sync,
            get_all_sessions_metadata,
        )

        now = time.time()
        for i in range(5):
            _write_metadata_sync(f"o{i}", {
                "session_id": f"o{i}", "last_message_at": now - i,
                "channel_id": "", "user_id": "", "created_at": now - i,
                "title": "", "message_count": 0,
            })

        sessions, total = get_all_sessions_metadata(limit=2, offset=2)
        assert total == 5
        assert len(sessions) == 2
        # offset=2 跳過前2個,取第3和第4個(按 last_message_at 倒序)
        assert sessions[0]["session_id"] == "o2"
        assert sessions[1]["session_id"] == "o3"

    @staticmethod
    def test_fallback_for_old_sessions(sessions_dir):
        """沒有 metadata.json 的舊會話應用目錄時間戳構造最小資訊"""
        from jiuwenclaw.server.runtime.session.session_metadata import get_all_sessions_metadata

        (sessions_dir / "legacy_sess").mkdir()
        # 不寫 metadata.json

        sessions, total = get_all_sessions_metadata()
        assert total == 1
        assert sessions[0]["session_id"] == "legacy_sess"
        assert sessions[0]["title"] == ""
        assert sessions[0]["mode"] == "unknown"
        assert sessions[0]["created_at"] > 0

    @staticmethod
    def test_empty_dir(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import get_all_sessions_metadata

        sessions, total = get_all_sessions_metadata()
        assert total == 0
        assert sessions == []


# ===========================================================================
# _read_metadata 容錯
# ===========================================================================
class TestReadMetadataRobustness:
    @staticmethod
    def test_corrupted_json(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import get_session_metadata

        d = sessions_dir / "sess_bad"
        d.mkdir()
        (d / "metadata.json").write_text("not valid json", encoding="utf-8")

        data = get_session_metadata("sess_bad")
        assert data == {}

    @staticmethod
    def test_non_dict_json(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import get_session_metadata

        d = sessions_dir / "sess_list"
        d.mkdir()
        (d / "metadata.json").write_text("[1,2,3]", encoding="utf-8")

        data = get_session_metadata("sess_list")
        assert data == {}


# ===========================================================================
# channel_metadata
# ===========================================================================
class TestChannelMetadata:
    @staticmethod
    def test_first_request_metadata_stored(sessions_dir):
        """首次請求的 metadata 應寫入 channel_metadata"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        update_session_metadata(
            session_id="sess_meta",
            channel_id="web",
            channel_metadata={"traceparent": "00-abc-123-01", "feishu_chat_id": "oc_xxx"},
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_meta" / "metadata.json")
        assert data["channel_metadata"]["traceparent"] == "00-abc-123-01"
        assert data["channel_metadata"]["feishu_chat_id"] == "oc_xxx"

    @staticmethod
    def test_no_overwrite_existing_metadata(sessions_dir):
        """已存在的 channel_metadata 不應被覆蓋"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            _write_metadata_sync,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        _write_metadata_sync("sess_no", {
            "session_id": "sess_no",
            "channel_id": "web",
            "user_id": "",
            "created_at": 1000.0,
            "last_message_at": 1000.0,
            "title": "",
            "message_count": 0,
            "channel_metadata": {"traceparent": "original"},
        })

        update_session_metadata(
            session_id="sess_no",
            channel_metadata={"traceparent": "new_value"},
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_no" / "metadata.json")
        assert data["channel_metadata"]["traceparent"] == "original"  # 未被覆蓋

    @staticmethod
    def test_empty_metadata_not_stored(sessions_dir):
        """空 metadata 不寫入欄位"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        update_session_metadata(
            session_id="sess_empty",
            channel_id="web",
            channel_metadata=None,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_empty" / "metadata.json")
        assert "channel_metadata" not in data

    @staticmethod
    def test_backfill_when_missing(sessions_dir):
        """首次未寫入時，後續可補充"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            update_session_metadata,
            _METADATA_QUEUE,
        )

        # 首次不帶 metadata
        update_session_metadata(session_id="sess_backfill", channel_id="web")
        _METADATA_QUEUE.join()

        # 二次補充 metadata
        update_session_metadata(
            session_id="sess_backfill",
            channel_metadata={"traceparent": "backfilled"},
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_backfill" / "metadata.json")
        assert data["channel_metadata"]["traceparent"] == "backfilled"


# ===========================================================================
# delivery_context
# ===========================================================================
class TestDeliveryContext:
    @staticmethod
    def test_delivery_context_can_refresh_route_metadata(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            _METADATA_QUEUE,
            get_session_delivery_context,
            set_session_delivery_context,
        )

        set_session_delivery_context(
            session_id="sess_delivery",
            channel_id="feishu",
            source_request_id="req-1",
            route_metadata={"feishu_chat_id": "oc_old"},
        )
        _METADATA_QUEUE.join()

        set_session_delivery_context(
            session_id="sess_delivery",
            channel_id="feishu",
            source_request_id="req-2",
            route_metadata={"feishu_chat_id": "oc_new"},
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_delivery" / "metadata.json")
        context = get_session_delivery_context("sess_delivery")

        assert data["delivery_context"]["source_request_id"] == "req-2"
        assert data["delivery_context"]["route_metadata"]["feishu_chat_id"] == "oc_new"
        assert context is not None
        assert context["channel_id"] == "feishu"
        assert context["route_metadata"]["feishu_chat_id"] == "oc_new"

    @staticmethod
    def test_delivery_context_keeps_previous_route_metadata_when_new_request_has_none(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            _METADATA_QUEUE,
            get_session_delivery_context,
            set_session_delivery_context,
        )

        set_session_delivery_context(
            session_id="sess_delivery_keep",
            channel_id="wecom",
            source_request_id="req-1",
            route_metadata={"conversation_id": "conv-1"},
        )
        _METADATA_QUEUE.join()

        set_session_delivery_context(
            session_id="sess_delivery_keep",
            channel_id="wecom",
            source_request_id="req-2",
            route_metadata=None,
        )
        _METADATA_QUEUE.join()

        context = get_session_delivery_context("sess_delivery_keep")
        assert context is not None
        assert context["source_request_id"] == "req-2"
        assert context["route_metadata"]["conversation_id"] == "conv-1"

    @staticmethod
    def test_build_server_push_message_uses_saved_delivery_context(sessions_dir):
        from jiuwenclaw.server.runtime.session.session_metadata import (
            _METADATA_QUEUE,
            build_server_push_message,
            set_session_delivery_context,
        )

        set_session_delivery_context(
            session_id="sess_push",
            channel_id="telegram",
            source_request_id="req-origin",
            route_metadata={"telegram_chat_id": "chat-1"},
        )
        _METADATA_QUEUE.join()

        push = build_server_push_message(
            session_id="sess_push",
            request_id="push-1",
            payload={"event_type": "chat.ask_user_question"},
            fallback_channel_id="web",
        )

        assert push["channel_id"] == "telegram"
        assert push["session_id"] == "sess_push"
        assert push["metadata"]["telegram_chat_id"] == "chat-1"


# ===========================================================================
# 需求驗證: 會話標題穩定性
# ===========================================================================
class TestTitleStability:
    """驗證兩個核心需求:
    1. 首條使用者訊息自動生成標題，後續訊息不改變
    2. 標題一旦建立就不再變化
    """

    @staticmethod
    def test_req1_first_message_sets_title_second_does_not(sessions_dir):
        """需求1: 首條訊息設定標題，第二條訊息不改變標題"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        # 模擬 web 前端建立會話(無標題)
        init_session_metadata(session_id="sess_req1")

        # 第一條使用者訊息
        update_session_metadata(
            session_id="sess_req1",
            channel_id="web",
            increment_message_count=True,
            user_content="第一條訊息應該成為標題",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_req1" / "metadata.json")
        assert data["title"] == "第一條訊息應該成為標題"

        # 第一條助手回覆
        update_session_metadata(
            session_id="sess_req1",
            channel_id="web",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_req1" / "metadata.json")
        assert data["title"] == "第一條訊息應該成為標題", "助手回覆不應覆蓋標題"

        # 第二條使用者訊息(模擬隔1分鐘後)
        update_session_metadata(
            session_id="sess_req1",
            channel_id="web",
            increment_message_count=True,
            user_content="第二條訊息不應改變標題",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_req1" / "metadata.json")
        assert data["title"] == "第一條訊息應該成為標題", "第二條使用者訊息不應覆蓋標題"
        assert data["message_count"] == 3

    @staticmethod
    def test_req1_rapid_user_then_assistant_no_race(sessions_dir):
        """需求1(競態): 使用者訊息和助手訊息快速連續到達時，標題不被覆蓋"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_race")

        # 模擬真實場景: 使用者訊息和助手訊息不等非同步寫入就連續呼叫
        # 不呼叫 _METADATA_QUEUE.join()，模擬非同步寫入未完成
        update_session_metadata(
            session_id="sess_race",
            channel_id="web",
            increment_message_count=True,
            user_content="使用者的第一條訊息",
        )
        # 助手立即回覆(不等使用者訊息的非同步寫入落盤)
        update_session_metadata(
            session_id="sess_race",
            channel_id="web",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_race" / "metadata.json")
        assert data["title"] == "使用者的第一條訊息", \
            "競態條件: 助手訊息的非同步寫入不應覆蓋使用者訊息生成的標題"
        assert data["message_count"] == 2

    @staticmethod
    def test_req2_title_immutable_after_creation(sessions_dir):
        """需求2: 標題一旦建立就不再改變，即使後續多輪對話"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_immut")

        # 第1輪
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
            user_content="最初的標題",
        )
        _METADATA_QUEUE.join()
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        # 第2輪
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
            user_content="第二輪訊息",
        )
        _METADATA_QUEUE.join()
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
        )
        _METADATA_QUEUE.join()

        # 第3輪
        update_session_metadata(
            session_id="sess_immut",
            increment_message_count=True,
            user_content="第三輪訊息",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_immut" / "metadata.json")
        assert data["title"] == "最初的標題", "多輪對話後標題仍保持不變"
        assert data["message_count"] == 5

    @staticmethod
    def test_req2_explicit_empty_title_does_not_clear(sessions_dir):
        """需求2: 即使傳入空字串 title 引數，也不應清除已有標題"""
        from jiuwenclaw.server.runtime.session.session_metadata import (
            init_session_metadata,
            update_session_metadata,
            _METADATA_QUEUE,
        )

        init_session_metadata(session_id="sess_noclear", title="已有標題")

        # 模擬某處傳入 title=""
        update_session_metadata(
            session_id="sess_noclear",
            title="",
        )
        _METADATA_QUEUE.join()

        data = _read_json(sessions_dir / "sess_noclear" / "metadata.json")
        assert data["title"] == "已有標題", "空字串不應清除已有標題"
