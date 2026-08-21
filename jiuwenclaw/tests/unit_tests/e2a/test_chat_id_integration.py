# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""整合測試：驗證從 Channel 到 AgentServer 的 Chat ID 傳遞鏈路."""

import time

from jiuwenclaw.server.utils.utils import get_chat_id
from jiuwenclaw.common.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.common.e2a.gateway_normalize import message_to_e2a_or_fallback
from jiuwenclaw.common.schema.agent import AgentRequest
from jiuwenclaw.common.schema.message import Message, ReqMethod


def test_feishu_channel_to_agentserver_chat_id():
    """測試飛書 Channel → Gateway → AgentServer 的 Chat ID 傳遞"""
    # 1. 模擬飛書 Channel 建立 Message
    msg = Message(
        id="feishu_msg_123",
        type="req",
        channel_id="feishu",
        session_id="oc_aaaaaaa",
        params={"content": "你好"},
        timestamp=time.time(),
        ok=True,
        provider="feishu",
        chat_id="oc_aaaaaaa",  # 飛書 Chat ID
        user_id="ou_xxxxxxx",
        bot_id="cli_xxxxxxx",
        req_method=ReqMethod.CHAT_SEND,
        is_stream=True,
        metadata={
            "message_id": "feishu_msg_123",
            "chat_type": "p2p",
            "msg_type": "text",
            "open_id": "ou_xxxxxxx",
            "feishu_open_id": "ou_xxxxxxx",
            "feishu_chat_id": "oc_aaaaaaa",
        },
    )

    # 2. Gateway: Message → E2AEnvelope
    env = message_to_e2a_or_fallback(msg)
    assert env.request_id == "feishu_msg_123"
    assert env.channel == "feishu"
    assert env.chat_id == "oc_aaaaaaa"

    # 3. AgentServer: E2AEnvelope → AgentRequest
    request = e2a_to_agent_request(env)
    assert request.request_id == "feishu_msg_123"
    assert request.channel_id == "feishu"
    assert request.chat_id == "oc_aaaaaaa"

    # 4. 驗證 get_chat_id() 能正確獲取
    chat_id = get_chat_id(request)
    assert chat_id == "oc_aaaaaaa"


def test_wecom_channel_to_agentserver_chat_id():
    """測試企微 Channel → Gateway → AgentServer 的 Chat ID 傳遞"""
    # 1. 模擬企微 Channel 建立 Message
    msg = Message(
        id="wecom_msg_456",
        type="req",
        channel_id="wecom",
        session_id="wecom_chatid",
        params={"content": "你好", "query": "你好"},
        timestamp=time.time(),
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        is_stream=True,
        chat_id="wecom_chatid",  # 企微 Chat ID
        metadata={
            "wecom_chat_id": "wecom_chatid",
            "wecom_req_id": "wecom_msg_456",
        },
    )

    # 2. Gateway: Message → E2AEnvelope
    env = message_to_e2a_or_fallback(msg)
    assert env.request_id == "wecom_msg_456"
    assert env.channel == "wecom"
    assert env.chat_id == "wecom_chatid"

    # 3. AgentServer: E2AEnvelope → AgentRequest
    request = e2a_to_agent_request(env)
    assert request.request_id == "wecom_msg_456"
    assert request.channel_id == "wecom"
    assert request.chat_id == "wecom_chatid"

    # 4. 驗證 get_chat_id() 能正確獲取
    chat_id = get_chat_id(request)
    assert chat_id == "wecom_chatid"


def test_dingtalk_channel_to_agentserver_chat_id():
    """測試釘釘 Channel → Gateway → AgentServer 的 Chat ID 傳遞"""
    # 1. 模擬釘釘 Channel 建立 Message
    msg = Message(
        id="dingtalk_msg_789",
        type="req",
        channel_id="dingtalk",
        session_id="dingtalk_sender_id",
        params={"content": "你好", "query": "你好"},
        timestamp=time.time(),
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        chat_id="dingtalk_conversation_id",  # 釘釘 Chat ID
        metadata={
            "conversation_id": "dingtalk_conversation_id",
            "conversation_type": "1",
            "dingtalk_chat_id": "dingtalk_conversation_id",
            "dingtalk_sender_id": "dingtalk_sender_id",
            "sender_name": "張三",
        },
    )

    # 2. Gateway: Message → E2AEnvelope
    env = message_to_e2a_or_fallback(msg)
    assert env.request_id == "dingtalk_msg_789"
    assert env.channel == "dingtalk"
    assert env.chat_id == "dingtalk_conversation_id"

    # 3. AgentServer: E2AEnvelope → AgentRequest
    request = e2a_to_agent_request(env)
    assert request.request_id == "dingtalk_msg_789"
    assert request.channel_id == "dingtalk"
    assert request.chat_id == "dingtalk_conversation_id"

    # 4. 驗證 get_chat_id() 能正確獲取
    chat_id = get_chat_id(request)
    assert chat_id == "dingtalk_conversation_id"


