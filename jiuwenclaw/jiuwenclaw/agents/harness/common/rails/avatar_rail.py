# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AvatarPromptRail - 數字分身 Rail.

處理所有 per-request 的 avatar 邏輯：
1. before_model_call: 根據 ContextVar 動態注入/移除 avatar 相關 PromptSection
2. before_tool_call: 攔截群聊記憶禁寫 + enable_memory=False 場景
"""

from __future__ import annotations

from typing import Any, Optional, Set

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agents.harness.common.rails.permissions.owner_scopes import (
    TOOL_PERMISSION_CONTEXT,
    PermissionContext,
)
from jiuwenclaw.common.utils import logger

_MEMORY_WRITE_TOOLS = frozenset({"write_memory", "edit_memory"})

_AVATAR_PROMPT_PRIORITY = 110


class AvatarPromptRail(DeepAgentRail):
    """數字分身 Rail — 處理所有 per-request 的 avatar 邏輯。

    職責:
    1. before_model_call: 根據 ContextVar 動態注入/移除 avatar 相關 PromptSection
    2. before_tool_call: 攔截群聊記憶禁寫 + enable_memory=False 場景
    """

    priority: int = 85

    def __init__(self) -> None:
        super().__init__()
        self._injected_sections: set[str] = set()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        builder = getattr(
            getattr(self, "_deep_agent", None) or ctx.agent,
            "system_prompt_builder",
            None,
        )
        if builder is None:
            return

        for name in list(self._injected_sections):
            builder.remove_section(name)
        self._injected_sections.clear()

        perm_ctx = TOOL_PERMISSION_CONTEXT.get()
        if perm_ctx is None:
            return

        language = getattr(builder, "language", "cn") or "cn"

        # 數字分身身份提示詞（僅群聊數字分身模式）
        if perm_ctx.group_digital_avatar and perm_ctx.avatar_mode:
            display_name = perm_ctx.avatar_principal_name or perm_ctx.principal_user_id
            avatar_content = _build_avatar_prompt(display_name, language)
            section = PromptSection(
                name="avatar_identity",
                content={language: avatar_content},
                priority=_AVATAR_PROMPT_PRIORITY,
            )
            builder.add_section(section)
            self._injected_sections.add("avatar_identity")

        # 判斷是否為群聊數字分身模式（三個條件同時滿足）
        is_group_digital_avatar = (
            perm_ctx.group_digital_avatar
            and perm_ctx.avatar_mode
        )

        # 群聊數字分身模式：禁止寫入記憶
        if is_group_digital_avatar:
            notice = (
                "\n[群聊模式：禁止呼叫 write_memory/edit_memory]\n"
                if language == "cn"
                else "\n[Group chat mode: write_memory/edit_memory calls are prohibited]\n"
            )
            section = PromptSection(
                name="group_chat_memory_notice",
                content={language: notice},
                priority=_AVATAR_PROMPT_PRIORITY + 1,
            )
            builder.add_section(section)
            self._injected_sections.add("group_chat_memory_notice")

        # 記憶完全禁用（三個條件同時滿足：enable_memory=False + group_digital_avatar=True + 群聊訊息）
        should_disable_memory = (
            not perm_ctx.enable_memory
            and perm_ctx.group_digital_avatar
            and perm_ctx.avatar_mode
        )
        if should_disable_memory:
            # 使用完全禁用提示詞（禁止讀取和寫入）
            disabled_content = _build_memory_fully_disabled_prompt(language)
            section = PromptSection(
                name="memory_fully_disabled",
                content={language: disabled_content},
                priority=_AVATAR_PROMPT_PRIORITY + 2,
            )
            builder.add_section(section)
            self._injected_sections.add("memory_fully_disabled")

        try:
            from jiuwenclaw.agents.harness.common.memory.forbidden import get_forbidden_memory_prompt
            forbidden = get_forbidden_memory_prompt(language)
            if forbidden:
                section = PromptSection(
                    name="forbidden_memory",
                    content={language: forbidden},
                    priority=_AVATAR_PROMPT_PRIORITY + 3,
                )
                builder.add_section(section)
                self._injected_sections.add("forbidden_memory")
        except Exception as e:
            logger.debug("[AvatarRail] 載入 forbidden_memory 失敗: %s", e)

        if is_group_digital_avatar:
            interaction_content = _build_interaction_prompt(language)
            section = PromptSection(
                name="interaction_guidance",
                content={language: interaction_content},
                priority=_AVATAR_PROMPT_PRIORITY + 4,
            )
            builder.add_section(section)
            self._injected_sections.add("interaction_guidance")

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """攔截記憶工具呼叫。

        不依賴 _tool_names 白名單，直接檢查所有工具。
        由於 DeepAgentRail.before_tool_call 沒有白名單過濾，所有工具呼叫都會經過這裡。

        處理兩種場景：
        1. 群聊數字分身模式（group_digital_avatar=True + avatar_mode=True）：禁止寫入記憶，但允許讀取
        2. 記憶完全禁用（enable_memory=False + group_digital_avatar=True + avatar_mode=True）：禁止讀取和寫入記憶
        """
        tool_name = ctx.inputs.tool_name
        perm_ctx = TOOL_PERMISSION_CONTEXT.get()
        if perm_ctx is None:
            return

        # 判斷是否為群聊數字分身模式
        is_group_digital_avatar = (
            perm_ctx.group_digital_avatar
            and perm_ctx.avatar_mode
        )

        # 判斷是否為記憶完全禁用（三個條件同時滿足）
        should_disable_memory = (
            not perm_ctx.enable_memory
            and perm_ctx.group_digital_avatar
            and perm_ctx.avatar_mode
        )

        # 場景2：記憶完全禁用 - 禁止讀取和寫入
        if should_disable_memory:
            all_memory_tools = frozenset({
                "write_memory", "edit_memory", "read_memory", "memory_search", "memory_get"
            })
            if tool_name in all_memory_tools:
                self._reject_tool(ctx, "[PERMISSION_DENIED] 記憶系統已禁用，禁止訪問")
            return

        # 場景1：群聊數字分身模式 - 只禁止寫入
        if is_group_digital_avatar and tool_name in _MEMORY_WRITE_TOOLS:
            self._reject_tool(ctx, "[PERMISSION_DENIED] 群聊模式下禁止寫入/編輯記憶檔案")
            return

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, message: str) -> None:
        """跳過工具執行，直接返回拒絕訊息。"""
        tool_call = ctx.inputs.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = message
        ctx.inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)


def _build_avatar_prompt(principal_user_id: str | None, language: str) -> str:
    """數字分身身份提示詞。文案複用自 agentserver/prompt_builder.py 的 _avatar_prompt()。"""
    if language == "cn":
        if principal_user_id:
            identity = f"你當前正在群聊場景中作為 **{principal_user_id}** 的數字分身發言。"
            perspective = f"1. **第一人稱視角**：始終以 {principal_user_id} 的身份和口吻回覆，使用\"我\"而非\"他/她\"。"
            boundary = f"2. **承諾邊界**：不要代替 {principal_user_id} 做出超出已知資訊範圍的承諾或決定。如果上下文不足，坦誠說明需要確認，而非編造事實。"
        else:
            identity = "你當前正在群聊場景中作為使用者本人的數字分身發言。"
            perspective = "1. **第一人稱視角**：始終以使用者本人的身份和口吻回覆，使用\"我\"而非\"他/她\"。"
            boundary = "2. **承諾邊界**：不要代替使用者做出超出已知資訊範圍的承諾或決定。如果上下文不足，坦誠說明需要確認，而非編造事實。"
        return f"""---

