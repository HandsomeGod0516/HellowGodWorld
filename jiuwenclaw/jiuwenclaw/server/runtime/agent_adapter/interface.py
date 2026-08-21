# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw Facade - 統一入口與 SDK 適配層.

此模組提供：
- 統一的 JiuWenClaw 公開 API
- SDK 工廠路由（透過環境變數選擇）
- 公共編排邏輯（session 佇列、Skills 路由、heartbeat、流式包裝）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Tuple

from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from jiuwenclaw.server.runtime.agent_adapter.agent_adapters import (
    AgentAdapter,
    create_adapter,
    resolve_sdk_choice,
)
from jiuwenclaw.agents.harness.common.memory.config import get_memory_mode
from jiuwenclaw.server.runtime.session.session_history import append_history_record
from jiuwenclaw.server.runtime.session.session_manager import SessionManager
from jiuwenclaw.server.runtime.skill.skill_manager import SkillManager
from jiuwenclaw.common.config import get_config
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.common.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.extensions.hook_event import AgentServerHookEvents
from jiuwenclaw.extensions.hooks_context import MemoryHookContext
from jiuwenclaw.common.schema.message import EventType, ReqMethod
from jiuwenclaw.common.utils import (
    get_agent_home_dir,
    get_agent_workspace_dir,
    get_env_file,
    reset_free_search_runtime_flags,
)

load_dotenv(dotenv_path=get_env_file(), override=True)
reset_free_search_runtime_flags()

logger = logging.getLogger(__name__)

# SkillDev 請求方法集合（統一委託給 SkillDevService）
_SKILLDEV_METHODS: frozenset[ReqMethod] = frozenset(
    m for m in ReqMethod if m.value.startswith("skilldev.")
)

_SKILL_ROUTES: dict[ReqMethod, str] = {
    ReqMethod.SKILLS_LIST: "handle_skills_list",
    ReqMethod.SKILLS_INSTALLED: "handle_skills_installed",
    ReqMethod.SKILLS_GET: "handle_skills_get",
    ReqMethod.SKILLS_MARKETPLACE_LIST: "handle_skills_marketplace_list",
    ReqMethod.SKILLS_INSTALL: "handle_skills_install",
    ReqMethod.SKILLS_UNINSTALL: "handle_skills_uninstall",
    ReqMethod.SKILLS_IMPORT_LOCAL: "handle_skills_import_local",
    ReqMethod.SKILLS_MARKETPLACE_ADD: "handle_skills_marketplace_add",
    ReqMethod.SKILLS_MARKETPLACE_REMOVE: "handle_skills_marketplace_remove",
    ReqMethod.SKILLS_MARKETPLACE_TOGGLE: "handle_skills_marketplace_toggle",
    ReqMethod.SKILLS_SKILLNET_SEARCH: "handle_skills_skillnet_search",
    ReqMethod.SKILLS_SKILLNET_INSTALL: "handle_skills_skillnet_install",
    ReqMethod.SKILLS_SKILLNET_INSTALL_STATUS: "handle_skills_skillnet_install_status",
    ReqMethod.SKILLS_SKILLNET_EVALUATE: "handle_skills_skillnet_evaluate",
    ReqMethod.SKILLS_CLAWHUB_GET_TOKEN: "handle_skills_clawhub_get_token",
    ReqMethod.SKILLS_CLAWHUB_SET_TOKEN: "handle_skills_clawhub_set_token",
    ReqMethod.SKILLS_CLAWHUB_SEARCH: "handle_skills_clawhub_search",
    ReqMethod.SKILLS_CLAWHUB_DOWNLOAD: "handle_skills_clawhub_download",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_INFO: "handle_skills_team_skills_hub_info",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_INIT: "handle_skills_team_skills_hub_init",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_VALIDATE: "handle_skills_team_skills_hub_validate",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_PACK: "handle_skills_team_skills_hub_pack",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_SEARCH: "handle_skills_team_skills_hub_search",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_INSTALL: "handle_skills_team_skills_hub_install",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_PUBLISH: "handle_skills_team_skills_hub_publish",
    ReqMethod.SKILLS_TEAMSKILLS_HUB_DELETE: "handle_skills_team_skills_hub_delete",
    ReqMethod.SKILLS_EVOLUTION_STATUS: "handle_skills_evolution_status",
    ReqMethod.SKILLS_EVOLUTION_GET: "handle_skills_evolution_get",
    ReqMethod.SKILLS_EVOLUTION_SAVE: "handle_skills_evolution_save",
}