def test_xiaoyi_channel_to_agentserver_chat_id():
    """測試小藝 Channel → Gateway → AgentServer 的 Chat ID 傳遞"""
    # 1. 模擬小藝 Channel 建立 Message
    msg = Message(
        id="xiaoyi_msg_012",
        type="req",
        channel_id="xiaoyi",
        session_id="xiaoyi_session_id",
        params={"query": "幫我查天氣"},
        timestamp=time.time(),
        is_stream=True,
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        chat_id="xiaoyi_session_id",  # 小藝 Chat ID
        metadata={
            "method": "message/stream",
            "xiaoyi_session_id": "xiaoyi_session_id",
            "xiaoyi_task_id": "xiaoyi_task_id",
        },
    )

    # 2. Gateway: Message → E2AEnvelope
    env = message_to_e2a_or_fallback(msg)
    assert env.request_id == "xiaoyi_msg_012"
    assert env.channel == "xiaoyi"
    assert env.chat_id == "xiaoyi_session_id"

    # 3. AgentServer: E2AEnvelope → AgentRequest
    request = e2a_to_agent_request(env)
    assert request.request_id == "xiaoyi_msg_012"
    assert request.channel_id == "xiaoyi"
    assert request.chat_id == "xiaoyi_session_id"

    # 4. 驗證 get_chat_id() 能正確獲取
    chat_id = get_chat_id(request)
    assert chat_id == "xiaoyi_session_id"


def test_all_channels_chat_id_unified_interface():
    """測試所有平臺透過統一介面 get_chat_id() 獲取 Chat ID"""
    # 構建四個平臺的 AgentRequest
    requests = {
        "feishu": AgentRequest(
            request_id="feishu",
            channel_id="feishu",
            session_id="s1",
            chat_id="oc_feishu",
            req_method=ReqMethod.CHAT_SEND,
            metadata={"feishu_chat_id": "oc_feishu"},
        ),
        "wecom": AgentRequest(
            request_id="wecom",
            channel_id="wecom",
            session_id="s2",
            chat_id="wecom_chat",
            req_method=ReqMethod.CHAT_SEND,
            metadata={"wecom_chat_id": "wecom_chat"},
        ),
        "dingtalk": AgentRequest(
            request_id="dingtalk",
            channel_id="dingtalk",
            session_id="s3",
            chat_id="dingtalk_chat",
            req_method=ReqMethod.CHAT_SEND,
            metadata={"dingtalk_chat_id": "dingtalk_chat"},
        ),
        "xiaoyi": AgentRequest(
            request_id="xiaoyi",
            channel_id="xiaoyi",
            session_id="s4",
            chat_id="xiaoyi_session",
            req_method=ReqMethod.CHAT_SEND,
            metadata={"xiaoyi_session_id": "xiaoyi_session"},
        ),
    }

    # 驗證所有平臺都能透過統一介面獲取 Chat ID
    expected = {
        "feishu": "oc_feishu",
        "wecom": "wecom_chat",
        "dingtalk": "dingtalk_chat",
        "xiaoyi": "xiaoyi_session",
    }

    for platform, request in requests.items():
        chat_id = get_chat_id(request)
        assert chat_id == expected.get(platform), f"{platform} 的 Chat ID 不正確"


def test_metadata_fallback_when_chat_id_missing():
    """測試頂層 chat_id 缺失時，能正確從 metadata 獲取"""
    # 測試場景：只有 metadata，沒有頂層 chat_id
    request = AgentRequest(
        request_id="fallback_test",
        channel_id="wecom",
        session_id="s1",
        chat_id=None,  # 頂層欄位為空
        req_method=ReqMethod.CHAT_SEND,
        metadata={"wecom_chat_id": "fallback_chatid"},
    )

    # 應該從 metadata 獲取
    chat_id = get_chat_id(request)
    assert chat_id == "fallback_chatid"


def test_top_level_priority_over_metadata():
    """測試頂層 chat_id 優先於 metadata"""
    request = AgentRequest(
        request_id="priority_test",
        channel_id="feishu",
        session_id="s1",
        chat_id="top_level_id",  # 頂層欄位
        req_method=ReqMethod.CHAT_SEND,
        metadata={"feishu_chat_id": "metadata_id"},  # metadata
    )

    # 應該優先使用頂層欄位
    chat_id = get_chat_id(request)
    assert chat_id == "top_level_id"
