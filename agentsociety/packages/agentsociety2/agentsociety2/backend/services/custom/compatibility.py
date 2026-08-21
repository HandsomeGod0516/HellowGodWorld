"""Compatibility helpers for custom environment modules."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from agentsociety2.backend.services.custom.models import (
    CompatibilityIssue,
    ScanDiagnostic,
)
from agentsociety2.env.base import EnvBase

ENV_COMPATIBILITY_RULES = [
    "最终自定义环境模块必须落在 custom/envs/*.py。",
    "类定义必须在目标文件内，不能只做 re-export。",
    "环境类必须继承 EnvBase，注册 key 保持 class_name。",
    "至少提供一个合法 @tool 方法。",
    "必须实现 step()，并且默认支持无参实例化 cls()。",
    "mcp_description() 必须可调用且返回非空描述。",
    "若模块需要观察能力，应通过 readonly 的 kind='observe' 工具提供。",
]


def ensure_relative_to_workspace(workspace_path: Path, target_path: Path | str) -> str:
    """Normalize a path to a workspace-relative string when possible."""

    resolved_target = Path(target_path).resolve()
    resolved_workspace = workspace_path.resolve()
    try:
        return str(resolved_target.relative_to(resolved_workspace))
    except ValueError:
        return str(target_path)


def get_registered_tool_names(obj: Any) -> list[str]:
    """Return registered tool names for a class or instance."""

    tools = getattr(obj, "_registered_tools", {}) or {}
    return list(tools.keys())


def is_no_arg_constructible(cls: type[Any]) -> tuple[bool, list[str]]:
    """Check whether a class can be instantiated with cls()."""

    signature = inspect.signature(cls)
    required_params: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.default is inspect.Parameter.empty:
            required_params.append(parameter.name)
    return not required_params, required_params


def overrides_base_method(
    cls: type[Any], base_cls: type[Any], method_name: str
) -> bool:
    """Return True when cls resolves method_name to an override beyond base_cls."""

    cls_method = inspect.getattr_static(cls, method_name, None)
    base_method = inspect.getattr_static(base_cls, method_name, None)
    return cls_method is not None and cls_method is not base_method


def build_env_scan_diagnostic(
    *,
    workspace_path: Path,
    module_path: str,
    file_path: Path,
    cls: type[Any],
) -> ScanDiagnostic:
    """Build structured compatibility diagnostics for an env class."""

    issues: list[CompatibilityIssue] = []
    tool_names = get_registered_tool_names(cls)
    has_step = overrides_base_method(cls, EnvBase, "step")
    is_no_arg, required_params = is_no_arg_constructible(cls)

    if not has_step:
        issues.append(
            CompatibilityIssue(
                code="missing_step",
                check="step_method",
                message=f"{cls.__name__} 缺少 step() 方法",
            )
        )

    if not tool_names:
        issues.append(
            CompatibilityIssue(
                code="missing_tools",
                check="registered_tools",
                message=f"{cls.__name__} 没有注册任何 @tool 方法",
            )
        )

    if not is_no_arg:
        issues.append(
            CompatibilityIssue(
                code="non_default_constructor",
                check="default_constructor",
                message=(
                    f"{cls.__name__} 不能直接通过 cls() 实例化，"
                    f"缺少默认值的参数: {required_params}"
                ),
                details={"required_parameters": required_params},
            )
        )

    try:
        description = cls.mcp_description() if hasattr(cls, "mcp_description") else ""
        if not description:
            issues.append(
                CompatibilityIssue(
                    code="empty_mcp_description",
                    check="mcp_description",
                    message=f"{cls.__name__} 的 mcp_description() 为空",
                )
            )
    except Exception as exc:
        issues.append(
            CompatibilityIssue(
                code="mcp_description_error",
                check="mcp_description",
                message=f"{cls.__name__} 的 mcp_description() 调用失败: {exc}",
            )
        )

    accepted = not any(issue.severity == "error" for issue in issues)
    return ScanDiagnostic(
        module_kind="env_module",
        module_path=module_path,
        file_path=str(file_path),
        class_name=cls.__name__,
        accepted=accepted,
        issues=issues,
        metadata={
            "tool_names": tool_names,
            "tool_count": len(tool_names),
            "has_step": has_step,
            "default_constructible": is_no_arg,
            "type": cls.__name__,
            "class_name": cls.__name__,
            "workspace_module_path": ensure_relative_to_workspace(
                workspace_path, file_path
            ),
        },
    )


def build_import_error_diagnostic(
    *,
    module_kind: str,
    file_path: Path,
    module_path: str,
    error: Exception,
) -> ScanDiagnostic:
    """Build a diagnostic entry for import-time failures."""

    return ScanDiagnostic(
        module_kind=module_kind,  # type: ignore[arg-type]
        module_path=module_path,
        file_path=str(file_path),
        accepted=False,
        issues=[
            CompatibilityIssue(
                code="import_error",
                check="import",
                message=f"{file_path.name} 导入失败: {error}",
            )
        ],
        metadata={},
    )
