# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import threading
import json
from typing import Any
from pathlib import Path
from jiuwenclaw.common.utils import logger, get_agent_memory_dir

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import ListMessageRequest
    from lark_oapi.api.contact.v3 import GetUserRequest
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    ListMessageRequest = None
    GetUserRequest = None

MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


class MessageStore:
    def __init__(self, api_client: Any = None, platform_adapter: Any = None):
        self._memory_dir = (
            get_agent_memory_dir() / "group_chat"
        )  # 群聊記憶目錄
        self._memory_file = self._memory_dir / "feishu_memory.json"  # 飛書記憶檔案路徑（相容舊邏輯）
        self._memory_lock = threading.Lock()  # 記憶檔案讀寫鎖
        self._api_client = api_client  # 飛書API客戶端
        self._platform_adapter = platform_adapter  # 平臺介面卡，用於獲取使用者資訊等

    def set_api_client(self, api_client: Any) -> None:
        """
        設定飛書API客戶端。

        Args:
            api_client: 飛書API客戶端例項
        """
        self._api_client = api_client
        logger.info("飛書API客戶端已設定")
    
    def set_platform_adapter(self, platform_adapter: Any) -> None:
        """
        設定平臺介面卡。

        Args:
            platform_adapter: 平臺介面卡例項
        """
        self._platform_adapter = platform_adapter
        logger.info("平臺介面卡已設定")
    
    def get_user_name_by_open_id(self, open_id: str) -> str:
        """
        獲取使用者名稱稱，優先使用平臺介面卡，如果不可用則使用本地API客戶端。

        Args:
            open_id: 使用者 open_id

        Returns:
            str: 使用者名稱
        """
        # 優先使用平臺介面卡（如果可用）
        if self._platform_adapter and hasattr(self._platform_adapter, 'get_user_name_by_open_id'):
            return self._platform_adapter.get_user_name_by_open_id(open_id)
        
        # 如果平臺介面卡不可用，返回空字串（因為原方法已被移除）
        return ""

    def _get_memory_file_path(self, chat_id: str) -> Path:
        """
        獲取指定群聊的記憶檔案路徑。

        Args:
            chat_id: 群聊ID

        Returns:
            Path: 記憶檔案路徑
        """
        return self._memory_dir / f"{chat_id}.json"

    def load_memory(self, chat_id: str | None = None) -> dict[str, list] | list:
        """
        載入飛書記憶檔案。

        Args:
            chat_id: 群聊ID，如果為None則載入所有記憶（相容舊邏輯）

        Returns:
            dict或list: 記憶資料
        """
        with self._memory_lock:
            # 如果指定了chat_id，載入該群聊的獨立記憶檔案
            if chat_id:
                memory_file = self._get_memory_file_path(chat_id)
                logger.info(f"[除錯] _load_memory: 群聊記憶檔案路徑={memory_file}, exists={memory_file.exists()}")
                if not memory_file.exists():
                    logger.info(f"[除錯] _load_memory: 群聊記憶檔案不存在，返回空列表: chat_id={chat_id}")
                    return []
                try:
                    with open(memory_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        logger.info(f"[除錯] _load_memory: 成功載入群聊記憶，訊息數={len(data)}, chat_id={chat_id}")
                        return data
                except Exception as e:
                    logger.warning(f"[除錯] 載入群聊記憶檔案失敗: {e}, chat_id={chat_id}")
                    return []
            
            # 相容舊邏輯：載入統一的feishu_memory.json
            logger.info(f"[除錯] _load_memory: 統一記憶檔案路徑={self._memory_file}, exists={self._memory_file.exists()}")
            if not self._memory_file.exists():
                logger.info(f"[除錯] _load_memory: 統一記憶檔案不存在，返回空字典")
                return {}
            try:
                with open(self._memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"[除錯] _load_memory: 成功載入統一記憶，會話數={len(data)}")
                    return data
            except Exception as e:
                logger.warning(f"[除錯] 載入飛書記憶檔案失敗: {e}")
                return {}

    def _save_memory(self, memory: dict[str, list] | list, chat_id: str | None = None) -> None:
        """
        儲存飛書記憶檔案。

        Args:
            memory: 記憶資料
            chat_id: 群聊ID，如果為None則儲存到統一的feishu_memory.json（相容舊邏輯）
        """
        with self._memory_lock:
            try:
                self._memory_dir.mkdir(parents=True, exist_ok=True)
                
                # 如果指定了chat_id，儲存到該群聊的獨立記憶檔案
                if chat_id:
                    memory_file = self._get_memory_file_path(chat_id)
                    with open(memory_file, "w", encoding="utf-8") as f:
                        json.dump(memory, f, ensure_ascii=False, indent=2)
                    logger.info(f"[除錯] _save_memory: 群聊記憶已儲存: {memory_file}, 訊息數={len(memory)}")
                else:
                    # 相容舊邏輯：儲存到統一的feishu_memory.json
                    with open(self._memory_file, "w", encoding="utf-8") as f:
                        json.dump(memory, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"儲存飛書記憶檔案失敗: {e}")

    @staticmethod
    def _parse_history_message_content(item: Any) -> str:
        """
        解析歷史訊息內容（從API返回的訊息物件）。

        Args:
            item: 飛書API返回的訊息物件

        Returns:
            str: 解析後的訊息內容
        """
        msg_type = getattr(item, "msg_type", "")

        if msg_type == "text":
            try:
                body = getattr(item, "body", None)
                if body and hasattr(body, "content"):
                    content_str = body.content
                    content_data = json.loads(content_str)
                    return content_data.get("text", "")
            except (json.JSONDecodeError, AttributeError):
                pass

            return getattr(item, "content", "") or ""
        elif msg_type == "interactive":
            try:
                body = getattr(item, "body", None)
                if body and hasattr(body, "content"):
                    content_str = body.content
                    content_data = json.loads(content_str)
                    if isinstance(content_data, dict):
                        elements = content_data.get("elements", [])
                        texts = []

                        def extract_text_from_elem(elem):
                            if isinstance(elem, dict):
                                tag = elem.get("tag", "")
                                if tag == "text":
                                    text_content = elem.get("text", "")
                                    if text_content:
                                        texts.append(text_content)
                                elif tag == "div":
                                    text_obj = elem.get("text", {})
                                    if isinstance(text_obj, dict):
                                        md_content = text_obj.get("content", "")
                                        if md_content:
                                            texts.append(md_content)
                            elif isinstance(elem, list):
                                for sub_elem in elem:
                                    extract_text_from_elem(sub_elem)

                        for elem in elements:
                            extract_text_from_elem(elem)

                        return "\n".join(texts) if texts else "[interactive card]"
            except (json.JSONDecodeError, AttributeError):
                pass

            return "[interactive]"
        else:
            return MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
    
    def _fetch_history_from_feishu(
        self, chat_id: str, start_time: int = 0
    ) -> list[dict]:
        """
        從飛書API拉取歷史訊息。

        Args:
            chat_id: 聊天ID
            start_time: 開始時間戳（毫秒），0表示拉取所有歷史

        Returns:
            list: 歷史訊息列表
        """
        if not self._api_client or not FEISHU_AVAILABLE:
            logger.warning("飛書API客戶端未初始化，無法拉取歷史訊息")
            return []

        try:
            builder = (
                ListMessageRequest.builder()
                .container_id_type("chat")
                .container_id(chat_id)
                .sort_type("ByCreateTimeAsc")
                .page_size(50)
            )

            if start_time > 0:
                builder.start_time(str(start_time))

            request = builder.build()
            response = self._api_client.im.v1.message.list(request)

            if not response.success():
                logger.warning(
                    f"拉取飛書歷史訊息失敗: code={response.code}, msg={response.msg}"
                )
                return []
            messages = []
            if response.data and response.data.items:
                for item in response.data.items:
                    msg_content = self._parse_history_message_content(item)
                    if msg_content:
                        open_id = ""
                        user_name = ""

                        sender = getattr(item, "sender", None)
                        if sender:
                            sender_id = getattr(sender, "id", None)
                            sender_id_type = getattr(sender, "id_type", None)

                            if sender_id and sender_id_type:
                                if sender_id_type == "open_id":
                                    open_id = sender_id
                                    user_name = self.get_user_name_by_open_id(
                                        sender_id
                                    )
                                elif sender_id_type == "app_id":
                                    user_name = f"bot_{sender_id}"

                        messages.append(
                            {
                                "message_id": getattr(item, "message_id", ""),
                                "content": msg_content,
                                "timestamp": getattr(item, "create_time", 0),
                                "msg_type": getattr(item, "msg_type", ""),
                                "open_id": open_id,
                                "user_name": user_name,
                            }
                        )

            logger.info(f"從飛書拉取了 {len(messages)} 條歷史訊息: chat_id={chat_id}")
            return messages

        except Exception as e:
            logger.warning(f"拉取飛書歷史訊息時發生異常: {e}")
            return []

    def _get_or_fetch_history(self, chat_id: str) -> list[dict]:
        """
        獲取或拉取會話歷史訊息。

        如果本地記憶中沒有該會話，則從飛書API拉取過去7天的歷史訊息。
        如果本地記憶中有該會話，則從最後一條訊息的時間戳開始拉取新訊息。

        Args:
            chat_id: 聊天ID

        Returns:
            list: 歷史訊息列表
        """
        from datetime import datetime, timedelta, timezone

        memory = self.load_memory(chat_id)
        memory_file = self._get_memory_file_path(chat_id)

        if not memory_file.exists():
            # 首次獲取：拉取過去7天的訊息
            logger.info(f"[除錯] 本地記憶檔案不存在，首次拉取過去7天曆史: chat_id={chat_id}")
            now = datetime.now(timezone.utc)
            start_time = int((now - timedelta(days=7)).timestamp() * 1000)
            history = self._fetch_history_from_feishu(chat_id, start_time=start_time)
            self._save_memory(history, chat_id)
            logger.info(f"[除錯] 首次拉取歷史訊息完成: chat_id={chat_id}, 訊息數={len(history)}")
            return history
        else:
            # 增量獲取：從最後一條訊息的時間戳開始拉取到今天
            logger.info(f"[除錯] 本地記憶檔案存在，進行增量更新: chat_id={chat_id}")

            if memory and len(memory) > 0:
                # 獲取最後一條訊息的時間戳
                last_timestamp = memory[-1].get("timestamp", 0)
                if last_timestamp:
                    # 從最後一條訊息的時間開始拉取
                    logger.info(f"[除錯] 從最後一條訊息時間開始拉取: last_timestamp={last_timestamp}")
                    new_messages = self._fetch_history_from_feishu(chat_id, start_time=last_timestamp)

                    # 合併新訊息（去重）
                    existing_ids = {msg.get("message_id") for msg in memory}
                    added_count = 0
                    for msg in new_messages:
                        if msg.get("message_id") not in existing_ids:
                            memory.append(msg)
                            added_count += 1

                    if added_count > 0:
                        self._save_memory(memory, chat_id)
                        logger.info(f"[除錯] 增量更新完成: chat_id={chat_id}, 新增訊息數={added_count}, 總訊息數={len(memory)}")
                    else:
                        logger.info(f"[除錯] 沒有新訊息需要新增: chat_id={chat_id}")

                    return memory

            # 如果沒有歷史訊息或無法獲取時間戳，返回現有記憶
            return memory if memory else []

    def add_message_to_memory(self, chat_id: str, message: dict) -> None:
        """
        將訊息新增到本地記憶。

        Args:
            chat_id: 聊天ID
            message: 訊息資料
        """
        # 載入該群聊的記憶
        history = self.load_memory(chat_id)

        open_id = message.get("open_id", "")
        user_name = self.get_user_name_by_open_id(open_id)
        message["user_name"] = user_name
        
        # 新增新訊息
        history.append(message)
        
        # 儲存到該群聊的獨立記憶檔案
        self._save_memory(history, chat_id)
        logger.info(f"[除錯] 新訊息已新增到群聊記憶: chat_id={chat_id}, 總訊息數={len(history)}")