"""預填充引數查詢API路由（只讀）

關聯檔案：
- @packages/agentsociety2/agentsociety2/backend/app.py - 主應用，註冊此路由 (/api/v1/prefill-params)
- @extension/src/prefillParamsViewProvider.ts - VSCode外掛前端呼叫此API
- @extension/src/webview/prefillParams/ - 前端展示元件

讀取檔案：
- {workspace}/.agentsociety/prefill_params.json - 預填充引數配置
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Literal

from fastapi import APIRouter, Query, HTTPException
from fastapi import Path as PathParam

from agentsociety2.logger import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/v1/prefill-params", tags=["prefill-params"])


def _load_prefill_params_file(workspace_path: str) -> Dict[str, Any]:
    """載入全域性預填充引數檔案"""
    prefill_file = Path(workspace_path) / ".agentsociety" / "prefill_params.json"

    if not prefill_file.exists():
        return {"version": "1.0", "env_modules": {}, "agents": {}}

    try:
        content = prefill_file.read_text(encoding="utf-8")
        return json.loads(content)
    except Exception as e:
        logger.error(f"Failed to load prefill params file: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to load prefill params file: {str(e)}"
        )


@router.get("")
async def get_prefill_params(
    workspace_path: str = Query(..., description="工作區路徑"),
) -> Dict[str, Any]:
    """
    獲取全域性預填充引數

    返回工作區中所有類（Agent和環境模組）的預填充引數配置。

    Args:
        workspace_path: 工作區根目錄路徑

    Returns:
        Dict[str, Any]: 預填充引數配置，包含：
            - success: 是否成功
            - data: 引數資料，結構為：
                - version: 配置版本
                - env_modules: 環境模組預填充引數字典
                - agents: Agent預填充引數字典

    Raises:
        HTTPException: 500 - 讀取配置檔案失敗

    Note:
        如果配置檔案不存在，返回空配置結構。
    """
    try:
        prefill_params = _load_prefill_params_file(workspace_path)
        return {"success": True, "data": prefill_params}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get prefill params: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get prefill params: {str(e)}"
        )


@router.get("/{class_kind}/{class_name}")
async def get_class_prefill_params(
    class_kind: Literal["env_module", "agent"] = PathParam(
        ..., description="類型別：env_module 或 agent"
    ),
    class_name: str = PathParam(
        ..., description="類名，如 mobility_space, basic_agent"
    ),
    workspace_path: str = Query(..., description="工作區路徑"),
) -> Dict[str, Any]:
    """
    獲取特定類的預填充引數

    返回指定類（Agent或環境模組）的預填充引數配置。

    Args:
        class_kind: 類型別，可選值：
            - env_module: 環境模組
            - agent: Agent類
        class_name: 類名，如 mobility_space, basic_agent 等
        workspace_path: 工作區根目錄路徑

    Returns:
        Dict[str, Any]: 類的預填充引數，包含：
            - success: 是否成功
            - class_kind: 類型別
            - class_name: 類名
            - params: 該類的預填充引數字典（如無配置則為空字典）

    Raises:
        HTTPException: 500 - 讀取配置檔案失敗

    Example:
        GET /api/v1/prefill-params/env_module/mobility_space?workspace_path=/path/to/workspace
    """
    try:
        prefill_params = _load_prefill_params_file(workspace_path)

        # 根據class_kind選擇對應的鍵
        params_key = "env_modules" if class_kind == "env_module" else "agents"
        class_params = prefill_params.get(params_key, {}).get(class_name, {})

        return {
            "success": True,
            "class_kind": class_kind,
            "class_name": class_name,
            "params": class_params,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get class prefill params: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get class prefill params: {str(e)}"
        )
