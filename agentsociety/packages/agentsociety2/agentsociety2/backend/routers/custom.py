"""
自定義模組 API 路由

提供掃描、清理、測試自定義模組的 API 端點。

關聯檔案：
- @extension/src/projectStructureProvider.ts - 前端專案結構檢視（呼叫此API）
- @extension/src/apiClient.ts - API客戶端

API端點：
- POST /api/v1/custom/scan - 掃描自定義模組並生成JSON配置
- POST /api/v1/custom/clean - 清理自定義模組配置
- POST /api/v1/custom/test - 測試自定義模組
- GET /api/v1/custom/list - 列出已註冊的自定義模組
- GET /api/v1/custom/status - 獲取自定義模組狀態

內部服務：
- @packages/agentsociety2/agentsociety2/backend/services/custom/scanner.py - 模組掃描
- @packages/agentsociety2/agentsociety2/backend/services/custom/generator.py - JSON生成
- @packages/agentsociety2/agentsociety2/registry/ - 模組登錄檔
"""

from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os
import json

# agentsociety2 是一個 Python 包，透過 import 使用
from agentsociety2.backend.services.custom.scanner import CustomModuleScanner
from agentsociety2.backend.services.custom.generator import CustomModuleJsonGenerator
from agentsociety2.backend.services.custom.script_generator import ScriptGenerator
from agentsociety2.registry import (
    get_registered_env_modules,
    get_registered_agent_modules,
    get_registry,
    register_scanned_custom_modules,
    scan_and_register_custom_modules,
)
from agentsociety2.logger import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/v1/custom", tags=["custom"])


# ========== 請求/響應模型 ==========


class ScanRequest(BaseModel):
    """掃描請求"""

    workspace_path: Optional[str] = Field(
        None, description="工作區路徑，不提供則使用環境變數"
    )


class ScanResponse(BaseModel):
    """掃描響應"""

    success: bool
    agents_found: int
    envs_found: int
    agents_generated: int
    envs_generated: int
    errors: List[str] = Field(default_factory=list)
    agent_diagnostics: List[Dict[str, Any]] = Field(default_factory=list)
    env_diagnostics: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


class CleanResponse(BaseModel):
    """清理響應"""

    success: bool
    removed_count: int
    message: str


class TestRequest(BaseModel):
    """測試請求"""

    workspace_path: Optional[str] = Field(
        None, description="工作區路徑，不提供則使用環境變數"
    )
    module_kind: Optional[str] = Field(
        None, description="模組型別: 'agent' 或 'env_module'，不提供則測試所有"
    )
    module_class_name: Optional[str] = Field(
        None, description="要測試的類名，與 module_kind 配合使用"
    )


class ModuleTestResult(BaseModel):
    """單個模組測試結果"""

    name: str
    module_kind: str = "env_module"
    success: bool
    output: str
    error: Optional[str] = None
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TestResponse(BaseModel):
    """測試響應"""

    success: bool
    test_output: str
    error: Optional[str] = None
    returncode: Optional[int] = None
    results: List[ModuleTestResult] = Field(default_factory=list)
    total_tests: Optional[int] = None
    passed_tests: Optional[int] = None
    failed_tests: Optional[int] = None


class ListResponse(BaseModel):
    """列表響應"""

    success: bool
    agents: List[Dict[str, Any]]
    envs: List[Dict[str, Any]]
    total_agents: int
    total_envs: int


# ========== API 端點 ==========