# 數字分身模式

{identity}以下規則**僅約束你在群聊中的對外回覆方式**，不影響你的任何內部能力。

## 回覆風格約束

{perspective}
{boundary}
3. **自然簡潔**：回覆風格應貼近真人在群聊中的表達習慣，簡短、自然，避免過度正式或冗長。
4. **不暴露身份**：不要提及自己是 AI、數字分身或機器人。

## 能力不受影響

數字分身模式**不限制**你的任何 agent 能力，以下功能照常執行：
- **工具呼叫**：所有工具（檔案操作、搜尋、程式碼執行等）正常使用
- **待辦管理**：todo_create / todo_complete / todo_insert 等正常使用
- **定時任務**：可以正常建立並執行 cron 任務和 heartbeat 任務
- **技能呼叫**：所有已註冊技能正常使用
"""
    if principal_user_id:
        identity = f"You are currently acting as **{principal_user_id}**'s digital avatar in a group chat."
        perspective = f"1. **First-person perspective**: Always reply as {principal_user_id},\
             using \"I\" instead of \"he/she\"."
        boundary = f"2. **Commitment boundary**: Do not make commitments or decisions beyond \
            known information on behalf of {principal_user_id}."
    else:
        identity = "You are currently acting as the user's digital avatar in a group chat."
        perspective = "1. **First-person perspective**: Always reply as the user, using \"I\" instead of \"he/she\"."
        boundary = "2. **Commitment boundary**: Do not make commitments or decisions beyond known \
            information on behalf of the user."
    return f"""---

