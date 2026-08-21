# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
與 openjiuwen 0.1.9+ ``openjiuwen.core.runner.callback.events`` 中 EventBase 對齊的最小 HookEventBase。

openjiuwen 0.1.7 未包含該模組，此處內建以便擴充套件事件名與作用域行為一致。

放在 schema 子包內，避免從 extensions 匯入時執行 extensions/__init__.py 引發迴圈依賴。
"""

from __future__ import annotations

DEFAULT_SCOPE = "_framework"


def build_event_name(scope: str, event_name: str) -> str:
    return f"{scope}:{event_name}"


def parse_event_name(scoped_event: str) -> tuple[str, str]:
    if ":" in scoped_event:
        scope, event_name = scoped_event.split(":", 1)
        return scope, event_name
    return DEFAULT_SCOPE, scoped_event


class HookEventBase:
    """帶 scope 的鉤子事件名基類（與 openjiuwen 0.1.9 EventBase 行為一致）。"""

    scope: str = DEFAULT_SCOPE

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for attr_name, attr_value in list(cls.__dict__.items()):
            if isinstance(attr_value, str) and ":" in attr_value:
                scope, event_name = parse_event_name(attr_value)
                if scope == DEFAULT_SCOPE and cls.scope != DEFAULT_SCOPE:
                    setattr(cls, attr_name, build_event_name(cls.scope, event_name))

    @classmethod
    def get_event(cls, event_name: str) -> str:
        return build_event_name(cls.scope, event_name)