@router.post("/scan", response_model=ScanResponse)
async def scan_custom_modules(request: ScanRequest):
    """
    掃描自定義模組並註冊到記憶體

    掃描工作區的 custom/agents/ 和 custom/envs/ 目錄（跳過 examples/ 子目錄），
    驗證發現的模組並將其直接註冊到記憶體中的 registry。

    Args:
        request: 掃描請求，包含：
            - workspace_path: 工作區路徑（可選，不提供則使用環境變數）

    Returns:
        ScanResponse: 掃描結果，包含：
            - success: 是否成功
            - agents_found: 發現的Agent數量
            - envs_found: 發現的環境模組數量
            - agents_generated: 成功註冊的Agent數量
            - envs_generated: 成功註冊的環境模組數量
            - errors: 錯誤資訊列表
            - message: 結果訊息

    Raises:
        HTTPException: 400 - 未提供工作區路徑
        HTTPException: 500 - 掃描失敗

    Note:
        此介面不會生成JSON配置檔案，模組僅註冊到記憶體中。
        如需持久化配置，請使用 /api/v1/custom/classes 端點。
    """
    workspace_path = request.workspace_path or os.getenv("WORKSPACE_PATH")
    if not workspace_path:
        raise HTTPException(
            status_code=400,
            detail="Workspace path not provided. Set WORKSPACE_PATH env var or pass in request.",
        )

    try:
        logger.info(f"[Custom Modules] Starting scan of workspace: {workspace_path}")

        scanner = CustomModuleScanner(workspace_path)
        scan_result = scanner.scan_all()

        logger.info(
            f"[Custom Modules] Scan complete: {len(scan_result['agents'])} agents, "
            f"{len(scan_result['envs'])} envs found"
        )

        registry = get_registry()
        registry.clear_custom_modules()
        scan_result = register_scanned_custom_modules(scan_result, registry)
        scan_result["errors"].extend(scan_result.get("registration_errors", []))

        message_parts = []
        agents_count = len(scan_result.get("agents", []))
        envs_count = len(scan_result.get("envs", []))

        if agents_count > 0:
            message_parts.append(f"發現 {agents_count} 個 Agent")
        if envs_count > 0:
            message_parts.append(f"發現 {envs_count} 個環境模組")

        if not message_parts:
            message = "未發現任何自定義模組"
        else:
            message = "、".join(message_parts) + "，已註冊到記憶體"

        logger.info(f"[Custom Modules] Scan complete: {message}")

        return ScanResponse(
            success=True,
            agents_found=len(scan_result["agents"]),
            envs_found=len(scan_result["envs"]),
            agents_generated=agents_count,
            envs_generated=envs_count,
            errors=scan_result.get("errors", []),
            agent_diagnostics=scan_result.get("agent_diagnostics", []),
            env_diagnostics=scan_result.get("env_diagnostics", []),
            message=message,
        )

    except Exception as e:
        logger.error(f"[Custom Modules] Scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"掃描失敗: {str(e)}")


@router.post("/clean", response_model=CleanResponse)
async def clean_custom_modules(request: ScanRequest):
    """
    清理自定義模組的JSON配置

    刪除所有標記為 is_custom=true 的JSON配置檔案。

    Args:
        request: 清理請求，包含：
            - workspace_path: 工作區路徑（可選）

    Returns:
        CleanResponse: 清理結果，包含：
            - success: 是否成功
            - removed_count: 刪除的配置數量
            - message: 結果訊息

    Raises:
        HTTPException: 400 - 未提供工作區路徑
        HTTPException: 500 - 清理失敗
    """
    workspace_path = request.workspace_path or os.getenv("WORKSPACE_PATH")
    if not workspace_path:
        raise HTTPException(
            status_code=400,
            detail="Workspace path not provided. Set WORKSPACE_PATH env var or pass in request.",
        )

    try:
        generator = CustomModuleJsonGenerator(workspace_path)
        count = generator.remove_custom_modules()

        return CleanResponse(
            success=True,
            removed_count=count,
            message=f"已清理 {count} 個自定義模組配置",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理失敗: {str(e)}")


@router.post("/test", response_model=TestResponse)
async def test_custom_modules(request: TestRequest):
    """
    測試自定義模組

    掃描並測試自定義模組，驗證其能否正常工作。可以測試所有模組或指定特定模組。

    Args:
        request: 測試請求，包含：
            - workspace_path: 工作區路徑（可選）
            - module_kind: 模組型別 ('agent' 或 'env_module'，可選）
            - module_class_name: 要測試的類名（與module_kind配合使用，可選）

    Returns:
        TestResponse: 測試結果，包含：
            - success: 是否全部透過
            - test_output: 測試輸出內容
            - error: 錯誤資訊（如有）
            - returncode: 測試程序返回碼
            - results: 各模組測試結果列表
            - total_tests: 總測試數
            - passed_tests: 透過數
            - failed_tests: 失敗數

    Raises:
        HTTPException: 400 - 未提供工作區路徑
        HTTPException: 500 - 測試失敗

    Note:
        如果不指定 module_kind 和 module_class_name，則測試所有發現的模組。
    """
    workspace_path = request.workspace_path or os.getenv("WORKSPACE_PATH")
    if not workspace_path:
        raise HTTPException(
            status_code=400,
            detail="Workspace path not provided. Set WORKSPACE_PATH env var or pass in request.",
        )

    module_kind = request.module_kind
    module_class_name = request.module_class_name

    try:
        # 記錄測試請求
        if module_kind and module_class_name:
            logger.info(f"[Custom Modules] Testing specific module: {module_kind}.{module_class_name}")
        else:
            logger.info(f"[Custom Modules] Starting test of workspace: {workspace_path}")

        builder = ScriptGenerator(workspace_path)

        if module_kind and module_class_name:
            scanner = CustomModuleScanner(workspace_path)
            scan_result = scanner.scan_all()
            target_modules = (
                scan_result.get("agents", [])
                if module_kind == "agent"
                else scan_result.get("envs", [])
            )
            module_info = next(
                (
                    item
                    for item in target_modules
                    if item.get("class_name") == module_class_name
                ),
                None,
            )
            if module_info is None:
                diagnostics = (
                    scan_result.get("agent_diagnostics", [])
                    if module_kind == "agent"
                    else scan_result.get("env_diagnostics", [])
                )
                module_info = next(
                    (
                        item
                        for item in diagnostics
                        if item.get("class_name") == module_class_name
                    ),
                    None,
                )
            if module_info is None:
                logger.warning(
                    f"[Custom Modules] Module not found: {module_kind}.{module_class_name}"
                )
                return TestResponse(
                    success=False,
                    test_output="",
                    error=f"未找到指定的模組: {module_class_name}",
                    results=[],
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                )
            result = await builder.run_target_test(
                module_kind=module_kind,
                module_path=module_info.get("module_path", ""),
                class_name=module_class_name,
            )
        else:
            scanner = CustomModuleScanner(workspace_path)
            scan_result = scanner.scan_all()

            agents = scan_result.get("agents", [])
            envs = scan_result.get("envs", [])

            logger.info(
                f"[Custom Modules] Test scan found: {len(agents)} agents, {len(envs)} envs"
            )

            if not agents and not envs:
                logger.warning("[Custom Modules] No custom modules found for testing")
                return TestResponse(
                    success=False,
                    test_output="",
                    error="未發現任何自定義模組，請先在 custom/ 目錄下建立模組",
                    results=[],
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                )

            result = await builder.run_test(scan_result)

        # 記錄每個模組的測試結果
        for module_result in result.get("results", []):
            status = "PASSED" if module_result["success"] else "FAILED"
            logger.info(f"[Custom Modules] Test {status}: {module_result['name']}")
            if module_result.get("error"):
                logger.error(f"[Custom Modules] Test error for {module_result['name']}: {module_result['error']}")

        output = result.get("stdout", "")
        stderr = result.get("stderr", "")
        if stderr:
            output = output + "\n--- 錯誤輸出 ---\n" + stderr if output else stderr

        # 記錄總體測試結果
        total = result.get("total_tests", 0)
        passed = result.get("passed_tests", 0)
        failed = result.get("failed_tests", 0)
        logger.info(f"[Custom Modules] Test complete: {passed}/{total} passed, {failed} failed")

        return TestResponse(
            success=result["success"],
            test_output=output,
            error=result.get("error"),
            returncode=result.get("returncode"),
            results=[ModuleTestResult(**r) for r in result.get("results", [])],
            total_tests=result.get("total_tests"),
            passed_tests=result.get("passed_tests"),
            failed_tests=result.get("failed_tests"),
        )

    except Exception as e:
        logger.error(f"[Custom Modules] Test failed: {e}")
        raise HTTPException(status_code=500, detail=f"測試失敗: {str(e)}")


@router.get("/list", response_model=ListResponse)
async def list_custom_modules():
    """
    列出當前已註冊的自定義模組

    從記憶體登錄檔中讀取所有標記為 is_custom=true 的模組資訊。

    Returns:
        ListResponse: 模組列表，包含：
            - success: 是否成功
            - agents: 自定義Agent列表
            - envs: 自定義環境模組列表
            - total_agents: Agent總數
            - total_envs: 環境模組總數

    Raises:
        HTTPException: 500 - 獲取列表失敗
    """
    try:
        registry = get_registry()
        workspace_path = os.getenv("WORKSPACE_PATH")
        if workspace_path and not registry._custom_loaded:
            try:
                scan_and_register_custom_modules(Path(workspace_path), registry)
            except Exception as exc:
                logger.warning(f"[Custom Modules] Auto-load before list failed: {exc}")

        result = {"agents": [], "envs": []}

        # 從登錄檔獲取自定義 Agent
        for agent_type, agent_class in get_registered_agent_modules():
            if getattr(agent_class, "_is_custom", False):
                try:
                    description = agent_class.mcp_description()
                except Exception:
                    description = f"{agent_class.__name__}: {agent_class.__doc__ or 'No description available'}"

                result["agents"].append({
                    "type": agent_type,
                    "class_name": agent_class.__name__,
                    "description": description,
                    "is_custom": True,
                })

        # 從登錄檔獲取自定義環境模組
        for module_type, env_class in get_registered_env_modules():
            if getattr(env_class, "_is_custom", False):
                try:
                    description = env_class.mcp_description()
                except Exception:
                    description = f"{env_class.__name__}: {env_class.__doc__ or 'No description available'}"

                result["envs"].append({
                    "type": module_type,
                    "class_name": env_class.__name__,
                    "description": description,
                    "is_custom": True,
                })

        return ListResponse(
            success=True,
            agents=result["agents"],
            envs=result["envs"],
            total_agents=len(result["agents"]),
            total_envs=len(result["envs"]),
        )
    except Exception as e:
        logger.error(f"[Custom Modules] List failed: {e}")
        raise HTTPException(status_code=500, detail=f"列表獲取失敗: {str(e)}")


@router.get("/status")
async def get_custom_modules_status():
    """
    獲取自定義模組狀態概覽

    返回工作區自定義模組目錄的狀態資訊。

    Returns:
        Dict[str, Any]: 狀態資訊，包含：
            - custom_dir_exists: custom目錄是否存在
            - agents_dir_exists: agents子目錄是否存在
            - envs_dir_exists: envs子目錄是否存在
            - agent_files_count: Agent檔案數量
            - env_files_count: 環境模組檔案數量
            - registered_agents: 已註冊的Agent數量
            - registered_envs: 已註冊的環境模組數量

    Raises:
        HTTPException: 400 - 未設定工作區路徑
    """
    workspace_path = os.getenv("WORKSPACE_PATH")
    if not workspace_path:
        raise HTTPException(status_code=400, detail="Workspace path not set")

    from pathlib import Path

    custom_dir = Path(workspace_path) / "custom"
    status = {
        "custom_dir_exists": custom_dir.exists(),
        "agents_dir_exists": (custom_dir / "agents").exists(),
        "envs_dir_exists": (custom_dir / "envs").exists(),
        "agent_files_count": 0,
        "env_files_count": 0,
        "registered_agents": 0,
        "registered_envs": 0,
    }

    # 統計自定義程式碼檔案
    if status["agents_dir_exists"]:
        status["agent_files_count"] = len(
            [
                f
                for f in (custom_dir / "agents").rglob("*.py")
                if not f.name.startswith("__") and "examples" not in f.parts
            ]
        )

    if status["envs_dir_exists"]:
        status["env_files_count"] = len(
            [
                f
                for f in (custom_dir / "envs").rglob("*.py")
                if not f.name.startswith("__") and "examples" not in f.parts
            ]
        )

    # 統計已註冊的模組（從記憶體登錄檔中讀取）
    try:
        for agent_type, agent_class in get_registered_agent_modules():
            if getattr(agent_class, "_is_custom", False):
                status["registered_agents"] += 1

        for module_type, env_class in get_registered_env_modules():
            if getattr(env_class, "_is_custom", False):
                status["registered_envs"] += 1
    except Exception as e:
        logger.warning(f"[Custom Modules] Failed to count registered modules: {e}")

    return status


@router.get("/classes")
async def list_available_classes(
    workspace_path: str = Query(..., description="工作區路徑"),
    include_custom: bool = Query(True, description="是否包含自定義模組"),
) -> Dict[str, Any]:
    """
    列出所有可用的Agent類和環境模組類

    返回所有可用的類，並標記哪些已配置預填充引數。

    Args:
        workspace_path: 工作區路徑（必填）
        include_custom: 是否包含自定義模組，預設True

    Returns:
        Dict[str, Any]: 可用類列表，包含：
            - success: 是否成功
            - env_modules: 環境模組字典，每個模組包含：
                - type, class_name, description, is_custom, has_prefill
            - agents: Agent字典，每個Agent包含：
                - type, class_name, description, is_custom, has_prefill
            - env_module_count: 環境模組數量
            - agent_count: Agent數量

    Raises:
        HTTPException: 500 - 獲取類列表失敗
    """
    try:
        registry = get_registry()

        # 掃描自定義模組（如果請求）
        if include_custom:
            try:
                scan_and_register_custom_modules(Path(workspace_path), registry)
            except Exception as e:
                logger.warning(f"Failed to scan custom modules: {e}")

        # 獲取所有已註冊的Agent類
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

        # 獲取所有已註冊的Env Module類
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

        # 載入預填充引數，標記哪些類已配置
        prefill_file = Path(workspace_path) / ".agentsociety" / "prefill_params.json"
        env_prefill = {}
        agent_prefill = {}

        if prefill_file.exists():
            try:
                with open(prefill_file, "r", encoding="utf-8") as f:
                    prefill_params = json.load(f)
                    env_prefill = prefill_params.get("env_modules", {})
                    agent_prefill = prefill_params.get("agents", {})
            except Exception as e:
                logger.warning(f"Failed to load prefill params: {e}")

        # 為每個類新增是否已配置的標記
        for module_type in env_modules:
            env_modules[module_type]["has_prefill"] = (
                module_type in env_prefill and bool(env_prefill[module_type])
            )

        for agent_type in agents:
            agents[agent_type]["has_prefill"] = agent_type in agent_prefill and bool(
                agent_prefill[agent_type]
            )

        return {
            "success": True,
            "env_modules": env_modules,
            "agents": agents,
            "env_module_count": len(env_modules),
            "agent_count": len(agents),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list available classes: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to list available classes: {str(e)}"
        )


@router.post("/rescan")
async def rescan_custom_modules(
    workspace_path: str = Query(..., description="工作區路徑"),
) -> Dict[str, Any]:
    """
    重新掃描自定義模組

    清除記憶體中的舊模組並重新掃描工作區的自定義模組。

    Args:
        workspace_path: 工作區路徑（必填）

    Returns:
        Dict[str, Any]: 掃描結果，包含：
            - success: 是否成功
            - scan_result: 掃描詳情
            - message: 結果訊息

    Raises:
        HTTPException: 500 - 重新掃描失敗
    """
    try:
        registry = get_registry()

        # 清除舊的自定義模組
        registry.clear_custom_modules()

        # 掃描新的自定義模組
        scan_result = scan_and_register_custom_modules(Path(workspace_path), registry)

        return {
            "success": True,
            "scan_result": scan_result,
            "message": f"Scanned {len(scan_result.get('envs', []))} env modules and "
            f"{len(scan_result.get('agents', []))} agents",
        }
    except Exception as e:
        logger.error(f"Failed to rescan custom modules: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to rescan custom modules: {str(e)}"
        )
