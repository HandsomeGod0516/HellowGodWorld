"""
Modules API router

Provides endpoints for listing available agent classes and environment modules.
Supports both built-in modules and custom modules from the workspace.

關聯檔案：
- @extension/src/apiClient.ts - API客戶端（呼叫getAgentClasses, getEnvModules）
- @extension/src/prefillParamsViewProvider.ts - 預填充引數檢視器
- @extension/src/simSettingsEditorProvider.ts - SIM_SETTINGS編輯器
- @packages/agentsociety2/agentsociety2/registry/ - 模組登錄檔

API端點：
- GET /api/v1/modules/agent_classes - 獲取所有可用的Agent類
- GET /api/v1/modules/env_module_classes - 獲取所有可用的Environment模組類
- GET /api/v1/modules/refresh - 重新整理模組列表（重新掃描）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, Query, HTTPException

from agentsociety2.logger import get_logger
from agentsociety2.registry import (
    get_registry,
    get_registered_env_modules,
    get_registered_agent_modules,
    scan_and_register_custom_modules,
)

logger = get_logger()

router = APIRouter(prefix="/api/v1/modules", tags=["modules"])


def _get_workspace_path() -> str:
    """Get workspace path from environment variable"""
    workspace_path = os.getenv("WORKSPACE_PATH")
    if not workspace_path:
        raise HTTPException(
            status_code=400,
            detail="WORKSPACE_PATH environment variable not set",
        )
    return workspace_path


def _load_custom_modules_if_needed() -> None:
    """Load custom modules if workspace is configured"""
    try:
        workspace_path = os.getenv("WORKSPACE_PATH")
        if workspace_path:
            registry = get_registry()
            # Only scan if not already loaded
            if not registry._custom_loaded:
                scan_and_register_custom_modules(Path(workspace_path), registry)
    except Exception as e:
        logger.warning(f"Failed to load custom modules: {e}")


@router.get("/agent_classes")
async def get_agent_classes(
    include_custom: bool = Query(True, description="是否包含自定義模組")
) -> Dict[str, Any]:
    """
    獲取所有可用的Agent類列表

    返回系統中所有已註冊的Agent類，包括內建和自定義模組。

    Args:
        include_custom: 是否包含自定義模組，預設True

    Returns:
        Dict[str, Any]: 包含Agent類資訊的響應：
            - success: 是否成功
            - agents: Agent類字典，鍵為型別名，值為：
                - type: 型別名
                - class_name: 類名
                - description: 描述
                - is_custom: 是否為自定義模組
            - count: Agent類總數

    Raises:
        HTTPException: 500 - 獲取Agent類失敗
    """
    try:
        _ = get_registry()  # 確保登錄檔已初始化

        # 載入自定義模組（如果需要）
        if include_custom:
            _load_custom_modules_if_needed()

        # 獲取所有已註冊的 Agent 類
        agents = {}
        for agent_type, agent_class in get_registered_agent_modules():
            try:
                description = agent_class.mcp_description()
            except Exception:
                description = f"{agent_class.__name__}: {agent_class.__doc__ or 'No description available'}"

            agents[agent_type] = {
                "type": agent_type,
                "class_name": agent_class.__name__,
                "description": description,
                "is_custom": getattr(agent_class, "_is_custom", False),
            }

        return {
            "success": True,
            "agents": agents,
            "count": len(agents),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent classes: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get agent classes: {str(e)}"
        )


@router.get("/env_module_classes")
async def get_env_module_classes(
    include_custom: bool = Query(True, description="是否包含自定義模組")
) -> Dict[str, Any]:
    """
    獲取所有可用的環境模組類列表

    返回系統中所有已註冊的環境模組類，包括內建和自定義模組。

    Args:
        include_custom: 是否包含自定義模組，預設True

    Returns:
        Dict[str, Any]: 包含環境模組類資訊的響應：
            - success: 是否成功
            - modules: 環境模組類字典，鍵為型別名，值為：
                - type: 型別名
                - class_name: 類名
                - description: 描述
                - is_custom: 是否為自定義模組
            - count: 模組類總數

    Raises:
        HTTPException: 500 - 獲取環境模組類失敗
    """
    try:
        _ = get_registry()  # 確保登錄檔已初始化

        # 載入自定義模組（如果需要）
        if include_custom:
            _load_custom_modules_if_needed()

        # 獲取所有已註冊的 Environment 模組類
        env_modules = {}
        for module_type, env_class in get_registered_env_modules():
            try:
                description = env_class.mcp_description()
            except Exception:
                description = f"{env_class.__name__}: {env_class.__doc__ or 'No description available'}"

            env_modules[module_type] = {
                "type": module_type,
                "class_name": env_class.__name__,
                "description": description,
                "is_custom": getattr(env_class, "_is_custom", False),
            }

        return {
            "success": True,
            "modules": env_modules,
            "count": len(env_modules),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get env module classes: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get env module classes: {str(e)}"
        )


@router.get("/all")
async def get_all_modules(
    include_custom: bool = Query(True, description="是否包含自定義模組")
) -> Dict[str, Any]:
    """
    獲取所有可用的模組類

    一次性返回所有Agent類和環境模組類，減少請求次數。

    Args:
        include_custom: 是否包含自定義模組，預設True

    Returns:
        Dict[str, Any]: 包含所有模組資訊的響應：
            - success: 是否成功
            - agents: Agent類字典
            - agent_count: Agent類數量
            - env_modules: 環境模組類字典
            - env_module_count: 環境模組類數量

    Raises:
        HTTPException: 500 - 獲取模組失敗
    """
    try:
        _ = get_registry()  # 確保登錄檔已初始化

        # 載入自定義模組（如果需要）
        if include_custom:
            _load_custom_modules_if_needed()

        # 獲取 Agent 類
        agents = {}
        for agent_type, agent_class in get_registered_agent_modules():
            try:
                description = agent_class.mcp_description()
            except Exception:
                description = f"{agent_class.__name__}: {agent_class.__doc__ or 'No description available'}"

            agents[agent_type] = {
                "type": agent_type,
                "class_name": agent_class.__name__,
                "description": description,
                "is_custom": getattr(agent_class, "_is_custom", False),
            }

        # 獲取 Environment 模組類
        env_modules = {}
        for module_type, env_class in get_registered_env_modules():
            try:
                description = env_class.mcp_description()
            except Exception:
                description = f"{env_class.__name__}: {env_class.__doc__ or 'No description available'}"

            env_modules[module_type] = {
                "type": module_type,
                "class_name": env_class.__name__,
                "description": description,
                "is_custom": getattr(env_class, "_is_custom", False),
            }

        return {
            "success": True,
            "agents": agents,
            "agent_count": len(agents),
            "env_modules": env_modules,
            "env_module_count": len(env_modules),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get all modules: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get all modules: {str(e)}"
        )