# Digital Avatar Mode

{identity} The rules below **only constrain your outward reply style** in group chat.

## Reply Style Constraints

{perspective}
{boundary}
3. **Natural and concise**: Reply style should resemble a real person's expression in group chat.
4. **Do not reveal identity**: Never mention that you are an AI, digital avatar, or bot.
"""


def _build_memory_disabled_prompt(language: str) -> str:
    """記憶寫入禁用提示詞（保留讀能力，與 React 鏈路行為一致）。"""
    if language == "cn":
        return """## 記憶系統 - 寫入已禁用

**記憶寫入功能當前已禁用。**

- **禁止** 使用 write_memory、edit_memory 寫入或修改記憶檔案
- **允許** 使用 memory_search、memory_get、read_memory 查詢已有記憶
- 如果使用者要求記住某些內容，回覆："記憶寫入功能當前未啟用，無法儲存新資訊，但我可以查詢已有的記憶。"
"""
    return """## Memory System - Write Disabled

**Memory write operations are currently disabled.**

- **Do NOT** use write_memory or edit_memory to write or modify memory files
- **Allowed**: memory_search, memory_get, read_memory for reading existing memories
- If the user asks to remember something, reply: "Memory writing is currently disabled, but I can query existing memories."
"""


def _build_memory_fully_disabled_prompt(language: str) -> str:
    """記憶完全禁用提示詞（禁止讀取和寫入）。"""
    if language == "cn":
        return """## 記憶系統 - 已完全禁用

**記憶系統當前已完全禁用。**

- **禁止** 使用任何記憶工具：
  - 寫入工具：write_memory、edit_memory
  - 讀取工具：read_memory、memory_search、memory_get
- 如果使用者詢問歷史資訊或要求記住某些內容，回覆："記憶系統當前已禁用，我無法訪問歷史記錄或儲存新資訊。"
"""
    return """## Memory System - Fully Disabled

**The memory system is currently fully disabled.**

- **Do NOT** use any memory tools:
  - Write tools: write_memory, edit_memory
  - Read tools: read_memory, memory_search, memory_get
- If the user asks about historical information or requests to remember something, reply: \
    "The memory system is currently disabled. I cannot access historical records or save new information."
"""


__all__ = [
    "AvatarPromptRail",
]


def _build_interaction_prompt(language: str) -> str:
    if language == "cn":
        return """## 多輪互動指引

在以下情況，你必須透過追問來明確需求，不要自行假設或跳過：

