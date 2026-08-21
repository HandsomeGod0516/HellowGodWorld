# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Xiaoyi Handset Tools - 小藝手機端裝置工具.

該目錄包含需要連線小藝手機端裝置才能使用的工具。
這些工具透過 WebSocket 與手機端通訊，呼叫裝置原生能力。

工具分類：
- 定位: get_user_location
- 備忘錄: create_note, search_notes, modify_note
- 日曆: create_calendar_event, search_calendar_event
- 聯絡人: search_contact
- 相簿: search_photo_gallery, upload_photo
- 檔案: search_file, upload_file, send_file_to_user
- 電話: call_phone
- 簡訊/訊息: send_message, search_message
- 鬧鐘: create_alarm, search_alarms, modify_alarm, delete_alarm
- 收藏: query_collection, add_collection, delete_collection
- 儲存: save_media_to_gallery, save_file_to_file_manager
- 推送記錄: view_push_result
- GUI 自動化: xiaoyi_gui_agent
- 影象理解: image_reading
- 時間戳轉換: convert_timestamp_to_utc8_time
"""

from .location_tool import get_user_location
from .note_tools import create_note, search_notes, modify_note
from .calendar_tools import create_calendar_event, search_calendar_event
from .contact_tools import search_contact
from .photo_tools import search_photo_gallery, upload_photo
from .file_tools import search_file, upload_file
from .phone_tools import call_phone
from .message_tools import send_message, search_message
from .alarm_tools import create_alarm, search_alarms, modify_alarm, delete_alarm
from .xiaoyi_collection_tool import query_collection, add_collection, delete_collection
from .save_tools import save_media_to_gallery, save_file_to_file_manager
from .push_result_tool import view_push_result
from .timestamp_tool import convert_timestamp_to_utc8_time
from .xiaoyi_gui_tool import xiaoyi_gui_agent
from .image_reading_tool import image_reading

__all__ = [
    "get_user_location",
    "create_note",
    "search_notes",
    "modify_note",
    "create_calendar_event",
    "search_calendar_event",
    "search_contact",
    "search_photo_gallery",
    "upload_photo",
    "search_file",
    "upload_file",
    "call_phone",
    "send_message",
    "search_message",
    "create_alarm",
    "search_alarms",
    "modify_alarm",
    "delete_alarm",
    "query_collection",
    "add_collection",
    "delete_collection",
    "save_media_to_gallery",
    "save_file_to_file_manager",
    "view_push_result",
    "convert_timestamp_to_utc8_time",
    "xiaoyi_gui_agent",
    "image_reading",
]
