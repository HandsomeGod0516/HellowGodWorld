# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 異常定義.

定義 Team 模組中使用的各種異常型別.
"""

from __future__ import annotations


class TeamError(Exception):
    """Team 基礎異常類."""
    pass


class TeamCreateError(TeamError):
    """Team 建立失敗."""
    pass


class TeamRecoverError(TeamError):
    """Team 恢復失敗."""
    pass


class TeamInteractError(TeamError):
    """Team 互動失敗."""
    pass


class TeamConfigError(TeamError):
    """Team 配置錯誤."""
    pass


class TeamMonitorError(TeamError):
    """Team Monitor 錯誤."""
    pass


class TeamSessionError(TeamError):
    """Team 會話錯誤."""
    pass


class TeamStorageError(TeamError):
    """Team 儲存錯誤."""
    pass
