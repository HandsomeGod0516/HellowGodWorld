from jiuwenclaw.common.schema.event_base import HookEventBase


class GatewayHookEvents(HookEventBase):
    """Gateway 和 AgentServer 互動事件

    這些事件定義了 Gateway 與 AgentServer 之間的訊息傳遞生命週期。
    """

    scope: str = "gateway"

    GATEWAY_STARTED = HookEventBase.get_event("gateway_started")
    GATEWAY_STOPPED = HookEventBase.get_event("gateway_stopped")
    BEFORE_CHAT_REQUEST = HookEventBase.get_event("before_chat_request")


class AgentServerHookEvents(HookEventBase):
    """AgentServer 事件

    這些事件定義了 AgentServer 的內部事件。
    """

    scope: str = "agent_server"

    AGENT_SERVER_STARTED = HookEventBase.get_event("agent_server_started")
    AGENT_SERVER_STOPPED = HookEventBase.get_event("agent_server_stopped")
    BEFORE_CHAT_REQUEST = HookEventBase.get_event("before_chat_request")
    MEMORY_BEFORE_CHAT = HookEventBase.get_event("memory_before_chat")
    MEMORY_AFTER_CHAT = HookEventBase.get_event("memory_after_chat")
    BEFORE_SYSTEM_PROMPT_BUILD = HookEventBase.get_event("before_system_prompt_build")
