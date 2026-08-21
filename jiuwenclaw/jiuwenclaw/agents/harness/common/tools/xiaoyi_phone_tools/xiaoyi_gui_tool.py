# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""小藝 GUI 自動化（xiaoyi_gui_agent）：透過 InvokeJarvisGUIAgent 與裝置協同完成螢幕操作."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from jiuwenclaw.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import get_xiaoyi_channel

from .utils import ToolInputError, format_success_response


def _get_gui_tool_async_lock(channel: Any) -> asyncio.Lock:
    gl = getattr(channel, "gui_tool_lock", None)
    if gl is not None:
        return gl
    inner = getattr(channel, "_gui_tool_lock", None)
    if inner is None:
        inner = asyncio.Lock()
        setattr(channel, "_gui_tool_lock", inner)
    return inner


def _payload_is_gui_final(payload: Dict[str, Any]) -> bool:
    """相容裝置 isFinal 為 bool / 1 / \"true\" 等."""
    v = payload.get("isFinal")
    if v is True:
        return True
    if isinstance(v, (int, float)) and int(v) == 1:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes"):
        return True
    return False


@tool(
    name="xiaoyi_gui_agent",
    description=(
        "透過模擬手機螢幕互動（點選、滑動、輸入等）完成僅能在 App 內完成的操作。\n\n"
        "注意：超時約 3 分鐘；執行期間勿並行呼叫其他工具；備忘錄/日程請用專用工具而非寫入 query。"
        "引數 query：自然語言操作指令與期望結果。"
    ),
)
async def xiaoyi_gui_agent(query: str) -> Dict[str, Any]:
    """執行 GUI Agent 指令."""
    if not query or not isinstance(query, str) or not query.strip():
        raise ToolInputError("缺少有效引數 query（非空字串）")

    query = query.strip()
    channel = get_xiaoyi_channel()
    if channel is None:
        raise RuntimeError(
            "無活躍小藝會話，xiaoyi_gui_agent 僅能在小藝會話活躍時使用。"
        )

    session_id = ""
    task_id = ""
    last_message_id = ""
    try:
        from jiuwenclaw.common.config import get_config

        cfg = get_config()
        xiaoyi_conf = cfg.get("channels", {}).get("xiaoyi", {})
        session_id = (xiaoyi_conf.get("last_session_id") or "").strip()
        task_id = (xiaoyi_conf.get("last_task_id") or "").strip()
        last_message_id = (xiaoyi_conf.get("last_message_id") or "").strip()
    except Exception as e:
        logger.warning("[XIAOYI_GUI_TOOL] 讀取會話配置失敗: %s", e)

    if not session_id:
        raise RuntimeError(
            "無活躍小藝會話，xiaoyi_gui_agent 僅能在小藝會話活躍時使用。"
        )

    # 與 TS xiaoyi-gui-tool 一致：優先 taskId；空則回退 sessionId，避免 interactionId 為空
    interaction_id = task_id if task_id else session_id
    # JSON-RPC id 與當前使用者輪次對齊（見 XiaoyiChannel message/stream 寫入的配置）
    message_id = last_message_id or f"gui_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    logger.info(
        "[XIAOYI_GUI_TOOL] call session_id=%s interaction_id=%s rpc_id=%s",
        session_id[:12] + "..." if len(session_id) > 12 else session_id,
        interaction_id[:12] + "..." if len(interaction_id) > 12 else interaction_id,
        message_id[:32] + "..." if len(message_id) > 32 else message_id,
    )

    done = asyncio.Event()
    result_holder: Dict[str, Any] = {}

    def on_gui(item: dict[str, Any]) -> None:
        try:
            payload = item.get("payload") or {}
            riid = payload.get("interactionId")
            if riid is not None and str(riid).strip() != "":
                if str(riid).strip() != str(interaction_id).strip():
                    logger.debug(
                        "[XIAOYI_GUI_TOOL] 忽略非本單回包 interactionId=%r expected=%r",
                        riid,
                        interaction_id,
                    )
                    return
            if not _payload_is_gui_final(payload):
                logger.debug("[XIAOYI_GUI_TOOL] 非終幀，繼續等待 isFinal")
                return
            sc = (payload.get("streamInfo") or {}).get("streamContent")
            if sc:
                result_holder["streamContent"] = sc
            else:
                result_holder["error"] = "GUI 響應缺少 streamContent"
            done.set()
        except Exception as ex:
            logger.warning("[XIAOYI_GUI_TOOL] on_gui 異常（已隔離）: %s", ex, exc_info=True)
            result_holder["error"] = f"GUI 回撥異常: {ex}"
            done.set()

    # 與 channel 層鎖配合：同一時間僅一單 GUI，避免多 handler 共收同一 WS 幀
    async with _get_gui_tool_async_lock(channel):
        channel.register_gui_agent_handler(on_gui)
        try:
            command = {
                "header": {
                    "namespace": "ClawAgent",
                    "name": "InvokeJarvisGUIAgentRequest",
                },
                "payload": {
                    "query": query,
                    "sessionId": session_id,
                    "interactionId": interaction_id,
                },
            }
            logger.info("[XIAOYI_GUI_TOOL] sending InvokeJarvisGUIAgentRequest")
            sent = await channel.send_xiaoyi_phone_tools_command(
                session_id=session_id,
                task_id=task_id or session_id,
                message_id=message_id,
                command=command,
            )
            if not sent:
                raise RuntimeError("傳送 GUI 指令失敗，WebSocket 未連線")

            await asyncio.wait_for(done.wait(), timeout=180.0)

            err = result_holder.get("error")
            if err:
                raise RuntimeError(str(err))
            text = result_holder.get("streamContent", "")
            return format_success_response(
                {"success": True, "result": text},
                "GUI 操作完成",
            )
        except asyncio.TimeoutError as e:
            raise RuntimeError("小藝 GUI Agent 操作超時（3 分鐘）") from e
        finally:
            try:
                channel.unregister_gui_agent_handler(on_gui)
            except Exception as unreg_err:
                logger.warning(
                    "[XIAOYI_GUI_TOOL] unregister_gui_agent_handler: %s",
                    unreg_err,
                )
