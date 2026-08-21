# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Intent recognition module.

This module implements event handling based on intent recognition, including:

- IntentRecognizer: recognizes user intent from input events.
- EventHandlerWithIntentRecognition: event handler that routes logic by
  recognized intent.

Workflow:
    1. Receive an input event.
    2. Use ``IntentRecognizer`` to recognize intent.
    3. Call the corresponding handler method based on intent type.

Supported intent types (see ``IntentType`` for details):
- CREATE_TASK
- PAUSE_TASK
- RESUME_TASK
- CONTINUE_TASK
- SUPPLEMENT_TASK
- CANCEL_TASK
- MODIFY_TASK
- SWITCH_TASK
- UNKNOWN_TASK
"""
import asyncio
import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.context_engine import ContextEngine, ModelContext
from openjiuwen.core.controller import DataFrame, TextDataFrame, FileDataFrame, JsonDataFrame, IntentType, TaskStatus, \
    Task
from openjiuwen.core.controller.base import ControllerConfig
from openjiuwen.core.controller.modules.event_handler import EventHandler, EventHandlerInput
from openjiuwen.core.controller.modules.intent_toolkits import IntentToolkits
from openjiuwen.core.controller.modules.task_manager import TaskManager, TaskFilter
from openjiuwen.core.controller.schema import Intent
from openjiuwen.core.controller.schema.event import Event, InputEvent, TaskFailedEvent, TaskCompletionEvent, \
    TaskInteractionEvent
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage, ToolMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.ability_manager import AbilityManager


class IntentRecognizer:
    """意圖識別器

    負責識別使用者輸入中的意圖，將事件轉換為Intent物件。
    """
    def __init__(
            self,
            config: ControllerConfig,
            task_manager: TaskManager,
            ability_manager: AbilityManager,
            context_engine: ContextEngine
    ):
        """初始化意圖識別器

        Args:
            config: 控制器配置
            task_manager: 工作管理員
            ability_manager: 能力包
            context_engine: 上下文引擎
        """
        self._config = config
        self._task_manager = task_manager
        self._context_engine = context_engine
        self._ability_manager = ability_manager

        self._system_message = SystemMessage(content="""# 角色
你是一個任務管理助手，專門使用工具建立和管理任務。你的核心理念是：**任何使用者請求都可以轉化為一個任務**，並由工作管理員處理。

# 核心原則
1. **任務化一切**：對於任何使用者請求（包括資訊查詢、事務處理、提醒等），你的第一反應不是直接執行或拒絕，而是思考如何將它建立為一個任務。
2. **透明管理**：如果任務需要外部能力（如天氣API），你仍然建立它，並明確告知使用者任務的狀態。

# 工作流程
1. **解析請求**：理解使用者想做什麼。
2. **任務操作**：使用工具建立一個對應的任務或修改已有任務。
3. **永遠不拒絕**：不聲稱“超出能力範圍”，而是告知使用者任務會由其他執行器處理。

# 任務目標
- 根據使用者輸入，**總是優先建立對應的任務**。
- 使用工具進行任務操作（建立、更新、列表、刪除）。
- 只有純粹閒聊或問候時不呼叫工具。
""")

        self._user_prompt_template = """你當前擁有的任務有：
{task_descriptions}

當前使用者的輸入為：
{query}

