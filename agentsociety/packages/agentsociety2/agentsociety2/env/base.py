"""环境模块基类。

本模块提供环境模块的基类 :class:`EnvBase` 和工具注册装饰器 :func:`tool`。

环境模块定义智能体可执行的操作和可观察的状态。通过 ``@tool`` 装饰器
注册方法为可调用工具，供 Router 调用执行。

工具类型
========

- **常规工具**: ``@tool(readonly=False)`` — 可修改环境状态
- **只读工具**: ``@tool(readonly=True)`` — 仅查询，不修改状态
- **观察工具**: ``@tool(readonly=True, kind="observe")`` — 每个 step 自动调用
- **统计工具**: ``@tool(readonly=True, kind="statistics")`` — 统计信息

Example::

    from agentsociety2.env import EnvBase, tool

    class MyEnv(EnvBase):
        @tool(readonly=True, kind="observe")
        def get_location(self, agent_id: int) -> str:
            '''获取 Agent 当前位置'''
            return self._locations.get(agent_id, "unknown")

        @tool(readonly=False)
        def move(self, agent_id: int, destination: str) -> str:
            '''移动 Agent 到指定位置'''
            self._locations[agent_id] = destination
            return f"Moved to {destination}"
"""

import asyncio
import functools
import inspect
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    Literal,
    Optional,
    TypeVar,
    overload,
)

from agentsociety2.logger import get_logger

if TYPE_CHECKING:
    from agentsociety2.storage import ReplayWriter

from mcp.server.fastmcp.tools.base import Tool
from mcp.server.fastmcp.tools.tool_manager import ToolManager
from openai.types.chat import ChatCompletionToolParam

__all__ = [
    "EnvBase",
    "PersonStepConstraints",
    "merge_person_step_constraints",
    "tool",
]

_constraints_logger = get_logger()


@dataclass(frozen=True)
class PersonStepConstraints:
    """单仿真步内对 PersonAgent 可见技能与工具白名单的约束。

    环境可实现 :meth:`EnvBase.person_step_constraints` 返回本结构；PersonAgent 合并后执行，不绑定单一实验。
    """

    hide_skills: frozenset[str] = frozenset()
    pin_allowed_tools_to_skill: str | None = None
    forbid_disabling_skills: frozenset[str] = frozenset()


def merge_person_step_constraints(
    env_modules: list[Any],
) -> PersonStepConstraints | None:
    """合并路由器上所有环境模块返回的约束（并集/冲突检测）。"""
    hide: set[str] = set()
    forbid: set[str] = set()
    pin: str | None = None
    for m in env_modules or []:
        fn = getattr(m, "person_step_constraints", None)
        if not callable(fn):
            continue
        c = fn()
        if c is None:
            continue
        hide.update(c.hide_skills)
        forbid.update(c.forbid_disabling_skills)
        if c.pin_allowed_tools_to_skill:
            p = c.pin_allowed_tools_to_skill.strip()
            if not p:
                continue
            if pin is None:
                pin = p
            elif pin != p:
                _constraints_logger.warning(
                    "person_step_constraints: conflicting pin_allowed_tools_to_skill %r vs %r (using first)",
                    pin,
                    p,
                )
    if not hide and not forbid and not pin:
        return None
    return PersonStepConstraints(
        hide_skills=frozenset(hide),
        pin_allowed_tools_to_skill=pin,
        forbid_disabling_skills=frozenset(forbid),
    )


F = TypeVar("F", bound=Callable)


@overload
def tool(
    readonly: Literal[True],
    name: str | None = None,
    description: str | None = None,
    kind: Literal["observe", "statistics"] = ...,
) -> Callable[[F], F]:
    """Overload for observe/statistics tools that must be readonly."""
    ...


@overload
def tool(
    readonly: bool,
    name: str | None = None,
    description: str | None = None,
    kind: None = None,
) -> Callable[[F], F]:
    """Overload for regular tools."""
    ...