_SKILL_COMMAND_REGEX = re.compile(
    r"^/skills use\s+(?P<skill_names>[^,]+)\s*,\s*(?P<query>.*)$"
)


def _handle_skills_use_slash_command(query: str) -> Tuple[list, str]:
    """Handle the /skills use slash command"""
    stripped = query.strip()
    if not stripped.startswith("/skills use"):
        return [], query
    
    skill_list = []
    matches = _SKILL_COMMAND_REGEX.match(stripped)
    if matches:
        skill_list.append(matches.group("skill_names")) # Currently only extracts one skill
        new_query = matches.group("query")
        return skill_list, new_query
    else:
        logger.warning(f"Couldn't parse command: {stripped}")
        return [], query


def build_user_prompt(content: str, files: dict, channel: str, language: str, *, 
    trusted_dirs: list[str] | None = None, metadata: dict[str, Any] | None = None) -> str:
    """Build user prompt for the agent."""

    interaction_prefix = ""
    if metadata:
        interaction_ctx = str(metadata.get("interaction_context") or "").strip()
        if interaction_ctx:
            interaction_prefix = f"\n{interaction_ctx}\n\n"

    skills_to_use, new_content = _handle_skills_use_slash_command(content)
    if new_content:
        content = new_content

    if language == "zh":
        prompt = "你收到一條訊息：\n"
        if channel == "cron":
            prompt = "你收到一條訊息，你的最終回覆將直接傳送給使用者，請輸出使用者期望看到的內容，而非操作確認：\n"
    else:
        prompt = "You receive a new message:\n"
        if channel == "cron":
            prompt = ("You receive a message. Your final reply will be sent directly to the user. "
                      "Output the content the user expects to see, not just a confirmation:\n")
    msg_data: dict[str, Any] = {
        "source": channel,
        "preferred_response_language": language,
        "content": content,
        "type": "user input",
    }
    if channel in ["cron", "heartbeat"]:
        msg_data["source"] = "system"
        msg_data["type"] = channel
    if metadata:
        chat_type = str(metadata.get("chat_type") or metadata.get("im_chat_type") or "").strip()
        if chat_type:
            msg_data["chat_type"] = chat_type
        sender_name = str(metadata.get("sender_name") or "").strip()
        if sender_name:
            msg_data["sender"] = sender_name
    if channel not in ["cron", "heartbeat"]:
        msg_data["files_updated_by_user"] = json.dumps(files, ensure_ascii=False)
    final_prompt = interaction_prefix + prompt + json.dumps(msg_data, ensure_ascii=False)
    if interaction_prefix:
        logger.info(
            "[build_user_prompt][DEBUG] interaction_context 存在，最終 prompt=\n%s",
            final_prompt,
        )

    now = datetime.now(timezone(timedelta(hours=8)))
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    user_message_context = {
        "source": channel,
        "timezone": "Asia/Shanghai",
        "timestamp": now_str,
        "preferred_response_language": language,
        "content": content,
        "files_updated_by_user": json.dumps(files, ensure_ascii=False),
        "type": "user input",
    }
    if skills_to_use:
        user_message_context["skills_to_use"] = skills_to_use
    if trusted_dirs:
        user_message_context["trusted_dirs"] = json.dumps(trusted_dirs, ensure_ascii=False)
    return interaction_prefix + prompt + json.dumps(user_message_context, ensure_ascii=False)