請根據你當前的任務和使用者輸入，進行合適的任務操作。
"""

    async def _prepare_user_message(self, query):
        tasks = await self._task_manager.get_task()
        task_prompt = []
        if tasks:
            for task in tasks:
                task_prompt.append(
                    f"## Task id: {task.task_id}\n### Task description: {task.description}\nStatus: {task.status}\n")
        else:
            task_prompt.append("無")
        task_prompt = "\n".join(task_prompt)

        prompt = self._user_prompt_template.format(
            task_descriptions=task_prompt,
            query=query
        )
        return UserMessage(content=prompt)

    async def recognize(self, event: Event, session: Session) -> List[Intent]:
        """識別意圖

        Args:
            event: 輸入事件
            session: 會話物件

        Returns:
            Intent: 識別出的意圖物件
        """

        context = self._context_engine.get_context(session_id=session.get_session_id())
        if not context:
            context = await self._context_engine.create_context(session=session)

        if not isinstance(event, InputEvent):
            raise ValueError

        inputs: List[DataFrame] = event.input_data
        texts = [df for df in inputs if isinstance(df, TextDataFrame)]
        files = [df for df in inputs if isinstance(df, FileDataFrame)]
        jsons = [df for df in inputs if isinstance(df, JsonDataFrame)]

        if files or jsons:
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg="Inputs with files or jsons are not supported for intent recognition."
            )

        if len(texts) > 1:
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg="Multiple inputs are not supported for intent recognition."
            )

        from openjiuwen.core.runner import Runner
        model = await Runner.resource_mgr.get_model(model_id=self._config.intent_llm_id)
        user_message = await self._prepare_user_message(query=texts[0].text)
        await context.add_messages(user_message)
        toolkits = IntentToolkits(event, self._config.intent_confidence_threshold)
        max_message_len = 50
        response = await model.invoke(
            messages=[self._system_message] + context.get_messages(size=max_message_len),
            tools=toolkits.get_openai_tool_schemas(self._config.intent_type_list)
        )
        await context.add_messages(response)

        intents = []
        while True:
            if not response.tool_calls:
                break
            else:
                for tool_call in response.tool_calls:
                    instance = getattr(toolkits, tool_call.name)
                    intent, result = await instance(**json.loads(tool_call.arguments))
                    intents.append(intent)
                    await context.add_messages(ToolMessage(
                        tool_call_id=tool_call.id,
                        content=result
                    ))
                response = await model.invoke(
                    messages=[self._system_message] + context.get_messages(size=max_message_len),
                    tools=toolkits.get_openai_tool_schemas()
                )
                await context.add_messages(response)

        return intents


class EventHandlerWithIntentRecognition(EventHandler):
    """基於意圖識別的事件處理器

    在EventHandler的基礎上增加意圖識別功能，根據識別出的意圖呼叫相應的處理方法。
    """

    def __init__(self):
        super().__init__()
        self.recognizer = IntentRecognizer(
            self._config,
            self.task_manager,
            self.ability_manager,
            self.context_engine
        )

    async def handle_input(self, inputs: EventHandlerInput):
        """處理輸入事件

        識別輸入意圖，並呼叫相應方法處理意圖，可重寫。

        Args:
            inputs: 事件處理器輸入
        """
        intents = await self.recognizer.recognize(inputs.event, inputs.session)
        tasks = []
        for intent in intents:
            if intent.intent_type == IntentType.CREATE_TASK:
                tasks.append(asyncio.create_task(self._process_create_task_intent(intent, inputs.session)))
            elif intent.intent_type == IntentType.PAUSE_TASK:
                tasks.append(asyncio.create_task(self._process_pause_task_intent(intent, inputs.session)))
            elif intent.intent_type == IntentType.RESUME_TASK:
                tasks.append(asyncio.create_task(self._process_resume_task_intent(intent, inputs.session)))
            elif intent.intent_type == IntentType.CONTINUE_TASK:
                tasks.append(asyncio.create_task(self._process_continue_task_intent(intent, inputs.session)))
            elif intent.intent_type == IntentType.SUPPLEMENT_TASK:
                tasks.append(asyncio.create_task(self._process_supplement_task_intent(intent, inputs.session)))
            elif intent.intent_type == IntentType.CANCEL_TASK:
                tasks.append(asyncio.create_task(self._process_cancel_task_intent(intent, inputs.session)))
            elif intent.intent_type == IntentType.MODIFY_TASK:
                tasks.append(asyncio.create_task(self._process_modify_task_intent(intent, inputs.session)))
            else:
                tasks.append(asyncio.create_task(self._process_unknown_task_intent(intent, inputs.session)))
        return await asyncio.gather(*tasks)

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        """處理任務互動事件

        將interaction直接丟擲給使用者，可重寫。

        Args:
            inputs: 事件處理器輸入
        """
        if not isinstance(inputs.event, TaskInteractionEvent):
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Input Event has to be type of TaskInteractionEvent, not {type(inputs.event)}"
            )
        await inputs.session.write_stream({
                "interaction": inputs.event.interaction
            })

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """處理任務完成事件

        將任務完成資訊丟擲給使用者，可重寫。

        Args:
            inputs: 事件處理器輸入
        """
        if not isinstance(inputs.event, TaskCompletionEvent):
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Input Event has to be type of TaskCompletionEvent, not {type(inputs.event)}"
            )
        await inputs.session.write_stream({
                "result": inputs.event.task_result
            })

    async def handle_task_failed(self, inputs: EventHandlerInput):
        """處理任務失敗事件

        將錯誤資訊丟擲給使用者，可重寫。

        Args:
            inputs: 事件處理器輸入
        """
        if not isinstance(inputs.event, TaskFailedEvent):
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Input Event has to be type of TaskFailedEvent, not {type(inputs.event)}"
            )
        await inputs.session.write_stream({
                "error_message": inputs.event.error_message
            })

    async def _process_create_task_intent(self, intent: Intent, session: Session):
        """處理建立任務意圖

        使用者自定義執行新任務邏輯。

        Args:
            intent: 意圖
            session: Session
        """
        task = Task(
            session_id=session.get_session_id(),
            task_id=intent.target_task_id,
            task_type="default_task_type",
            description=intent.target_task_description,
            priority=1,
            context_id=f"{session.get_session_id()}_{intent.target_task_id}",
            inputs=[intent.event] if isinstance(intent.event, InputEvent) else None,
            status=TaskStatus.SUBMITTED,
            error_message=None,
            metadata=intent.metadata,
        )
        await self.task_manager.add_task(task)

    async def _process_pause_task_intent(self, intent: Intent, session: Session):
        """處理暫停任務意圖

        呼叫 task_scheduler 的 pause_task 方法打斷目標任務。

        Args:
            intent: 意圖
            session: Session
        """
        await self.task_scheduler.pause_task(intent.target_task_id)

    async def _process_resume_task_intent(self, intent: Intent, session: Session):
        """處理恢復任務意圖

        將要恢復的任務的狀態置為 submitted。

        Args:
            intent: 意圖
            session: Session
        """
        task = await self.task_manager.get_task(TaskFilter(task_id=intent.target_task_id))
        task = task[0]
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.SUBMITTED
            await self.task_manager.update_task(task)

    async def _process_continue_task_intent(self, intent: Intent, session: Session):
        """處理接續任務意圖

        Args:
            intent: 意圖
            session: Session
        """
        if not isinstance(intent.event, InputEvent):
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Input Event has to be type of InputEvent, not {type(intent.event)}"
            )
        previous_events = []
        context_ids = []
        for task_id in intent.depend_task_id:
            old_tasks = await self.task_manager.get_task(TaskFilter(task_id=task_id))
            if old_tasks:
                previous_events.extend(old_tasks[0].inputs)
                context_id = old_tasks[0].context_id
                context_ids.append(context_id)
        event: InputEvent = intent.event
        event.input_data.append(
            JsonDataFrame(data={
                context_id: (await self._context_engine.get_context(context_id)).get_messages()
                for context_id in context_ids
            })
        )
        previous_events.append(event)
        task = Task(
            session_id=session.get_session_id(),
            task_id=intent.target_task_id,
            task_type="default_task_type",
            description=intent.target_task_description,
            priority=1,
            context_id=f"{session.get_session_id()}_{intent.target_task_id}",
            inputs=previous_events,
            status=TaskStatus.SUBMITTED,
            error_message=None,
            metadata=intent.metadata,
        )
        await self.task_manager.add_task(task)

    async def _process_supplement_task_intent(self, intent: Intent, session: Session):
        """處理補充任務意圖

        Args:
            intent: 意圖
            session: Session
        """
        if intent.intent_type != IntentType.SUPPLEMENT_TASK:
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Input Event has to be type of SUPPLEMENT_TASK, not {type(intent.event)}"
            )

        tasks = await self.task_manager.get_task(TaskFilter(task_id=intent.target_task_id))
        task = tasks[0]
        await self.task_scheduler.pause_task(intent.target_task_id)
        task.description += "\n\n任務補充資訊:\n{}".format(intent.supplementary_info)
        task.status = TaskStatus.SUBMITTED
        await self.task_manager.update_task(task)

    async def _process_cancel_task_intent(self, intent: Intent, session: Session):
        """處理取消任務意圖

        呼叫 task_scheduler 的 cancel_task 方法取消目標任務。

        Args:
            intent: 意圖
            session: Session
        """
        if intent.intent_type != IntentType.CANCEL_TASK:
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Input event has to be type of CANCEL_TASK, not {type(intent.event)}"
            )

        await self.task_scheduler.cancel_task(intent.target_task_id)

    async def _process_modify_task_intent(self, intent: Intent, session: Session):
        """處理修改任務意圖

        修改目標任務後，將其狀態置為 submitted。

        Args:
            intent: 意圖
            session: Session
        """
        if intent.intent_type != IntentType.MODIFY_TASK:
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Input Event has to be type of InputEvent, not {type(intent.event)}"
            )
        await self.task_scheduler.cancel_task(intent.target_task_id)
        task = await self.task_manager.get_task(TaskFilter(task_id=intent.target_task_id))
        task[0].description = intent.target_task_description
        if not isinstance(task[0].inputs, list):
            task[0].inputs = [intent.event]
        else:
            task[0].inputs.append(intent.event)
        task[0].status = TaskStatus.SUBMITTED
        await self.task_manager.update_task(task[0])

    async def _process_unknown_task_intent(self, intent: Intent, session: Session):
        """處理未知任務意圖

        返回 Intent 的 clarification_prompt 欄位給使用者。

        Args:
            intent: 意圖
            session: Session
        """
        if intent.intent_type != IntentType.UNKNOWN_TASK:
            raise ValueError
        await session.write_stream({
                "clarification_prompt": intent.clarification_prompt
            })