def tool(
    readonly: bool,
    name: str | None = None,
    description: str | None = None,
    kind: Literal["observe", "statistics"] | None = None,
) -> Callable[[F], F]:
    """将环境方法注册为可调用工具（供 Router/LLM 调用）。

    :param readonly: 是否只读。
        当 ``kind`` 为 ``observe`` 或 ``statistics`` 时必须为 ``True``（运行时强校验）。
    :param name: 可选。工具名（默认使用函数名）。
    :param description: 可选。工具描述（用于模型函数调用 schema）。
    :param kind: 可选。工具类型：

        - ``observe``：观测工具（通常每步自动调用），除 ``self`` 外最多 1 个参数
        - ``statistics``：统计工具，除 ``self`` 外不能有参数
        - ``None``：普通工具
    :returns: 装饰器函数。
    :raises ValueError: ``kind`` 与 ``readonly``/参数签名不匹配时抛出。
    """

    def tool_decorator(func: F) -> F:
        # Validate readonly constraint for observe/statistics tools
        if kind in ("observe", "statistics") and not readonly:
            raise ValueError(
                f"Tool '{func.__name__}' with kind='{kind}' must have readonly=True. "
                f"Tools of kind 'observe' or 'statistics' are read-only by design."
            )

        # Validate kind parameter if provided
        if kind is not None:
            if kind not in ("observe", "statistics"):
                raise ValueError(
                    f"Invalid kind '{kind}'. Must be 'observe', 'statistics', or None."
                )

            # Get function signature to validate parameters
            sig = inspect.signature(func)
            params = list(sig.parameters.values())

            # Exclude the first parameter (self) for class methods
            # For instance methods, the first parameter is typically 'self'
            non_self_params = params[1:] if len(params) > 0 else []

            if kind == "observe":
                # Observe functions can have at most one parameter besides self
                if len(non_self_params) > 1:
                    param_names = [p.name for p in non_self_params]
                    raise ValueError(
                        f"Tool '{func.__name__}' with kind='observe' can have at most one "
                        f"parameter besides self, but found {len(non_self_params)}: {param_names}. The only one allowed parameter is agent_id, id, or person_id"
                    )
            elif kind == "statistics":
                # Statistics functions can only have self parameter
                if len(non_self_params) > 0:
                    param_names = [p.name for p in non_self_params]
                    raise ValueError(
                        f"Tool '{func.__name__}' with kind='statistics' can only have self "
                        f"parameter, but found additional parameters: {param_names}"
                    )

        # Store the tool information in the function's attribute for Metaclass usage
        tool = Tool.from_function(
            func,
            name=name,
            description=description,
        )
        func._tool_info = {  # type: ignore
            "mcp.Tool": tool,
            "readonly": readonly,
            "kind": kind,
        }

        # Wrap the function to record call history
        original_func = func
        tool_name = name if name else func.__name__

        # Get function signature to convert args to kwargs
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())
        # Skip 'self' parameter for instance methods
        if param_names and param_names[0] == "self":
            param_names = param_names[1:]

        def _normalize_to_kwargs(args, kwargs):
            """
            Convert args and kwargs to unified kwargs dict based on function signature.

            Args:
                args: Positional arguments (excluding self)
                kwargs: Keyword arguments

            Returns:
                Dict of arguments with parameter names as keys
            """
            normalized_kwargs = {}

            # Process positional args first (map to parameter names by position)
            for i, arg in enumerate(args):
                if i < len(param_names):
                    param_name = param_names[i]
                    # Only add if not already in kwargs (kwargs take precedence)
                    if param_name not in kwargs:
                        normalized_kwargs[param_name] = arg

            # Add all kwargs
            normalized_kwargs.update(kwargs)

            return normalized_kwargs

        def _create_call_record(
            args, kwargs, return_value, exception_occurred, exception_info
        ):
            """Helper function to create a call record."""
            # Convert args and kwargs to unified kwargs dict
            normalized_kwargs = _normalize_to_kwargs(args, kwargs)
            kwargs_repr = _serialize_to_literal(normalized_kwargs)
            return_value_repr = (
                _serialize_to_literal(return_value)
                if return_value is not None
                else None
            )

            return {
                "function_name": tool_name,
                "kwargs": kwargs_repr,
                "return_value": return_value_repr,
                "exception_occurred": exception_occurred,
                "exception_info": exception_info,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        if inspect.iscoroutinefunction(func):

            async def async_wrapper(self, *args, **kwargs):
                exception_occurred = False
                exception_info = None
                return_value = None

                try:
                    return_value = await original_func(self, *args, **kwargs)
                except Exception as e:
                    exception_occurred = True
                    exception_info = {
                        "type": type(e).__name__,
                        "message": str(e),
                    }
                    raise
                finally:
                    # Record the call history
                    if hasattr(self, "_tool_call_history"):
                        call_record = _create_call_record(
                            args,
                            kwargs,
                            return_value,
                            exception_occurred,
                            exception_info,
                        )
                        self._tool_call_history.append(call_record)
                return return_value

            wrapped_func = async_wrapper
        else:
            # Sync function wrapper using functools.wraps
            @functools.wraps(original_func)
            def sync_wrapper(self, *args, **kwargs):
                exception_occurred = False
                exception_info = None
                return_value = None

                try:
                    return_value = original_func(self, *args, **kwargs)
                except Exception as e:
                    exception_occurred = True
                    exception_info = {
                        "type": type(e).__name__,
                        "message": str(e),
                    }
                    raise
                finally:
                    # Record the call history
                    if hasattr(self, "_tool_call_history"):
                        call_record = _create_call_record(
                            args,
                            kwargs,
                            return_value,
                            exception_occurred,
                            exception_info,
                        )
                        self._tool_call_history.append(call_record)
                return return_value

            wrapped_func = sync_wrapper

        # Set custom attributes
        wrapped_func._tool_info = func._tool_info  # type: ignore
        wrapped_func._original_func = original_func  # type: ignore

        return wrapped_func  # type: ignore

    return tool_decorator


def _serialize_to_literal(value: Any) -> Any:
    """
    Serialize a value to a JSON-serializable literal representation.

    Args:
        value: The value to serialize

    Returns:
        A JSON-serializable representation of the value
    """
    try:
        # Try to serialize directly
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        # If not directly serializable, convert to string representation
        try:
            return repr(value)
        except Exception:
            return str(type(value).__name__)


class EnvMeta(type):
    """元类：收集 ``@tool`` 装饰的方法并注册到 tool_manager。"""

    def __new__(cls, name, bases, namespace, **kwargs):
        new_class = super().__new__(cls, name, bases, namespace, **kwargs)

        registered_tools: Dict[str, Any] = {}
        readonly_tools: Dict[str, bool] = {}
        tool_kinds: Dict[str, str | None] = {}
        for attr_name, attr_value in namespace.items():
            if callable(attr_value) and hasattr(attr_value, "_tool_info"):
                tool_info = attr_value._tool_info  # type: ignore
                # tool_info is a dict, containing "mcp.Tool", "readonly", and "kind"
                tool_obj = tool_info["mcp.Tool"]
                tool_obj.fn = attr_value  # Bind the actual function
                tool_key = tool_obj.name if tool_obj.name else attr_name
                registered_tools[tool_key] = tool_obj
                readonly_tools[tool_key] = tool_info.get("readonly", False)
                tool_kinds[tool_key] = tool_info.get("kind", None)
        new_class._registered_tools = registered_tools  # type: ignore
        new_class._readonly_tools = readonly_tools  # type: ignore
        new_class._tool_kinds = tool_kinds  # type: ignore
        return new_class


class EnvBase(metaclass=EnvMeta):
    """环境模块基类。

    环境模块定义 Agent 可执行的操作和可观察的状态。通过 ``@tool`` 装饰器
    注册方法为可调用工具，供 Router 调用。

    工具类型：
        - **常规工具**: ``@tool(readonly=False)`` — 可修改环境状态
        - **只读工具**: ``@tool(readonly=True)`` — 仅查询，不修改状态
        - **观察工具**: ``@tool(readonly=True, kind="observe")`` — 自动调用
        - **统计工具**: ``@tool(readonly=True, kind="statistics")`` — 统计信息

    子类应实现：
        - 使用 ``@tool`` 装饰器定义可执行操作
        - 可选：实现 ``observe()`` 方法（默认收集 kind="observe" 的工具）
    """

    # 声明式状态持久化：子类覆盖以自动创建 replay 表
    _agent_state_columns: ClassVar[list] = []
    _env_state_columns: ClassVar[list] = []

    @classmethod
    def _state_table_prefix_from_class(cls) -> str:
        """从类名推导表名前缀（PascalCase -> snake_case，去常见后缀）"""
        name = cls.__name__
        for suffix in ("Space", "Env", "Module"):
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[: -len(suffix)]
                break
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()

    def __init__(self):
        self.t = datetime.now()

        # Initialize tool call history storage
        self._tool_call_history: list[dict[str, Any]] = []

        # Replay writer for storing simulation state
        self._replay_writer: Optional["ReplayWriter"] = None
        self._state_tables_registered: bool = False

        tools = list(getattr(self.__class__, "_registered_tools", {}).values())
        self._tool_manager = ToolManager(tools=tools)
        self._readonly_llm_tools: list[ChatCompletionToolParam] = []
        self._llm_tools: list[ChatCompletionToolParam] = []
        """
        Tool Schema for LLM to call
        """
        for t in self._tool_manager.list_tools():
            parameters = deepcopy(t.parameters)
            # remove self
            if "properties" in parameters and "self" in parameters["properties"]:
                del parameters["properties"]["self"]
            if "required" in parameters and "self" in parameters["required"]:
                parameters["required"].remove("self")
            # convert format
            func: ChatCompletionToolParam = {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": parameters,
                },
            }
            self._llm_tools.append(func)
            readonly_tools = getattr(self.__class__, "_readonly_tools", {})
            if readonly_tools.get(t.name, False):
                self._readonly_llm_tools.append(func)

    def _set_llm_tools(self, tools: list[ChatCompletionToolParam]):
        self._llm_tools = tools

    @property
    def name(self):
        """Name of the environment module"""
        return self.__class__.__name__

    @property
    def description(self) -> str:
        """Description of the environment module for router selection and function calling"""
        return """EnvBase is an abstract class for environment modules. DO NOT USE IT DIRECTLY.
It contains no functions or methods.
"""

    @classmethod
    def mcp_description(cls) -> str:
        """
        Return a description text for MCP environment module candidate list.
        This method should be overridden by subclasses to provide a description
        suitable for MCP server to list available environment modules.

        Returns:
            A string description of the environment module for MCP registration.

        Format:
            The description uses Markdown format with the following structure:
            - Class name and brief description
            - Detailed description section
            - Initialization parameters (if applicable)
            - Example config or JSON schema (if applicable)
        """
        # Check if this is the base class being called directly
        if cls is EnvBase:
            return f"""{cls.__name__}: Abstract base class for environment modules.

**Description:** {cls.__doc__ or 'No description available'}

**Note:** This is an abstract base class. Do not use it directly. Subclasses should override this method to provide specific descriptions, initialization parameters, and usage examples.
"""
        else:
            # For subclasses that don't override this method
            return f"""{cls.__name__}: Environment module.

**Description:** {cls.__doc__ or 'No description available'}

**Note:** This subclass has not provided a detailed description. Please refer to the class documentation or source code for initialization parameters and usage.
"""

    # ---- Skill Discovery ----

    @classmethod
    def get_agent_skills_dirs(cls) -> list[Path]:
        """Return directories containing agent skills provided by this environment module.

        This method enables environment modules to bundle specialized skills that agents
        should use when operating within that environment. Skills are discovered by scanning
        the returned directories for SKILL.md files.

        **Default Discovery Convention:**

        The base implementation automatically discovers skills from:

        1. Package modules (e.g., ``mobility_space/__init__.py``):
           Scans ``<module_dir>/agent_skills/`` directory.

        2. Single-file modules (e.g., ``economy_space.py``):
           Scans ``<module_dir>/<stem>_agent_skills/`` directory.

        **Override for Custom Paths:**

        Subclasses can override this method to specify custom skill directories:

        ```python
        @classmethod
        def get_agent_skills_dirs(cls) -> list[Path]:
            from pathlib import Path
            # Point to a shared skills directory
            skills_dir = Path(__file__).parent.parent / "skills"
            return [skills_dir] if skills_dir.is_dir() else []
        ```

        **Skill Directory Structure:**

        Each skill directory should contain subdirectories with SKILL.md files:

        ```
        agent_skills/
        ├── navigation/
        │   ├── SKILL.md          # Required: skill definition with YAML frontmatter
        │   └── scripts/          # Optional: executable scripts
        └── spatial_reasoning/
            └── SKILL.md
        ```

        Returns:
            List of Path objects pointing to directories containing skill subdirectories.
            Empty list if no skills are provided.
        """
        import inspect

        module_file = Path(inspect.getfile(cls))
        parent = module_file.parent
        dirs: list[Path] = []

        # Single-file module: economy_space.py → economy_space_agent_skills/
        stem_dir = parent / f"{module_file.stem}_agent_skills"
        if stem_dir.is_dir():
            dirs.append(stem_dir)

        # Package module: mobility_space/ → mobility_space/agent_skills/
        pkg_dir = parent / "agent_skills"
        if pkg_dir.is_dir():
            dirs.append(pkg_dir)

        return dirs

    def get_default_skill(self) -> str | None:
        """Return the default skill that should be auto-activated for agents in this environment.

        When PersonAgent initializes within an environment, it automatically activates
        the specified skill. This allows environments to override the default
        observation-needs-cognition-plan decision flow with specialized behavior.

        **Use Cases:**

        - Game/experiment environments requiring specific decision protocols
        - Domain-specific environments with specialized reasoning patterns
        - Tutorial or guided scenarios with constrained agent behavior

        **Example:**

        ```python
        def get_default_skill(self) -> str | None:
            return "public-goods-experiment"  # Auto-activate experiment skill
        ```

        **Note:** The skill must be available (either built-in or discovered via
        :meth:`get_agent_skills_dirs`). If the skill is not found, a warning is logged
        and the agent uses the default skill set.

        Returns:
            Skill name to auto-activate, or None for default behavior.
        """
        return None

    def person_step_constraints(self) -> Optional["PersonStepConstraints"]:
        """可选：返回本步对 PersonAgent 的通用约束（隐藏技能、钉住 allowed-tools 等）。

        默认无约束。需要「专用默认 skill 独占一步」类行为的环境应返回
        :class:`PersonStepConstraints`，
        由 PersonAgent 与具体实验/玩法解耦。

        Returns:
            PersonStepConstraints | None
        """
        return None

    async def init(self, start_datetime: datetime):
        """初始化环境模块。

        :param start_datetime: 仿真起始时间。
        """
        self.t = start_datetime

    async def step(self, tick: int, t: datetime):
        """推进环境模块一个仿真步。

        :param tick: 本步时间跨度（秒）。
        :param t: 本步结束后的仿真时间。
        :raises NotImplementedError: 基类不提供默认实现。
        """
        raise NotImplementedError

    async def close(self):
        """关闭环境模块并释放资源（可选重写）。"""
        ...

    # ---- Dump & Load ----
    def _dump_state(self) -> dict:
        """子类钩子：导出内部状态（如需持久化请重写）。"""
        return {}

    def _load_state(self, state: dict):
        """子类钩子：加载内部状态（与 :meth:`_dump_state` 配对）。"""
        return None

    async def dump(self) -> dict:
        """序列化环境模块状态。

        :returns: 可序列化字典，包含 ``name``、``t`` 与 ``state``。
        """
        return {
            "name": self.name,
            "t": self.t.isoformat(),
            "state": self._dump_state(),
        }

    async def load(self, dump_data: dict):
        """从 :meth:`dump` 的输出恢复环境模块状态。

        :param dump_data: 由 :meth:`dump` 产生的字典。
        """
        try:
            t_str = dump_data.get("t")
            if isinstance(t_str, str) and len(t_str) > 0:
                from datetime import datetime as _dt

                self.t = _dt.fromisoformat(t_str)
        except Exception:
            # keep current t on parse failure
            pass
        state = dump_data.get("state") or {}
        if isinstance(state, dict):
            self._load_state(state)

    def get_tool_call_history(self) -> list[dict[str, Any]]:
        """获取工具调用历史（浅拷贝）。

        :returns: 调用记录列表。每条包含 ``function_name``、``kwargs``、``return_value``、
            ``exception_occurred``、``exception_info``、``timestamp``。
        """
        return self._tool_call_history.copy()

    def reset_tool_call_history(self):
        """清空工具调用历史。"""
        self._tool_call_history.clear()

    # ==================== Replay Data Methods ====================

    def set_replay_writer(self, writer: "ReplayWriter") -> None:
        """设置回放写入器（用于自动建表与写入状态）。

        :param writer: :class:`~agentsociety2.storage.ReplayWriter` 实例。
        """
        self._replay_writer = writer
        if writer is not None and (
            self._agent_state_columns or self._env_state_columns
        ):
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._register_state_tables())
                task.add_done_callback(self._on_register_state_tables_done)
            except RuntimeError:
                # 没有运行中的事件循环，首次写入时惰性注册
                pass

    @staticmethod
    def _on_register_state_tables_done(task: "asyncio.Task[None]") -> None:
        """Callback for _register_state_tables task to log exceptions."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            import logging

            logging.getLogger(__name__).error(
                "Failed to register state tables: %s", exc, exc_info=exc
            )

    @property
    def _state_table_prefix(self) -> str:
        """从类名推导表名前缀（PascalCase → snake_case，去常见后缀）"""
        return type(self)._state_table_prefix_from_class()

    async def _register_state_tables(self) -> None:
        """根据 _agent_state_columns / _env_state_columns 声明自动注册 replay 表"""
        if self._replay_writer is None or self._state_tables_registered:
            return

        from agentsociety2.storage import ColumnDef, ReplayDatasetSpec, TableSchema

        prefix = self._state_table_prefix

        if self._agent_state_columns:
            table_name = f"{prefix}_agent_state"
            columns = [
                ColumnDef(
                    "agent_id",
                    "INTEGER",
                    nullable=False,
                    logical_type="identifier",
                    analysis_role="identifier",
                    description="Agent identifier for this snapshot row.",
                ),
                ColumnDef(
                    "step",
                    "INTEGER",
                    nullable=False,
                    logical_type="step",
                    analysis_role="timestamp",
                    description="Simulation step for this snapshot row.",
                ),
                ColumnDef(
                    "t",
                    "TIMESTAMP",
                    nullable=False,
                    logical_type="timestamp",
                    analysis_role="timestamp",
                    description="Simulation timestamp for this snapshot row.",
                ),
                *self._agent_state_columns,
            ]
            schema = TableSchema(
                name=table_name,
                columns=[
                    *columns,
                ],
                primary_key=["agent_id", "step"],
                indexes=[["step"], ["t"]],
            )
            await self._replay_writer.register_table(schema)
            capabilities = ["agent_snapshot", "timeseries"]
            column_names = {col.name for col in columns}
            if {"lng", "lat"}.issubset(column_names):
                capabilities.extend(["geo_point", "trajectory"])
            if {"tile_x", "tile_y"}.issubset(column_names):
                capabilities.extend(["tile_point", "trajectory"])
            await self._replay_writer.register_dataset(
                ReplayDatasetSpec(
                    dataset_id=f"{prefix}.agent_state",
                    table_name=table_name,
                    module_name=self.name,
                    kind="entity_snapshot",
                    title=f"{self.name} Agent State",
                    description=f"Per-agent replay snapshots exported by {self.name}.",
                    entity_key="agent_id",
                    step_key="step",
                    time_key="t",
                    default_order=["step", "agent_id"],
                    capabilities=capabilities,
                ),
                columns,
            )

        if self._env_state_columns:
            table_name = f"{prefix}_env_state"
            columns = [
                ColumnDef(
                    "step",
                    "INTEGER",
                    nullable=False,
                    logical_type="step",
                    analysis_role="timestamp",
                    description="Simulation step for this environment snapshot row.",
                ),
                ColumnDef(
                    "t",
                    "TIMESTAMP",
                    nullable=False,
                    logical_type="timestamp",
                    analysis_role="timestamp",
                    description="Simulation timestamp for this environment snapshot row.",
                ),
                *self._env_state_columns,
            ]
            schema = TableSchema(
                name=table_name,
                columns=[
                    *columns,
                ],
                primary_key=["step"],
                indexes=[["t"]],
            )
            await self._replay_writer.register_table(schema)
            await self._replay_writer.register_dataset(
                ReplayDatasetSpec(
                    dataset_id=f"{prefix}.env_state",
                    table_name=table_name,
                    module_name=self.name,
                    kind="env_snapshot",
                    title=f"{self.name} Environment State",
                    description=f"Per-step environment snapshots exported by {self.name}.",
                    entity_key=None,
                    step_key="step",
                    time_key="t",
                    default_order=["step"],
                    capabilities=["env_snapshot", "timeseries"],
                ),
                columns,
            )

        self._state_tables_registered = True

    async def _write_agent_state(
        self, agent_id: int, step: int, t: datetime, **data: Any
    ) -> None:
        """写入 per-agent 交互状态到 {prefix}_agent_state 表

        Args:
            agent_id: Agent ID
            step: 当前步数
            t: 当前模拟时间
            **data: 模块自定义字段（需与 _agent_state_columns 声明匹配）
        """
        if self._replay_writer is None:
            return
        if not self._state_tables_registered:
            await self._register_state_tables()
        table_name = f"{self._state_table_prefix}_agent_state"
        await self._replay_writer.write(
            table_name, {"agent_id": agent_id, "step": step, "t": t, **data}
        )

    async def _write_agent_state_batch(
        self, step: int, t: datetime, records: list[dict[str, Any]]
    ) -> None:
        """批量写入 per-agent 交互状态

        Args:
            step: 当前步数
            t: 当前模拟时间
            records: 记录列表，每条需包含 agent_id 和模块自定义字段
        """
        if self._replay_writer is None or not records:
            return
        if not self._state_tables_registered:
            await self._register_state_tables()
        table_name = f"{self._state_table_prefix}_agent_state"
        rows = [{"step": step, "t": t, **rec} for rec in records]
        await self._replay_writer.write_batch(table_name, rows)

    async def _write_env_state(self, step: int, t: datetime, **data: Any) -> None:
        """写入环境全局状态到 {prefix}_env_state 表

        Args:
            step: 当前步数
            t: 当前模拟时间
            **data: 模块自定义字段（需与 _env_state_columns 声明匹配）
        """
        if self._replay_writer is None:
            return
        if not self._state_tables_registered:
            await self._register_state_tables()
        table_name = f"{self._state_table_prefix}_env_state"
        await self._replay_writer.write(table_name, {"step": step, "t": t, **data})
