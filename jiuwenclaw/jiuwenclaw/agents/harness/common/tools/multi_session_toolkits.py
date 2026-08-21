# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Session Toolkit
生命週期：Agent建立新session開始，到所有session協程結束

在結束後，MultiSessionToolkit所有內容

Agent 可以透過以下工具操控協程
1. create_new_sessions
接收一個任務描述的列表，對列表裡每一個任務，建立一個agent例項，並透過Runner執行該agent，同時把session資訊記錄在self.sessions中
2. cancel_session
根據session_id取消對應協程
3. list_all_sessions
檢視所有協程資訊

協程管理原則：
1. 協程建立後，任務資訊儲存在self.sessions中
2. 協程取消後，對應資訊需要同步在self.sessions中
3. 某一協程結束後，會呼叫notify方法，透過MessageHandler將訊息傳送出去
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from enum import Enum
from typing import Dict, List

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig, AgentCard
from pydantic import BaseModel

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenclaw.agents.harness.common.tools.mcp_toolkits import get_mcp_tools
from jiuwenclaw.gateway.message_handler.message_handler import MessageHandler

logger = logging.getLogger(__name__)


class Status(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class SessionTask(BaseModel):
    session_id: str
    description: str
    status: Status
    result: str = ""


class MultiSessionToolkit:
    """Toolkit for multi-session agent task tracking. Supports parallel sub-agent execution."""

    def __init__(self, session_id: str, channel_id: str, request_id: str, sub_agent_config: ReActAgentConfig) -> None:
        """Initialize MultiSessionToolkit for a session.

        Args:
            session_id: Parent session/conversation identifier.
            channel_id: Channel ID for routing notify messages back to parent.
        """
        self.session_id = session_id
        self.channel_id = channel_id
        self.request_id = request_id
        self.sessions: List[SessionTask] = []
        self._tasks: Dict[str, asyncio.Task] = {}
        self._sub_agent_config: ReActAgentConfig = sub_agent_config
        logger.info(
            "[MultiSessionToolkit] 初始化 parent_session_id=%s channel_id=%s request_id=%s",
            session_id,
            channel_id,
            request_id,
        )

    async def get_sub_agent(self) -> ReActAgent:
        """Create and return a sub-agent instance. Override in subclass."""
        logger.debug("[MultiSessionToolkit] get_sub_agent 建立子 agent")
        agent_card = AgentCard(
            name="spawn_sub_agent"
        )
        agent = ReActAgent(agent_card)
        agent.configure(self._sub_agent_config)
        mcp_tools = get_mcp_tools()
        for mcp_tool in mcp_tools:
            Runner.resource_mgr.add_tool(mcp_tool)
            agent.ability_manager.add(mcp_tool.card)
        logger.debug("[MultiSessionToolkit] get_sub_agent 完成 mcp_tools_count=%d", len(mcp_tools))
        return agent

    async def _run_and_notify(
            self,
            session_id: str,
            description: str,
            agent: ReActAgent,
            inputs: dict,
    ) -> None:
        """Run agent and call notify on completion (success/cancel/error)."""
        logger.debug(
            "[MultiSessionToolkit] _run_and_notify 開始 session_id=%s description=%s",
            session_id,
            description[:80] + "..." if len(description) > 80 else description,
        )
        task = SessionTask(
            session_id=session_id,
            description=description,
            status=Status.RUNNING,
            result="",
        )
        self.sessions.append(task)

        try:
            result = await Runner.run_agent(agent, inputs)
            result_str = result.get("output", "") if isinstance(result, dict) else str(result)
            logger.info(
                "[MultiSessionToolkit] 協程完成 session_id=%s status=completed result_len=%d",
                session_id,
                len(result_str),
            )
            self._update_session(session_id, Status.COMPLETED, result_str)
            await self.notify(session_id, Status.COMPLETED, result=result_str)
        except asyncio.CancelledError:
            logger.info("[MultiSessionToolkit] 協程已取消 session_id=%s", session_id)
            self._update_session(session_id, Status.CANCELLED, "任務已取消")
            await self.notify(session_id, Status.CANCELLED)
            raise
        except Exception as e:
            err_str = str(e)
            logger.exception(
                "[MultiSessionToolkit] 協程異常 session_id=%s error=%s",
                session_id,
                err_str,
            )
            self._update_session(session_id, Status.ERROR, err_str)
            await self.notify(session_id, Status.ERROR, error=err_str)
            raise
        finally:
            self._tasks.pop(session_id, None)
            logger.debug(
                "[MultiSessionToolkit] _run_and_notify 結束 session_id=%s 剩餘協程數=%d",
                session_id, len(self._tasks)
            )

    def _update_session(self, session_id: str, status: Status, result: str = "") -> None:
        """Update session task status in self.sessions."""
        for st in self.sessions:
            if st.session_id == session_id:
                st.status = status
                st.result = result
                logger.debug(
                    "[MultiSessionToolkit] _update_session session_id=%s status=%s",
                    session_id,
                    status.value,
                )
                break

    async def notify(
            self,
            session_id: str,
            status: Status,
            result: str = "",
            error: str = "",
    ) -> None:
        """Send subtask update to MessageHandler. Called on completion (success/cancel/error)."""
        try:
            mh = MessageHandler.get_instance()
        except RuntimeError as e:
            logger.warning(
                "[MultiSessionToolkit] MessageHandler 未初始化，跳過 notify: session_id=%s %s",
                session_id,
                e,
            )
            return

        st = next((s for s in self.sessions if s.session_id == session_id), None)
        description = st.description if st else ""
        index = next((i for i, s in enumerate(self.sessions) if s.session_id == session_id), 0)
        total = len(self.sessions)

        # 前端 SubtaskStatus: 'completed' | 'error'，cancelled 對映為 error
        if status == Status.COMPLETED:
            payload_status = "completed"
            message = result or ""
        else:
            payload_status = "error"
            message = error or "任務已取消" if status == Status.CANCELLED else error
        payload = {
            "event_type": "chat.session_result",
            "session_id": session_id,
            "description": description,
            "status": payload_status,
            "index": index + 1,
            "total": total,
            "result": message,
            "is_parallel": True,
        }
        msg = {
            "request_id": self.request_id,
            "channel_id": self.channel_id,
            "session_id": self.session_id,
            "payload": payload,
            "is_complete": False,
        }
        logger.debug(
            "[MultiSessionToolkit] notify 傳送 subtask_update session_id=%s status=%s index=%d/%d",
            session_id,
            payload_status,
            index + 1,
            total,
        )
        from jiuwenclaw.server.agent_ws_server import AgentWebSocketServer
        server = AgentWebSocketServer.get_instance()
        await server.send_push(msg)

        if self.all_tasks_done():
            session_result_summary = "後臺會話任務均已完成：\n"
            for st in self.sessions:
                session_result_summary += (f"\nsession_id: {st.session_id}\n"
                                           f"description: {st.description}\nresult: {st.result}\n")
            inputs = {
                "conversation_id": self.session_id,
                "query": json.dumps({
                    "source": "system",
                    "content": session_result_summary,
                    "type": "notify"
                }),
            }
            # 使用 run_agent_streaming 而非 run_agent，以確保 session.post_run() 被呼叫，
            # 從而將對話歷史持久化到 checkpoint。run_agent 不會建立 Session 或呼叫 post_run，
            # 導致 notify 中的 agent 對話未儲存。
            accumulated: list[str] = []
            final_output: str | None = None
            async for chunk in Runner.run_agent_streaming(
                    server.get_agent(),
                    inputs=inputs,
            ):
                if not hasattr(chunk, "type") or not hasattr(chunk, "payload"):
                    continue
                payload = chunk.payload if isinstance(chunk.payload, dict) else {}
                if chunk.type == "content_chunk":
                    c = payload.get("content", "")
                    if c:
                        accumulated.append(str(c))
                elif chunk.type == "answer":
                    out = payload.get("output")
                    if isinstance(out, dict):
                        temp = out.get("output", str(out)) or "".join(accumulated)
                        if temp != "":
                            final_output = temp
                    elif out is not None:
                        final_output = str(out)
                    else:
                        final_output = "".join(accumulated) if accumulated else ""
            result = {
                "output": final_output if final_output is not None else "".join(accumulated),
                "result_type": "answer",
            }
            payload = {
                "event_type": "chat.final",
                "task_id": self.session_id,
                "content": result,
            }
            msg = {
                "request_id": self.request_id,
                "channel_id": self.channel_id,
                "session_id": self.session_id,
                "payload": payload,
                "is_complete": True,
            }
            await server.send_push(msg)

    async def create_new_sessions(self, task_descriptions: List[str]) -> str:
        """Create sub-agent sessions for each task description."""
        logger.info(
            "[MultiSessionToolkit] create_new_sessions 開始 parent_session_id=%s 任務數=%d",
            self.session_id,
            len(task_descriptions),
        )
        created = []
        for i, task_description in enumerate(task_descriptions):
            session_id = f"spawn_{time.monotonic_ns()}_{secrets.token_hex(4)}"
            logger.debug(
                "[MultiSessionToolkit] 建立協程 [%d/%d] session_id=%s description=%s",
                i + 1,
                len(task_descriptions),
                session_id,
                task_description[:60] + "..." if len(task_description) > 60 else task_description,
            )
            agent = await self.get_sub_agent()
            inputs = {
                "conversation_id": session_id,
                "query": task_description,
            }
            coro = self._run_and_notify(session_id, task_description, agent, inputs)
            task = asyncio.create_task(coro)
            self._tasks[session_id] = task
            created.append(session_id)
        logger.info(
            "[MultiSessionToolkit] create_new_sessions 完成 已建立 %d 個協程: %s",
            len(created),
            ", ".join(created),
        )
        return f"已建立 {len(created)} 個協程: {', '.join(created)}"

    async def cancel_session(self, session_id: str) -> str:
        """Cancel a running session by session_id."""
        logger.info(
            "[MultiSessionToolkit] cancel_session 請求 parent_session_id=%s target_session_id=%s",
            self.session_id,
            session_id,
        )
        task = self._tasks.get(session_id)
        if task is None:
            logger.warning(
                "[MultiSessionToolkit] cancel_session 未找到 session_id=%s 當前協程: %s",
                session_id,
                list(self._tasks.keys()),
            )
            return f"未找到 session_id={session_id}"
        if task.done():
            logger.info("[MultiSessionToolkit] cancel_session session_id=%s 已結束，無需取消", session_id)
            return f"session_id={session_id} 已結束"
        task.cancel()
        try:
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        logger.info("[MultiSessionToolkit] cancel_session 已取消 session_id=%s", session_id)
        return f"已取消 session_id={session_id}"

    async def list_all_sessions(self) -> str:
        """List all session tasks with status."""
        logger.debug(
            "[MultiSessionToolkit] list_all_sessions parent_session_id=%s 協程數=%d",
            self.session_id,
            len(self.sessions),
        )
        if not self.sessions:
            return "暫無協程"
        lines = []
        for st in self.sessions:
            lines.append(f"{st.session_id} | {st.description} | {st.status.value} | {st.result}")
        return "\n".join(lines)

    def get_tools(self) -> List[Tool]:
        """Return tools for registration in Runner."""
        session_id = self.session_id

        def make_tool(
                name: str,
                description: str,
                input_params: dict,
                func,
        ) -> Tool:
            card = ToolCard(
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="session_new",
                description=(
                    "建立多個協程任務。接收任務描述列表，每個任務建立一個子 agent 並非同步執行。"
                    "協程完成後會透過 notify 傳送結果。"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "task_descriptions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "任務描述列表",
                        }
                    },
                    "required": ["task_descriptions"],
                },
                func=self.create_new_sessions,
            ),
            make_tool(
                name="session_cancel",
                description="根據 session_id 取消正在執行的協程。",
                input_params={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "要取消的協程 session_id",
                        }
                    },
                    "required": ["session_id"],
                },
                func=self.cancel_session,
            ),
            make_tool(
                name="session_list",
                description="檢視所有協程列表及其狀態（session_id | description | status | result）。",
                input_params={"type": "object", "properties": {}},
                func=self.list_all_sessions,
            ),
        ]

    def all_tasks_done(self) -> bool:
        """判斷是否所有任務都已結束。"""
        return all([s.status in [Status.COMPLETED, Status.ERROR] for s in self.sessions])
