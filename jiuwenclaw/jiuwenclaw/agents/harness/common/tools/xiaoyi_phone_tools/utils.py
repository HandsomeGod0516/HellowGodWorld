# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Utilities for xiaoyi handset tools.

提供裝置側工具的通用功能：
- 獲取 channel 例項
- 傳送 command 並等待響應
- 引數驗證
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from jiuwenclaw.common.utils import logger
from jiuwenclaw.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import get_xiaoyi_channel
from jiuwenclaw.common.config import get_config


def _is_data_event_status_success(status: Any) -> bool:
    """裝置 data-event 的 status 是否為成功（相容大小寫及部分別名）."""
    if status is True:
        return True
    if status is None or status is False:
        return False
    s = str(status).strip().lower()
    return s in ("success", "succeed", "successful", "ok")


def _outputs_top_level_code_ok(code: Any) -> bool:
    """outputs.code 表示成功或未攜帶錯誤碼（None 視為不按 code 判失敗）."""
    if code is None:
        return True
    if isinstance(code, bool):
        return bool(code)
    try:
        if isinstance(code, (int, float)) and int(code) == 0:
            return True
    except (TypeError, ValueError):
        pass
    return str(code).strip() == "0"


class ToolInputError(Exception):
    """工具輸入引數錯誤.

    丟擲此錯誤會讓框架返回 HTTP 400 而非 500，
    LLM 會將其識別為引數錯誤而非瞬時故障。
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.status = 400


async def execute_device_command(
    intent_name: str,
    command: Dict[str, Any],
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """執行裝置命令並等待響應.

    Args:
        intent_name: Intent 名稱，用於匹配響應
        command: Command 資料結構
        timeout: 超時時間（秒）

    Returns:
        包含 content 欄位的響應字典

    Raises:
        RuntimeError: 會話不存在或執行失敗
        ToolInputError: 引數錯誤
    """
    logger.info(f"[{intent_name}_TOOL] Starting execution")

    # 獲取 XiaoyiChannel 例項
    channel = get_xiaoyi_channel()
    if channel is None:
        logger.error(f"[{intent_name}_TOOL] FAILED: No active session found!")
        raise RuntimeError(
            f"No active XY session found. {intent_name} tool can only be used during an active conversation."
        )

    # 從 config 讀取 xiaoyi 通道的會話與任務標識
    session_id = ""
    task_id = ""
    message_id = f"cmd_{int(asyncio.get_event_loop().time() * 1000)}"

    try:
        config = get_config()
        xiaoyi_conf = config.get("channels", {}).get("xiaoyi", {})
        session_id = xiaoyi_conf.get("last_session_id", "")
        task_id = xiaoyi_conf.get("last_task_id", "")
    except Exception as e:
        logger.warning(f"[{intent_name}_TOOL] 獲取會話資訊失敗: {e}")

    if not session_id:
        logger.error(f"[{intent_name}_TOOL] FAILED: No valid session found!")
        raise RuntimeError(
            f"No active XY session found. {intent_name} tool can only be used during an active conversation."
        )

    logger.info(
        f"[{intent_name}_TOOL] Session context: session_id={session_id!r} "
        f"task_id={task_id!r} message_id={message_id!r}"
    )

    # 建立事件等待結果
    result_event = asyncio.Event()
    result_data: Optional[Dict[str, Any]] = None
    error_result: Optional[Exception] = None

    # 定義 data-event 處理器
    def on_data_event(event):
        nonlocal result_data, error_result
        logger.info(
            f"[{intent_name}_TOOL] Received data event: intent={event.intent_name}, "
            f"status={event.status}"
        )

        if event.intent_name == intent_name:
            logger.info(f"[{intent_name}_TOOL] Intent name matched! status={event.status}")

            # 裝置在無簡訊等場景常返回 outputs: {}，此處必須用「outputs is not None」判斷。
            if _is_data_event_status_success(event.status):
                if event.outputs is None:
                    error_result = RuntimeError(
                        "執行失敗: status=success 但 outputs 為 null"
                    )
                    logger.error(
                        f"[{intent_name}_TOOL] success 但 outputs 為 null"
                    )
                else:
                    result_data = event.outputs
                    keys = (
                        list(event.outputs.keys())
                        if isinstance(event.outputs, dict)
                        else []
                    )
                    logger.info(
                        f"[{intent_name}_TOOL] Execution successful, outputs keys={keys}"
                    )
            else:
                error_result = RuntimeError(f"執行失敗: {event.status}")
                out_preview = ""
                if isinstance(event.outputs, dict):
                    try:
                        out_preview = json.dumps(
                            event.outputs, ensure_ascii=False
                        )[:600]
                    except Exception:
                        out_preview = str(event.outputs)[:600]
                logger.error(
                    f"[{intent_name}_TOOL] Execution failed: status={event.status!r} "
                    f"outputs_preview={out_preview}"
                )

            result_event.set()
        else:
            logger.debug(
                f"[{intent_name}_TOOL] Intent name mismatch: expected={intent_name}, "
                f"got={event.intent_name}"
            )

    # 註冊處理器
    channel.register_data_event_handler(intent_name, on_data_event)

    try:
        # 傳送命令
        logger.info(f"[{intent_name}_TOOL] Sending command...")
        sent = await channel.send_xiaoyi_phone_tools_command(
            session_id=session_id,
            task_id=task_id or session_id,
            message_id=message_id,
            command=command,
        )

        if not sent:
            raise RuntimeError("傳送指令失敗，WebSocket 未連線")

        # 等待響應
        logger.info(f"[{intent_name}_TOOL] Waiting for response (timeout: {timeout}s)...")
        await asyncio.wait_for(result_event.wait(), timeout=timeout)

        if error_result:
            logger.info(f"[{intent_name}_TOOL] Response error_result = {error_result}")
            raise error_result

        logger.info(f"[{intent_name}_TOOL] Response result_event = {result_event}")
        logger.info(f"[{intent_name}_TOOL] Response result_data = {result_data}")

        # 成功時 outputs 可能為 {}，勿用「or」短路（空 dict 在 Python 中為假）
        return {} if result_data is None else result_data

    except asyncio.TimeoutError as e:
        logger.error(
            f"[{intent_name}_TOOL] Timeout: no response within {timeout}s"
        )
        raise RuntimeError(
            f"裝置命令超時（{timeout}s 內未收到 {intent_name} 響應）"
        ) from e

    finally:
        channel.unregister_data_event_handler(intent_name, on_data_event)


def raise_if_device_error(outputs: Any, what_failed: str) -> None:
    """若裝置 outputs 含失敗 code 或 retErrCode，丟擲 RuntimeError.

    部分 Intent 使用 code，部分使用 retErrCode（字串 \"0\" 表示成功）。
    """
    if not isinstance(outputs, dict):
        return
    code = outputs.get("code")
    if not _outputs_top_level_code_ok(code):
        error_msg = outputs.get("errorMsg") or outputs.get("errMsg") or "未知錯誤"
        raise RuntimeError(
            f"{what_failed}: {error_msg} (錯誤程式碼: {code})"
        ) from None
    ret = outputs.get("retErrCode")
    if ret is not None and str(ret) != "0":
        err_msg = outputs.get("errMsg", "未知錯誤")
        raise RuntimeError(
            f"{what_failed}: {err_msg} (retErrCode: {ret})"
        ) from None


def validate_required_params(params: Dict[str, Any], required: list[str]) -> None:
    """驗證必填引數.

    Args:
        params: 引數字典
        required: 必填引數名列表

    Raises:
        ToolInputError: 缺少必填引數
    """
    for param_name in required:
        value = params.get(param_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ToolInputError(f"缺少必填引數 {param_name}")


def format_success_response(data: Dict[str, Any], message: str = "") -> Dict[str, Any]:
    """格式化成功響應.

    Args:
        data: 響應資料
        message: 可選的訊息

    Returns:
        包含 content 的響應字典
    """

    response = {"success": True, **data}
    if message:
        response["message"] = message

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(response, ensure_ascii=False),
            }
        ]
    }


def format_error_response(error: str) -> Dict[str, Any]:
    """格式化錯誤響應.

    Args:
        error: 錯誤資訊

    Returns:
        包含 content 的錯誤響應字典
    """

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"success": False, "error": error}, ensure_ascii=False),
            }
        ]
    }
