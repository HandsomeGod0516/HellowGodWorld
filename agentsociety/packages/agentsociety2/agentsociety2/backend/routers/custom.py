"""
自定义模块 API 路由

提供扫描、清理、测试自定义模块的 API 端点。

关联文件：
- @extension/src/projectStructureProvider.ts - 前端项目结构视图（调用此API）
- @extension/src/apiClient.ts - API客户端

API端点：
- POST /api/v1/custom/scan - 扫描自定义模块并生成JSON配置
- POST /api/v1/custom/clean - 清理自定义模块配置
- POST /api/v1/custom/test - 测试自定义模块
- GET /api/v1/custom/list - 列出已注册的自定义模块
- GET /api/v1/custom/status - 获取自定义模块状态

内部服务：
- @packages/agentsociety2/agentsociety2/backend/services/custom/scanner.py - 模块扫描
- @packages/agentsociety2/agentsociety2/backend/services/custom/generator.py - JSON生成
- @packages/agentsociety2/agentsociety2/registry/ - 模块注册表
"""

from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os
import json

# agentsociety2 是一个 Python 包，通过 import 使用
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


# ========== 请求/响应模型 ==========


class ScanRequest(BaseModel):
    """扫描请求"""

    workspace_path: Optional[str] = Field(
        None, description="工作区路径，不提供则使用环境变量"
    )


class ScanResponse(BaseModel):
    """扫描响应"""

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
    """清理响应"""

    success: bool
    removed_count: int
    message: str


class TestRequest(BaseModel):
    """测试请求"""

    workspace_path: Optional[str] = Field(
        None, description="工作区路径，不提供则使用环境变量"
    )
    module_kind: Optional[str] = Field(
        None, description="模块类型: 'agent' 或 'env_module'，不提供则测试所有"
    )
    module_class_name: Optional[str] = Field(
        None, description="要测试的类名，与 module_kind 配合使用"
    )


class ModuleTestResult(BaseModel):
    """单个模块测试结果"""

    name: str
    module_kind: str = "env_module"
    success: bool
    output: str
    error: Optional[str] = None
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TestResponse(BaseModel):
    """测试响应"""

    success: bool
    test_output: str
    error: Optional[str] = None
    returncode: Optional[int] = None
    results: List[ModuleTestResult] = Field(default_factory=list)
    total_tests: Optional[int] = None
    passed_tests: Optional[int] = None
    failed_tests: Optional[int] = None


class ListResponse(BaseModel):
    """列表响应"""

    success: bool
    agents: List[Dict[str, Any]]
    envs: List[Dict[str, Any]]
    total_agents: int
    total_envs: int


# ========== API 端点 ==========


@router.post("/scan", response_model=ScanResponse)
async def scan_custom_modules(request: ScanRequest):
    """
    扫描自定义模块并注册到内存

    扫描工作区的 custom/agents/ 和 custom/envs/ 目录（跳过 examples/ 子目录），
    验证发现的模块并将其直接注册到内存中的 registry。

    Args:
        request: 扫描请求，包含：
            - workspace_path: 工作区路径（可选，不提供则使用环境变量）

    Returns:
        ScanResponse: 扫描结果，包含：
            - success: 是否成功
            - agents_found: 发现的Agent数量
            - envs_found: 发现的环境模块数量
            - agents_generated: 成功注册的Agent数量
            - envs_generated: 成功注册的环境模块数量
            - errors: 错误信息列表
            - message: 结果消息

    Raises:
        HTTPException: 400 - 未提供工作区路径
        HTTPException: 500 - 扫描失败

    Note:
        此接口不会生成JSON配置文件，模块仅注册到内存中。
        如需持久化配置，请使用 /api/v1/custom/classes 端点。
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
            message_parts.append(f"发现 {agents_count} 个 Agent")
        if envs_count > 0:
            message_parts.append(f"发现 {envs_count} 个环境模块")

        if not message_parts:
            message = "未发现任何自定义模块"
        else:
            message = "、".join(message_parts) + "，已注册到内存"

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
        raise HTTPException(status_code=500, detail=f"扫描失败: {str(e)}")


@router.post("/clean", response_model=CleanResponse)
async def clean_custom_modules(request: ScanRequest):
    """
    清理自定义模块的JSON配置

    删除所有标记为 is_custom=true 的JSON配置文件。

    Args:
        request: 清理请求，包含：
            - workspace_path: 工作区路径（可选）

    Returns:
        CleanResponse: 清理结果，包含：
            - success: 是否成功
            - removed_count: 删除的配置数量
            - message: 结果消息

    Raises:
        HTTPException: 400 - 未提供工作区路径
        HTTPException: 500 - 清理失败
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
            message=f"已清理 {count} 个自定义模块配置",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