### 何時必須追問
1. **缺少關鍵引數**：任務需要具體引數但使用者未提供（如訂會議室但沒說樓層、時間）
2. **需求模糊或寬泛**：使用者請求範圍太大或方向不明確，直接執行可能偏離意圖（如"幫我寫個報告""做個調研""整理一下"）
3. **存在多種理解**：請求可以有多種解讀方式，不同理解會導致完全不同的執行結果
4. **需要確認授權**：需要 principal（你代替的人）確認或授權才能執行

### 群聊追問
如果缺少的資訊可以由群聊中的某位使用者提供，在回覆開頭加上 `[群聊追問@使用者名稱]`：
- 例：`[群聊追問@張三] 請問需要預約哪個樓層的會議室？`
- 系統會自動在群聊中 @張三 並追蹤回覆

如果缺少的資訊由傳送請求的人自己補充即可，在回覆開頭加上 `[群聊追問]`（不帶@）：
- 例：`[群聊追問] 請問會議主題是什麼？`
- 例：`[群聊追問] 你說的調研報告是關於哪個方向的？需要覆蓋哪些內容？`
- 系統會自動追蹤傳送者的回覆

### 私聊追問
如果需要 principal（你代替的人）確認或授權，在回覆開頭加上 `[私聊追問]`：
- 例：`[私聊追問] 張三要訂會議室，你確認嗎？`
- 系統會自動私聊 principal 並在群聊中傳送簡短確認

### 注意事項
- 需求模糊時**必須追問**，不要自行猜測使用者意圖後直接執行，否則很可能白做
- 追問時給出具體選項或方向提示，幫助使用者快速回復（如"是A方向還是B方向？"而非"你要什麼？"）
- 追問字首必須放在回覆的最開頭
- 收到追問的回答後，繼續完成任務即可，不需要再加字首
- 收到追問回答後，只針對當前追問的任務繼續處理，不要與之前的其他任務混淆
- 如果群聊歷史中存在多個不同的任務，務必根據追問上下文區分，只處理當前任務
"""
    return """## Multi-turn Interaction Guidance

You MUST follow up to clarify requirements in these situations — do NOT assume or skip:

### When You Must Follow Up
1. **Missing key parameters**: The task requires specific parameters the user hasn't provided (e.g., booking a room without specifying floor or time)
2. **Vague or broad requests**: The request is too broad or unclear — executing directly may miss the user's intent (e.g., "write a report", "do some research", "organize this")
3. **Ambiguous interpretation**: The request could be understood in multiple ways, leading to very different outcomes
4. **Need confirmation**: You need the principal (the person you represent) to confirm or authorize

### Group Follow-up
If the missing information can be provided by someone in the group chat, prefix your reply with `[群聊追問@Username]`:
- Example: `[群聊追問@張三] Which floor meeting room do you need?`
- The system will automatically @mention the user and track their reply

If the sender can provide the missing information themselves, prefix your reply with `[群聊追問]` (without @):
- Example: `[群聊追問] What is the meeting topic?`
- Example: `[群聊追問] What direction should the research report cover? What topics should it include?`
- The system will automatically track the sender's reply

### DM Follow-up
If you need the principal (the person you represent) to confirm or authorize, prefix your reply with `[私聊追問]`:
- Example: `[私聊追問] 張三 wants to book a meeting room, do you confirm?`
- The system will automatically DM the principal and send a brief acknowledgment in the group

### Notes
- When the request is vague, you **MUST follow up** — do NOT guess the user's intent and execute, or you'll likely waste effort
- When following up, provide specific options or directional hints to help the user reply quickly (e.g., "Direction A or Direction B?" rather than "What do you want?")
- The follow-up prefix must be at the very beginning of your reply
- After receiving the answer, continue completing the task without any prefix
- After receiving the answer, only process the current task from the follow-up, do not mix with previous tasks
- If the group chat history contains multiple different tasks, distinguish them based on the follow-up context and only handle the current one
"""
