"""日誌模組 - 提供統一的日誌配置和管理。

本模組提供 agentsociety 專案的日誌功能：

主要函式
--------

- **get_logger**: 獲取 agentsociety logger 單例
- **set_logger_level**: 設定日誌級別
- **setup_logging**: 完整的日誌配置（控制檯 + 檔案）
- **setup_litellm_logging**: 配置 LiteLLM 日誌整合

特性
----

- 彩色控制檯輸出（按日誌級別著色）
- INFO 級別超長日誌自動截斷
- 支援檔案日誌輸出
- LiteLLM API 呼叫日誌整合

使用示例::

    from agentsociety2.logger import get_logger, setup_logging

    # 簡單使用
    logger = get_logger()
    logger.info("Hello, world!")

    # 完整配置
    setup_logging(log_file="app.log", log_level=logging.DEBUG)
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

__all__ = [
    "get_logger",
    "set_logger_level",
    "setup_litellm_logging",
    "setup_logging",
]

# 控制檯超長日誌：僅 INFO 截斷（保留前後），其他級別不截斷
_HEAD, _TAIL = 180, 180
_MAX_LEN = 500


def _shorten(msg: str, level: int) -> str:
    if level != logging.INFO or len(msg) <= _MAX_LEN:
        return msg
    return msg[:_HEAD] + " ... " + msg[-_TAIL:]


class ColoredFormatter(logging.Formatter):
    """按級別著色，僅 INFO 超長時截斷（保留前後）。格式 [HH:MM:SS] LEVEL  msg"""

    # 顏色方案：INFO 用青色減少視覺疲勞，WARNING/ERROR 用亮色提高可見度
    _colors = {
        logging.DEBUG: "\x1b[90m",  # 暗灰，DEBUG 通常量大，弱化
        logging.INFO: "\x1b[36m",  # 青色，大量 INFO 時比綠色更柔和
        logging.WARNING: "\x1b[93m",  # 亮黃，比普通黃更醒目
        logging.ERROR: "\x1b[91m",  # 亮紅，比普通紅更突出
        logging.CRITICAL: "\x1b[91;1m",  # 粗體亮紅，最高優先順序
    }
    _reset = "\x1b[0m"

    def __init__(self, fmt: str | None = None, datefmt: str | None = None):
        """初始化 ColoredFormatter

        Args:
            fmt: 日誌格式，預設為 "[%(asctime)s] %(levelname)-7s %(message)s"
            datefmt: 時間格式，預設為 "%Y-%m-%d %H:%M:%S"
        """
        super().__init__(fmt=fmt or "[%(asctime)s] %(levelname)-7s %(message)s", datefmt=datefmt or "%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        # 儲存原始訊息
        orig_msg, orig_args = record.msg, record.args
        # 縮短訊息
        msg = _shorten(record.getMessage(), record.levelno)
        record.msg, record.args = msg, ()

        # 先使用父類方法格式化（包含時間）
        formatted = super().format(record)

        # 新增顏色
        use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        if use_color:
            color = self._colors.get(record.levelno, "")
            # 為整行新增顏色（時間、級別、訊息）
            formatted = color + formatted + self._reset

        # 恢復原始訊息
        record.msg, record.args = orig_msg, orig_args
        return formatted


def get_logger():
    """獲取 agentsociety logger 單例。

    :returns: :class:`logging.Logger`（name 固定為 ``agentsociety``）。
    """
    logger = logging.getLogger("agentsociety")
    # check if there is already a handler, avoid duplicate output
    if not logger.hasHandlers():
        logger.setLevel(logging.INFO)
        # set propagate to False, avoid duplicate output
        logger.propagate = False

        handler = logging.StreamHandler()
        # Use our custom ColoredFormatter
        handler.setFormatter(ColoredFormatter())
        logger.addHandler(handler)
    return logger


def add_file_handler(log_file: str, level: int = logging.INFO) -> None:
    """為 agentsociety logger 新增檔案處理器（冪等）。

    :param log_file: 日誌檔案路徑。
    :param level: 日誌級別。
    """
    logger = logging.getLogger("agentsociety")

    # 檢查是否已經有相同檔案的檔案處理器
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == os.path.abspath(log_file):
            return  # 已存在，不重複新增

    # 建立日誌目錄（如果不存在）
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # 建立檔案處理器（使用不帶顏色的簡單格式）
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(level)
    # 檔案中使用簡單格式，不帶顏色
    file_formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)


def set_logger_level(level: str):
    """設定 agentsociety logger 的日誌級別。

    :param level: 傳給 :meth:`logging.Logger.setLevel` 的級別。
    """
    get_logger().setLevel(level)


class LiteLLMLogger:
    """
    Custom logger for LiteLLM that logs prompts, responses, tokens, and timing.

    This class implements the callback interface for LiteLLM to log:
    - Prompts at DEBUG level
    - Responses at INFO level
    - Token usage and timing at INFO level
    """

    def __init__(self):
        self.logger = get_logger()
        self._call_start_times: Dict[str, float] = {}

    def log_pre_api_call(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
    ):
        """在 API 呼叫前記錄 prompt（DEBUG）。"""
        # Generate a unique call ID for tracking
        call_id = f"{model}_{int(time.time() * 1000000)}"
        self._call_start_times[call_id] = time.time()

        prompt_text = self._format_messages(messages)
        prompt_text = _shorten(prompt_text, logging.DEBUG)
        self.logger.debug(f"[LiteLLM] {model} | Prompt:\n{prompt_text}")

        # Store call_id in kwargs for later retrieval
        kwargs["_litellm_call_id"] = call_id

    def log_post_api_call(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: float,
        end_time: float,
    ):
        """在 API 呼叫後記錄 response / tokens / timing（INFO）。"""
        duration = end_time - start_time
        model = kwargs.get("model", "unknown")

        # Extract response content
        response_content = self._extract_response_content(response_obj)

        # Extract token usage
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0

        if hasattr(response_obj, "usage") and response_obj.usage:
            input_tokens = getattr(response_obj.usage, "prompt_tokens", 0) or 0
            output_tokens = getattr(response_obj.usage, "completion_tokens", 0) or 0
            total_tokens = getattr(response_obj.usage, "total_tokens", 0) or 0
        elif isinstance(response_obj, dict):
            usage = response_obj.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or 0

        response_content = _shorten(response_content, logging.INFO)
        self.logger.info(f"[LiteLLM] {model} | Response:\n{response_content}")

        # Log token usage and timing at INFO level
        self.logger.info(
            f"[LiteLLM] Model: {model} | Tokens - Input: {input_tokens}, "
            f"Output: {output_tokens}, Total: {total_tokens} | "
            f"Duration: {duration:.3f}s"
        )

    def _format_messages(self, messages: List[Dict[str, Any]]) -> str:
        """將 messages 格式化為可讀字串。"""
        formatted_parts = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Handle different content types
            if isinstance(content, list):
                # Handle content array (e.g., multimodal)
                content_str = json.dumps(content, ensure_ascii=False, indent=2)
            elif isinstance(content, str):
                content_str = content
            else:
                content_str = str(content)

            formatted_parts.append(f"[{role}]: {content_str}")

            # Include tool_calls if present
            if "tool_calls" in msg:
                tool_calls = msg["tool_calls"]
                formatted_parts.append(
                    f"  Tool calls: {json.dumps(tool_calls, ensure_ascii=False, indent=2)}"
                )

        return "\n".join(formatted_parts)

    def _extract_response_content(self, response_obj: Any) -> str:
        """從 LiteLLM 響應物件中提取主要內容文字。"""
        try:
            if hasattr(response_obj, "choices") and response_obj.choices:
                choice = response_obj.choices[0]
                if hasattr(choice, "message"):
                    content = getattr(choice.message, "content", None)
                    if content:
                        return content
                    # Check for tool calls
                    if hasattr(choice.message, "tool_calls"):
                        tool_calls = choice.message.tool_calls
                        if tool_calls:
                            return f"[Tool calls: {len(tool_calls)}]"
            elif isinstance(response_obj, dict):
                choices = response_obj.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content")
                    if content:
                        return content
                    # Check for tool calls
                    tool_calls = message.get("tool_calls")
                    if tool_calls:
                        return f"[Tool calls: {len(tool_calls)}]"
        except Exception as e:
            return f"[Error extracting response: {str(e)}]"

        return "[Empty or unknown response]"


def setup_litellm_logging():
    """配置 LiteLLM 日誌整合。

    預設不啟用 callbacks（避免部分版本的序列化告警）；可透過環境變數
    ``AGENTSOCIETY_ENABLE_LITELLM_CALLBACKS=1`` 啟用。
    """
    try:
        import litellm

        # NOTE:
        # Some LiteLLM versions can emit noisy Pydantic serialization warnings
        # when callback pipelines serialize provider-specific message objects.
        # Keep callback registration disabled by default to avoid that path.
        # If detailed callback logs are needed, enable via env:
        #   AGENTSOCIETY_ENABLE_LITELLM_CALLBACKS=1
        if os.getenv("AGENTSOCIETY_ENABLE_LITELLM_CALLBACKS", "").strip() == "1":
            litellm_logger = LiteLLMLogger()
            litellm.callbacks = [litellm_logger]
        else:
            litellm.callbacks = []

        # Configure litellm's internal logger
        litellm_logging = logging.getLogger("litellm")
        litellm_logging.handlers.clear()
        litellm_logging.propagate = False
        litellm_logging.setLevel(logging.WARNING)

        get_logger().info(
            "LiteLLM logging initialized (callbacks=%s)", litellm.callbacks
        )
    except ImportError:
        get_logger().warning("litellm not available, skipping LiteLLM logging setup")
    except Exception as e:
        get_logger().warning(f"Failed to setup LiteLLM logging: {e}")


def setup_logging(
    log_file: Optional[str] = None,
    log_level: int = logging.INFO,
    log_format: Optional[str] = None,
    console_output: bool = True,
) -> logging.Logger:
    """初始化應用日誌（root + agentsociety + LiteLLM）。

    :param log_file: 可選。日誌檔案路徑；為空則只輸出到控制檯。
    :param log_level: 日誌級別。
    :param log_format: 可選。自定義格式化串；為空則使用預設格式。
    :param console_output: 是否輸出到控制檯。
    :returns: 配置後的 agentsociety logger 例項。
    """
    # Default format
    if log_format is None:
        file_formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    else:
        file_formatter = logging.Formatter(log_format)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    root_logger.handlers.clear()

    # Add file handler if log_file is provided
    if log_file:
        # Create logs directory if needed
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Add console handler if requested
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(ColoredFormatter())
        root_logger.addHandler(console_handler)

    # Configure agentsociety logger
    logger = get_logger()
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.propagate = False

    # Add handlers to agentsociety logger
    if log_file:
        file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(ColoredFormatter())
        logger.addHandler(console_handler)

    # Setup LiteLLM logging
    setup_litellm_logging()

    # Configure litellm internal logger to avoid duplicate logs
    litellm_logger = logging.getLogger("litellm")
    litellm_logger.setLevel(logging.WARNING)
    litellm_logger.handlers.clear()
    litellm_logger.propagate = False

    return logger