@router.post("/test", response_model=TestResponse)
async def test_custom_modules(request: TestRequest):
    """
    测试自定义模块

    扫描并测试自定义模块，验证其能否正常工作。可以测试所有模块或指定特定模块。

    Args:
        request: 测试请求，包含：
            - workspace_path: 工作区路径（可选）
            - module_kind: 模块类型 ('agent' 或 'env_module'，可选）
            - module_class_name: 要测试的类名（与module_kind配合使用，可选）

    Returns:
        TestResponse: 测试结果，包含：
            - success: 是否全部通过
            - test_output: 测试输出内容
            - error: 错误信息（如有）
            - returncode: 测试进程返回码
            - results: 各模块测试结果列表
            - total_tests: 总测试数
            - passed_tests: 通过数
            - failed_tests: 失败数

    Raises:
        HTTPException: 400 - 未提供工作区路径
        HTTPException: 500 - 测试失败

    Note:
        如果不指定 module_kind 和 module_class_name，则测试所有发现的模块。
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
        # 记录测试请求
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
                    error=f"未找到指定的模块: {module_class_name}",
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
                    error="未发现任何自定义模块，请先在 custom/ 目录下创建模块",
                    results=[],
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                )

            result = await builder.run_test(scan_result)

        # 记录每个模块的测试结果
        for module_result in result.get("results", []):
            status = "PASSED" if module_result["success"] else "FAILED"
            logger.info(f"[Custom Modules] Test {status}: {module_result['name']}")
            if module_result.get("error"):
                logger.error(f"[Custom Modules] Test error for {module_result['name']}: {module_result['error']}")

        output = result.get("stdout", "")
        stderr = result.get("stderr", "")
        if stderr:
            output = output + "\n--- 错误输出 ---\n" + stderr if output else stderr

        # 记录总体测试结果
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
        raise HTTPException(status_code=500, detail=f"测试失败: {str(e)}")


@router.get("/list", response_model=ListResponse)
async def list_custom_modules():
    """
    列出当前已注册的自定义模块

    从内存注册表中读取所有标记为 is_custom=true 的模块信息。

    Returns:
        ListResponse: 模块列表，包含：
            - success: 是否成功
            - agents: 自定义Agent列表
            - envs: 自定义环境模块列表
            - total_agents: Agent总数
            - total_envs: 环境模块总数

    Raises:
        HTTPException: 500 - 获取列表失败
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

        # 从注册表获取自定义 Agent
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

        # 从注册表获取自定义环境模块
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
        raise HTTPException(status_code=500, detail=f"列表获取失败: {str(e)}")


@router.get("/status")
async def get_custom_modules_status():
    """
    获取自定义模块状态概览

    返回工作区自定义模块目录的状态信息。

    Returns:
        Dict[str, Any]: 状态信息，包含：
            - custom_dir_exists: custom目录是否存在
            - agents_dir_exists: agents子目录是否存在
            - envs_dir_exists: envs子目录是否存在
            - agent_files_count: Agent文件数量
            - env_files_count: 环境模块文件数量
            - registered_agents: 已注册的Agent数量
            - registered_envs: 已注册的环境模块数量

    Raises:
        HTTPException: 400 - 未设置工作区路径
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

    # 统计自定义代码文件
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

    # 统计已注册的模块（从内存注册表中读取）
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
    workspace_path: str = Query(..., description="工作区路径"),
    include_custom: bool = Query(True, description="是否包含自定义模块"),
) -> Dict[str, Any]:
    """
    列出所有可用的Agent类和环境模块类

    返回所有可用的类，并标记哪些已配置预填充参数。

    Args:
        workspace_path: 工作区路径（必填）
        include_custom: 是否包含自定义模块，默认True

    Returns:
        Dict[str, Any]: 可用类列表，包含：
            - success: 是否成功
            - env_modules: 环境模块字典，每个模块包含：
                - type, class_name, description, is_custom, has_prefill
            - agents: Agent字典，每个Agent包含：
                - type, class_name, description, is_custom, has_prefill
            - env_module_count: 环境模块数量
            - agent_count: Agent数量

    Raises:
        HTTPException: 500 - 获取类列表失败
    """
    try:
        registry = get_registry()

        # 扫描自定义模块（如果请求）
        if include_custom:
            try:
                scan_and_register_custom_modules(Path(workspace_path), registry)
            except Exception as e:
                logger.warning(f"Failed to scan custom modules: {e}")

        # 获取所有已注册的Agent类
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

        # 获取所有已注册的Env Module类
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

        # 加载预填充参数，标记哪些类已配置
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

        # 为每个类添加是否已配置的标记
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
    workspace_path: str = Query(..., description="工作区路径"),
) -> Dict[str, Any]:
    """
    重新扫描自定义模块

    清除内存中的旧模块并重新扫描工作区的自定义模块。

    Args:
        workspace_path: 工作区路径（必填）

    Returns:
        Dict[str, Any]: 扫描结果，包含：
            - success: 是否成功
            - scan_result: 扫描详情
            - message: 结果消息

    Raises:
        HTTPException: 500 - 重新扫描失败
    """
    try:
        registry = get_registry()

        # 清除旧的自定义模块
        registry.clear_custom_modules()

        # 扫描新的自定义模块
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
