# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 事件型別定義.

定義 Team 模式的事件型別常量和 SDK 事件型別對映。
"""

from enum import Enum

from openjiuwen.agent_teams.monitor.models import MonitorEventType


class TeamEventCategory(str, Enum):
    """Team 事件大類列舉.
    
    前端根據大類分別顯示在不同區域：
    - team.member: 成員事件區域
    - team.task: 任務事件區域
    - team.message: 訊息事件區域（需要記錄到歷史）
    """
    MEMBER = "team.member"
    TASK = "team.task"
    MESSAGE = "team.message"


class TeamEventType(str, Enum):
    """Team 事件型別列舉.
    
    命名規範: team.{category}.{action}
    - member: 成員相關事件
    - task: 任務相關事件
    - message: 訊息相關事件
    """
    
    # 成員事件
    MEMBER_SPAWNED = "team.member.spawned"
    MEMBER_STATUS_CHANGED = "team.member.status_changed"
    MEMBER_EXECUTION_CHANGED = "team.member.execution_changed"
    MEMBER_RESTARTED = "team.member.restarted"
    MEMBER_SHUTDOWN = "team.member.shutdown"
    
    # 任務事件
    TASK_CREATED = "team.task.created"
    TASK_CLAIMED = "team.task.claimed"
    TASK_COMPLETED = "team.task.completed"
    TASK_CANCELLED = "team.task.cancelled"
    TASK_UNBLOCKED = "team.task.unblocked"
    
    # 訊息事件
    MESSAGE_P2P = "team.message.p2p"
    MESSAGE_BROADCAST = "team.message.broadcast"


EVENT_TYPE_TO_CATEGORY: dict[TeamEventType, TeamEventCategory] = {
    # 成員事件
    TeamEventType.MEMBER_SPAWNED: TeamEventCategory.MEMBER,
    TeamEventType.MEMBER_STATUS_CHANGED: TeamEventCategory.MEMBER,
    TeamEventType.MEMBER_EXECUTION_CHANGED: TeamEventCategory.MEMBER,
    TeamEventType.MEMBER_RESTARTED: TeamEventCategory.MEMBER,
    TeamEventType.MEMBER_SHUTDOWN: TeamEventCategory.MEMBER,
    # 任務事件
    TeamEventType.TASK_CREATED: TeamEventCategory.TASK,
    TeamEventType.TASK_CLAIMED: TeamEventCategory.TASK,
    TeamEventType.TASK_COMPLETED: TeamEventCategory.TASK,
    TeamEventType.TASK_CANCELLED: TeamEventCategory.TASK,
    TeamEventType.TASK_UNBLOCKED: TeamEventCategory.TASK,
    # 訊息事件
    TeamEventType.MESSAGE_P2P: TeamEventCategory.MESSAGE,
    TeamEventType.MESSAGE_BROADCAST: TeamEventCategory.MESSAGE,
}

SDK_TO_TEAM_EVENT_MAP: dict[MonitorEventType, TeamEventType] = {
    MonitorEventType.MEMBER_SPAWNED: TeamEventType.MEMBER_SPAWNED,
    MonitorEventType.MEMBER_STATUS_CHANGED: TeamEventType.MEMBER_STATUS_CHANGED,
    MonitorEventType.MEMBER_EXECUTION_CHANGED: TeamEventType.MEMBER_EXECUTION_CHANGED,
    MonitorEventType.MEMBER_RESTARTED: TeamEventType.MEMBER_RESTARTED,
    MonitorEventType.MEMBER_SHUTDOWN: TeamEventType.MEMBER_SHUTDOWN,
    MonitorEventType.TASK_CREATED: TeamEventType.TASK_CREATED,
    MonitorEventType.TASK_CLAIMED: TeamEventType.TASK_CLAIMED,
    MonitorEventType.TASK_COMPLETED: TeamEventType.TASK_COMPLETED,
    MonitorEventType.TASK_CANCELLED: TeamEventType.TASK_CANCELLED,
    MonitorEventType.TASK_UNBLOCKED: TeamEventType.TASK_UNBLOCKED,
    MonitorEventType.MESSAGE: TeamEventType.MESSAGE_P2P,
    MonitorEventType.BROADCAST: TeamEventType.MESSAGE_BROADCAST,
}


def get_team_event_type(sdk_event_type: MonitorEventType) -> TeamEventType | None:
    """將 SDK 事件型別對映為 Team 事件型別.
    
    Args:
        sdk_event_type: SDK 的 MonitorEventType
        
    Returns:
        TeamEventType 或 None（如果未對映）
    """
    return SDK_TO_TEAM_EVENT_MAP.get(sdk_event_type)


def get_event_category(event_type: TeamEventType) -> TeamEventCategory:
    """獲取事件的大類.
    
    Args:
        event_type: Team 事件型別
        
    Returns:
        事件大類
    """
    return EVENT_TYPE_TO_CATEGORY.get(event_type, TeamEventCategory.MEMBER)


def is_message_event(event_type: TeamEventType) -> bool:
    """判斷是否為訊息事件（需要記錄到歷史）.
    
    Args:
        event_type: Team 事件型別
        
    Returns:
        是否為訊息事件
    """
    return EVENT_TYPE_TO_CATEGORY.get(event_type) == TeamEventCategory.MESSAGE
