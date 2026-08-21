# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServer 工具函式."""

from typing import Any

from jiuwenclaw.common.schema.agent import AgentRequest


def get_chat_id(request: AgentRequest) -> str | None:
    """獲取請求的 Chat ID（平臺聊天標識）。

    優先使用頂層欄位，向後相容 metadata 方式。

    Args:
        request: AgentServer 請求物件

    Returns:
        平臺聊天標識（Chat ID），如果無法獲取則返回 None
    """
    # 1. 優先使用頂層欄位
    if request.chat_id:
        return request.chat_id

    # 2. 向後相容：從 metadata 獲取（優先順序按平臺）
    if request.metadata:
        return (
            request.metadata.get('feishu_chat_id') or
            request.metadata.get('wecom_chat_id') or
            request.metadata.get('dingtalk_chat_id') or
            request.metadata.get('xiaoyi_session_id')
        )
    return None