class JiuWenClaw:
    """JiuWenClaw 統一門面.

    提供：
    - SDK 工廠路由
    - 統一對外 API（create_instance, reload_agent_config, process_message, process_message_stream）
    - 公共編排（session 佇列、Skills 路由、heartbeat、流式包裝）
    """

    def __init__(self) -> None:
        self._adapter: AgentAdapter | None = None
        self._sdk_name: str | None = None
        self._skill_manager = SkillManager(workspace_dir=str(get_agent_workspace_dir()))
        self._session_manager = SessionManager()
        # SkillDev 模式：懶初始化，首次 skilldev.* 請求時構造
        self._skilldev_service = None

    def _get_skilldev_service(self):
        """懶初始化並返回 SkillDevService 例項.

        SkillDevService 是無狀態的，單例項即可服務所有請求。
        首次呼叫時從當前 JiuWenClaw 配置中提取最小依賴並構造。
        """
        if self._skilldev_service is not None:
            return self._skilldev_service

        from jiuwenclaw.server.runtime.skill.skilldev import (SkillDevDeps, SkillDevService,
                                                              StateStore, WorkspaceProvider)
        from jiuwenclaw.common.utils import get_workspace_dir
        from jiuwenclaw.agents.harness.common.tools.mcp_toolkits import get_mcp_tools

        skilldev_base = get_workspace_dir() / "skilldev"
        state_store = StateStore(skilldev_base)
        workspace_provider = WorkspaceProvider(skilldev_base)

        config = get_config()
        model_configs = config.get("models", {})
        default_model = model_configs.get("default", {})

        deps = SkillDevDeps(
            model_name=default_model.get("model_name", ""),
            model_client_config=default_model.get("model_client_config", {}),
            mcp_tools_factory=get_mcp_tools,  # 直接複用已載入的 MCP 工具工廠
            sysop_config=None,
            state_store=state_store,
            workspace_provider=workspace_provider,
        )
        self._skilldev_service = SkillDevService(deps)
        logger.info("[JiuWenClaw] SkillDevService 初始化完成")
        return self._skilldev_service

    def _ensure_adapter(self, *, mode: str = "agent") -> AgentAdapter:
        """確保 adapter 已初始化，如果未初始化則根據環境變數和 mode 建立."""
        if self._adapter is None:
            self._sdk_name = resolve_sdk_choice()
            self._adapter = create_adapter(self._sdk_name, mode=mode)
            if hasattr(self._adapter, "set_skill_manager"):
                self._adapter.set_skill_manager(self._skill_manager)
            self._skill_manager.set_skillnet_install_complete_hook(
                self.create_instance
            )
            logger.info("[JiuWenClaw] Initialized adapter: sdk=%s, mode=%s", self._sdk_name, mode)
        return self._adapter

    async def create_instance(self, config: dict[str, Any] | None = None, *,
                              mode: str = "agent", sub_mode: str = None) -> None:
        """初始化 Agent 例項.

        Args:
            config: 可選配置，透傳給底層 adapter.
            mode: 例項化模式，"claw"（預設）或 "code"，透傳給底層 adapter.
            sub_mode: 子模式
        """
        adapter = self._ensure_adapter(mode=mode)
        await adapter.create_instance(config, mode=mode, sub_mode=sub_mode)
        logger.info("[JiuWenClaw] Agent instance created: sdk=%s, mode=%s, sub_mode=%s", self._sdk_name, mode, sub_mode)

    async def reload_agent_config(
            self,
            config_base: dict[str, Any] | None = None,
            env_overrides: dict[str, Any] | None = None,
    ) -> None:
        """從配置重新載入.

        Args:
            config_base: 可選的完整配置快照；傳入時優先使用它而不是讀取本地 config.yaml。
            env_overrides: 可選的環境變數增量；僅覆蓋請求中出現的 key。
        """
        adapter = self._ensure_adapter()
        await adapter.reload_agent_config(config_base, env_overrides)
        logger.info("[JiuWenClaw] Agent config reloaded: sdk=%s", self._sdk_name)

    def _build_inputs(self, request: AgentRequest) -> Tuple[dict[str, Any], str, str]:
        """構建 adapter 所需的 inputs 字典."""
        from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

        config_base = get_config()
        memory_mode = get_memory_mode(config_base)
        query = request.params.get("query", "")
        channel = request.session_id.split('_')[0] if request.session_id else "web"
        language = config_base.get("preferred_language", "zh")

        # Get trusted directories from request params (passed by TUI)
        trusted_dirs: list[str] = []
        raw_trusted_dirs = request.params.get("trusted_dirs")
        if isinstance(raw_trusted_dirs, list):
            for d in raw_trusted_dirs:
                if isinstance(d, str) and d.strip():
                    trusted_dirs.append(d.strip())
        if request.metadata and request.metadata.get("interaction_context"):
            logger.info(
                "[_build_inputs][DEBUG] request.params.query=\n%s",
                query[:2000] if isinstance(query, str) else str(query)[:2000],
            )

        if isinstance(query, InteractiveInput):
            final_query = query
        else:
            answers = request.params.get("answers", [])
            if answers:
                request_id = request.params.get("request_id", "")
                source = request.params.get("source", "")
                interactive_input = self._build_interactive_input_from_answers(request_id, answers, source)
                final_query = interactive_input if interactive_input is not None else build_user_prompt(
                    query,
                    files=request.params.get("files", {}),
                    channel=channel,
                    language=language,
                    trusted_dirs=trusted_dirs,
                    metadata=request.metadata,
                )
            else:
                final_query = build_user_prompt(
                    query,
                    files=request.params.get("files", {}),
                    channel=channel,
                    language=language,
                    trusted_dirs=trusted_dirs,
                    metadata=request.metadata,
                )

        inputs: dict[str, Any] = {
            "conversation_id": request.session_id,
            "query": final_query,
            "channel": channel,
            "language": language,
        }

        # 傳遞 enable_memory 引數
        enable_memory = request.metadata.get("enable_memory", True) if request.metadata else True
        inputs["enable_memory"] = enable_memory

        # 傳遞 trusted_dirs 引數（用於 RuntimePromptRail 新增路徑限制策略）
        if trusted_dirs:
            inputs["trusted_dirs"] = trusted_dirs

        run = request.params.get("run")
        if run:
            inputs["run"] = run

        # 返回原始 query（未經 build_user_prompt 包裝）
        # Team 模式需要使用原始 query，而不是 JSON 包裝後的 prompt
        return inputs, memory_mode, query

    @staticmethod
    def _build_interactive_input_from_answers(
            request_id: str, answers: list[dict], source: str = ""
    ) -> Any:
        """從使用者答案構建 InteractiveInput.

        Args:
            request_id: 工具呼叫 ID
            answers: 使用者答案列表，每個答案對應一個問題
            source: 中斷來源，用於區分 PermissionRail 和 AskUserRail

        Returns:
            InteractiveInput 例項
        """
        from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

        interactive_input = InteractiveInput()

        if source == "ask_user_interrupt":
            answers_dict = {}
            for answer in answers:
                if isinstance(answer, dict):
                    question_text = answer.get("question", "")
                    selected_options = answer.get("selected_options", [])
                    answer_value = selected_options[0] if selected_options else ""
                    if question_text and answer_value:
                        answers_dict[question_text] = answer_value
            interactive_input.update(request_id, {"answers": answers_dict})
            logger.info(
                "[JiuWenClaw] AskUserRail InteractiveInput.update: request_id=%s payload=%s",
                request_id, {"answers": answers_dict}
            )
            return interactive_input

        answer = answers[0] if answers else {}
        selected_options = answer.get("selected_options", []) if isinstance(answer, dict) else []
        custom_input = answer.get("custom_input", "") if isinstance(answer, dict) else ""

        if "本次允許" in selected_options:
            confirm_payload = {"approved": True, "auto_confirm": False, "feedback": ""}
        elif "總是允許" in selected_options:
            confirm_payload = {
                "approved": True,
                "auto_confirm": True,
                "persist_allow": True,
                "feedback": "",
            }
        elif "拒絕" in selected_options:
            confirm_payload = {"approved": False, "auto_confirm": False, "feedback": custom_input or "使用者拒絕"}
        else:
            confirm_payload = {"approved": False, "auto_confirm": False, "feedback": "未知選項"}

        interactive_input.update(request_id, confirm_payload)
        logger.info(
            "[JiuWenClaw] PermissionRail InteractiveInput.update: request_id=%s payload=%s",
            request_id, confirm_payload
        )

        return interactive_input

    async def _handle_skilldev_request(self, request: AgentRequest) -> AgentResponse | None:
        """處理 SkillDev 相關請求，返回 None 表示不是 SkillDev 請求."""
        if request.req_method not in _SKILLDEV_METHODS:
            return None

        service = self._get_skilldev_service()
        try:
            chunks = []
            async for chunk in service.handle(request):
                chunks.append(chunk)
            final = chunks[-1] if chunks else None
            payload = final.payload if final else {}
        except Exception as exc:
            logger.error("[JiuWenClaw] skilldev 請求處理失敗: %s", exc)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    async def _handle_skills_request(self, request: AgentRequest) -> AgentResponse | None:
        """處理 Skills 相關請求，返回 None 表示不是 Skills 請求."""
        if request.req_method not in _SKILL_ROUTES:
            return None

        handler_name = _SKILL_ROUTES[request.req_method]
        handler = getattr(self._skill_manager, handler_name)
        try:
            payload = await handler(request.params)
            _reload_after_skills = handler_name in [
                "handle_skills_install",
                "handle_skills_uninstall",
                "handle_skills_import_local",
                "handle_skills_skillnet_install",
                "handle_skills_clawhub_download",
                "handle_skills_team_skills_hub_install",
            ]
            if handler_name == "handle_skills_skillnet_install" and payload.get("pending"):
                _reload_after_skills = False
            if _reload_after_skills:
                await self.create_instance()
        except Exception as exc:
            logger.error("[JiuWenClaw] skills 請求處理失敗: %s", exc)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    async def _process_interrupt(self, request: AgentRequest) -> AgentResponse:
        """處理 interrupt 請求.

        根據 intent 分流：
        - pause: 暫停 ReAct 迴圈（不取消任務）
        - resume: 恢復已暫停的 ReAct 迴圈
        - cancel: 取消當前 session 正在執行的任務
        - supplement: 取消當前任務但保留 todo

        Args:
            request: AgentRequest，params 中可包含：
                - intent: 中斷意圖 ('pause' | 'cancel' | 'resume' | 'supplement')
                - new_input: 新的使用者輸入（用於切換任務）

        Returns:
            AgentResponse 包含 interrupt_result 事件資料
        """
        intent = request.params.get("intent", "cancel")
        session_id = self._session_manager.get_session_id(request.session_id)
        adapter = self._ensure_adapter()

        if intent == "pause":
            # 暫停：不取消任務，只暫停 ReAct 迴圈
            return await adapter.process_interrupt(request)

        if intent == "resume":
            # 恢復：恢復 ReAct 迴圈
            return await adapter.process_interrupt(request)

        if intent == "supplement":
            # 取消當前 session 的任務
            response = await adapter.process_interrupt(request)
            await self._session_manager.cancel_session_task(session_id, "interrupt(supplement): ")
            return response

        # cancel: 僅取消當前 session 的任務，避免誤傷其它併發會話
        await self._session_manager.cancel_session_task(session_id, f"interrupt(intent={intent}): ")
        await self._cancel_team_work_for_session(
            session_id,
            request.channel_id,
            log_prefix=f"interrupt(intent={intent}): ",
        )
        return await adapter.process_interrupt(request)

    async def _cancel_team_work_for_session(
        self,
        session_id: str,
        channel_id: str | None = None,
        log_prefix: str = "",
    ) -> bool:
        """終止當前 session 的 Team runtime（若存在）。"""
        from jiuwenclaw.agents.harness.team import get_team_manager

        try:
            team_manager = get_team_manager(channel_id)
            return await team_manager.terminate_session_runtime(session_id, reason=log_prefix)
        except Exception:
            logger.exception(
                "[JiuWenClaw] failed to terminate team runtime: session_id=%s",
                session_id,
            )
            return False

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        """處理非流式請求.

        支援多 session 併發執行，同 session 內任務按先進後出順序執行.
        """
        adapter = self._ensure_adapter()

        if request.req_method == ReqMethod.CHAT_CANCEL:
            return await self._process_interrupt(request)

        if request.req_method == ReqMethod.CHAT_ANSWER:
            return await adapter.handle_user_answer(request)

        heartbeat_response = await adapter.handle_heartbeat(request)
        if heartbeat_response is not None:
            return heartbeat_response

        skilldev_response = await self._handle_skilldev_request(request)
        if skilldev_response is not None:
            return skilldev_response

        skills_response = await self._handle_skills_request(request)
        if skills_response is not None:
            return skills_response

        session_id = self._session_manager.get_session_id(request.session_id)
        query = request.params.get("query", "")
        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
            channel_metadata=request.metadata,
            mode=request.params.get("mode", "unknown"),
        )

        logger.info(
            "[JiuWenClaw] 處理請求: request_id=%s channel_id=%s session_id=%s sdk=%s",
            request.request_id, request.channel_id, session_id, self._sdk_name,
        )

        inputs, memory_mode, raw_query = self._build_inputs(request)

        # cloud memory: before chat hook
        if memory_mode == "cloud":
            mem_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_BEFORE_CHAT, mem_ctx)
            memory_block = "\n\n".join(b for b in mem_ctx.memory_blocks if b)
            inputs["memory_block"] = memory_block

        async def run_agent_task():
            return await adapter.process_message_impl(request, inputs)

        result = await self._session_manager.submit_and_wait(session_id, run_agent_task)

        if result.ok and result.payload.get("content"):
            content = result.payload["content"]
            content_str = content if isinstance(content, str) else str(content)
            append_history_record(
                session_id=session_id,
                request_id=request.request_id,
                channel_id=request.channel_id,
                role="assistant",
                event_type="chat.final",
                content=content_str,
                timestamp=time.time(),
                mode=request.params.get("mode", "unknown"),
            )

            # cloud memory: after chat hook
            if memory_mode == "cloud":
                after_ctx = MemoryHookContext(
                    session_id=request.session_id or "default",
                    request_id=request.request_id or "",
                    channel_id=request.channel_id,
                    agent_name="main_agent",
                    workspace_dir=str(get_agent_home_dir()),
                    assistant_message=content_str,
                    extra=request.params,
                )
                await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

        return result

    async def process_message_stream(
            self, request: AgentRequest
    ) -> AsyncIterator[AgentResponseChunk]:
        """處理流式請求.

        支援多 session 併發執行，同 session 內任務按先進後出順序執行.
        """
        # SkillDev 流式請求：直接委託給 SkillDevService，繞過 ReActAgent
        if request.req_method in _SKILLDEV_METHODS:
            service = self._get_skilldev_service()
            try:
                async for chunk in service.handle(request):
                    yield chunk
            except Exception as exc:
                logger.error("[JiuWenClaw] skilldev 流式請求處理失敗: %s", exc)
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"event_type": "skilldev.error", "error": str(exc)},
                    is_complete=True,
                )
            return

        adapter = self._ensure_adapter()

        session_id = self._session_manager.get_session_id(request.session_id)
        query = request.params.get("query", "")

        mode = request.params.get("mode", "") if isinstance(request.params, dict) else ""
        team_flag = request.params.get("team", False) if isinstance(request.params, dict) else False
        is_team_mode = team_flag or (isinstance(mode, str) and mode.strip().lower() == "team")

        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
            channel_metadata=request.metadata,
            mode=request.params.get("mode", "unknown"),
        )

        logger.info(
            "[JiuWenClaw] 處理流式請求: request_id=%s channel_id=%s session_id=%s sdk=%s",
            request.request_id, request.channel_id, session_id, self._sdk_name,
        )

        inputs, memory_mode, raw_query = self._build_inputs(request)
        rid = request.request_id
        cid = request.channel_id

        # Team 模式：使用原始 query，而不是 build_user_prompt 包裝後的內容
        if is_team_mode:
            inputs["query"] = raw_query
            logger.info(
                "[JiuWenClaw] Team模式使用原始query: %s",
                raw_query[:100] if raw_query else "",
            )

        # cloud memory: before chat hook
        if memory_mode == "cloud":
            mem_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_BEFORE_CHAT, mem_ctx)
            memory_block = "\n\n".join(b for b in mem_ctx.memory_blocks if b)
            inputs["memory_block"] = memory_block

        # Team 模式: 檢查是否是後續請求（需要繞過 Session Manager）
        is_team_first_request = True
        if is_team_mode:
            from jiuwenclaw.agents.harness.team import get_team_manager
            team_manager = get_team_manager(request.channel_id)
            is_team_first_request = not team_manager.has_stream_task(session_id)
            logger.info(
                "[JiuWenClaw] Team模式: session_id=%s is_first=%s",
                session_id, is_team_first_request
            )

        stream_queue = asyncio.Queue()
        stream_done = asyncio.Event()
        final_answer_content = ""
        final_answer_chunks: list[str] = []

        async def run_stream_task():
            try:
                async for chunk in adapter.process_message_stream_impl(request, inputs):
                    await stream_queue.put(("chunk", chunk))
            except asyncio.CancelledError:
                logger.info("[JiuWenClaw] 流式任務被取消: request_id=%s session_id=%s", rid, session_id)
                await stream_queue.put(("error", asyncio.CancelledError()))
            except Exception as exc:
                logger.exception("[JiuWenClaw] 流式任務異常: %s", exc)
                await stream_queue.put(("error", exc))
            finally:
                stream_done.set()

        # Team 模式: 後續請求直接執行，繞過 Session Manager 佇列
        # 因為 Team 是長期執行的(persistent)，interact 呼叫不需要等待前一個任務完成
        # 且 team_helpers 內部已有請求鎖保證同一 session 的請求序列執行
        if is_team_mode and not is_team_first_request:
            logger.info(
                "[JiuWenClaw] Team模式後續請求，直接執行: request_id=%s session_id=%s",
                rid, session_id,
            )
            asyncio.create_task(run_stream_task())
        else:
            await self._session_manager.submit_task(session_id, run_stream_task)

        try:
            while not stream_done.is_set() or not stream_queue.empty():
                try:
                    item = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                event_type, data = item

                if event_type == "error":
                    if isinstance(data, asyncio.CancelledError):
                        logger.info("[JiuWenClaw] 流式處理被中斷: request_id=%s", rid)
                        raise data
                    append_history_record(
                        session_id=session_id,
                        request_id=rid,
                        channel_id=cid,
                        role="assistant",
                        event_type="chat.error",
                        content=str(data),
                        timestamp=time.time(),
                        mode=request.params.get("mode", "unknown"),
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.error", "error": str(data)},
                        is_complete=False,
                    )
                else:
                    if isinstance(data, AgentResponseChunk):
                        if isinstance(data.payload, dict) and isinstance(data.payload.get("event_type"), str):
                            et = str(data.payload.get("event_type"))
                            should_record = et.startswith("chat.")
                            if not should_record and et == EventType.TEAM_MESSAGE.value:
                                should_record = True

                            if should_record:
                                payload_dict = dict(data.payload)
                                extra_fields = {k: v for k, v in payload_dict.items() if
                                                k not in ("event_type", "content")}
                                if et == EventType.TEAM_MESSAGE.value and "event" in payload_dict:
                                    event_data = payload_dict.get("event", {})
                                    if isinstance(event_data, dict):
                                        for k, v in event_data.items():
                                            if k not in ("type", "timestamp", "content"):
                                                extra_fields[k] = v
                                append_history_record(
                                    session_id=session_id,
                                    request_id=rid,
                                    channel_id=cid,
                                    role="assistant",
                                    event_type=et,
                                    content=data.payload.get("content") or data.payload.get("error") or "",
                                    timestamp=time.time(),
                                    extra=extra_fields if extra_fields else None,
                                    mode=request.params.get("mode", "unknown"),
                                )
                            if et == "chat.final":
                                final_answer_content = str(data.payload.get("content", ""))
                            elif et == "chat.delta":
                                final_answer_chunks.append(str(data.payload.get("content", "")))
                        yield data
                    elif isinstance(data, dict) and isinstance(data.get("event_type"), str):
                        et = str(data.get("event_type"))
                        should_record = et.startswith("chat.")
                        if not should_record and et == EventType.TEAM_MESSAGE.value:
                            should_record = True

                        if should_record:
                            extra_fields = {k: v for k, v in data.items() if k not in ("event_type", "content")}
                            if et == EventType.TEAM_MESSAGE.value and "event" in data:
                                event_data = data.get("event", {})
                                if isinstance(event_data, dict):
                                    for k, v in event_data.items():
                                        if k not in ("type", "timestamp", "content"):
                                            extra_fields[k] = v
                            append_history_record(
                                session_id=session_id,
                                request_id=rid,
                                channel_id=cid,
                                role="assistant",
                                event_type=et,
                                content=data.get("content") or data.get("error") or "",
                                timestamp=time.time(),
                                extra=extra_fields if extra_fields else None,
                                mode=request.params.get("mode", "unknown"),
                            )
                        if et == "chat.final":
                            final_answer_content = str(data.get("content", ""))
                        elif et == "chat.delta":
                            final_answer_chunks.append(str(data.get("content", "")))
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload=data,
                            is_complete=False,
                        )
        except asyncio.CancelledError:
            logger.info("[JiuWenClaw] 流式處理被中斷: request_id=%s", rid)
            raise

        # cloud memory: after chat hook
        if memory_mode == "cloud":
            assistant_message = final_answer_content or "".join(final_answer_chunks)
            after_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                assistant_message=assistant_message,
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={"is_complete": True},
            is_complete=True,
        )

    # ---------- 例項獲取 ----------

    def get_instance(self):
        return self._adapter._instance

    async def compress_context(self, session_id: str, session: Any = None) -> dict[str, Any]:
        """主動觸發上下文壓縮。

        Args:
            session_id: 會話ID
            session: Session 物件（可選）

        Returns:
            包含壓縮結果的字典:
            - result: "busy" | "compressed" | "noop"
            - stats: 壓縮統計資訊（僅當 result == "compressed" 時）
        """
        adapter = self._adapter
        if adapter is None:
            raise ValueError("Agent adapter not available")
        return await adapter.compress_context(
            session_id=session_id,
            session=session,
        )

    # ---------- 資源清理 ----------

    async def cancel_inflight_work(self, log_prefix: str = "[gateway disconnect] ") -> None:
        """Gateway 與 AgentServer 的 WebSocket 斷開時呼叫：取消 session 流式任務並中止 adapter 內層迴圈。"""
        await self._session_manager.cancel_all_session_tasks(log_prefix)
        adapter = self._adapter
        if adapter is None:
            return
        abort_fn = getattr(adapter, "abort_on_gateway_disconnect", None)
        if not callable(abort_fn):
            return
        try:
            await abort_fn()
        except Exception:
            logger.exception("[JiuWenClaw] adapter.abort_on_gateway_disconnect failed")

    async def cleanup(self) -> None:
        """清理資源，準備銷燬例項.

        每次 initialize 重建 agent 時呼叫。
        不清理記憶資料（記憶資料保留在檔案系統中）。
        """
        logger.info("[JiuWenClaw] cleanup: 清理資源")

        if self._adapter is not None:
            try:
                if hasattr(self._adapter, "cleanup"):
                    await self._adapter.cleanup()
            except Exception as e:
                logger.warning("[JiuWenClaw] Adapter cleanup failed: %s", e)
            self._adapter = None

        logger.info("[JiuWenClaw] cleanup: 完成")
