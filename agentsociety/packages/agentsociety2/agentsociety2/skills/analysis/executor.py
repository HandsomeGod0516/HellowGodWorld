"""执行器：`AnalysisRunner`（主路径工具+代码）、`CodeExecutor`、`ToolRegistry` 与内置工具。"""

import asyncio
import fnmatch
import json
import os
import platform
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from litellm import AllMessageValues
from pydantic import BaseModel

from agentsociety2.logger import get_logger
from agentsociety2.code_executor.code_generator import CodeGenerator
from agentsociety2.code_executor.dependency_detector import DependencyDetector
from agentsociety2.code_executor.local_executor import LocalCodeExecutor
from agentsociety2.config import get_llm_router_and_model

from .models import AnalysisConfig
from .llm_contracts import judgment_prompt
from .utils import (
    XmlParseError,
    extract_database_schema,
    format_database_schema_markdown,
    parse_llm_xml_to_model,
)

logger = get_logger()


# ─────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ExecutionResult:
    """代码执行结果"""

    success: bool
    stdout: str = ""
    stderr: str = ""
    artifacts: List[str] = field(default_factory=list)
    generated_code: str = ""
    error: str = ""


@dataclass
class ToolInfo:
    """工具信息"""

    name: str
    description: str
    tool_type: str = "builtin"
    parameters: List[str] = field(default_factory=list)


class ToolResult(BaseModel):
    """工具执行结果"""

    success: bool
    content: str
    error: Optional[str] = None
    data: Any = None


class ExecutionJudgment(BaseModel):
    """执行结果判断"""

    success: bool
    reason: str
    should_retry: bool = False
    retry_instruction: str = ""


class CodeExecutionJudgment(BaseModel):
    """代码执行裁判（与 CodeExecutor 的 ExecutionJudgment 字段一致，供 AnalysisRunner 解析 XML）"""

    success: bool
    reason: str
    should_retry: bool = False
    retry_instruction: str = ""


# ─────────────────────────────────────────────────────────────────────────
# CodeExecutor: 代码执行器
# ─────────────────────────────────────────────────────────────────────────


class CodeExecutor:
    """Python 代码执行器"""

    def __init__(
        self,
        config: AnalysisConfig,
        output_dir: Path,
    ):
        self.config = config
        self.output_dir = Path(output_dir)
        self.logger = logger

        profile = config.llm_profile_coder
        self._router, self._model_name = get_llm_router_and_model(profile)

        self.code_generator = CodeGenerator()
        self.dependency_detector = DependencyDetector()

    async def execute(
        self,
        description: str,
        db_path: Optional[Path] = None,
        extra_files: Optional[List[str]] = None,
        timeout: Optional[int] = None,
    ) -> ExecutionResult:
        """
        执行 Python 代码。

        Args:
            description: 代码描述
            db_path: 数据库路径
            extra_files: 额外文件
            timeout: 超时时间（秒）

        Returns:
            ExecutionResult
        """
        timeout = timeout or self.config.code_execution_timeout
        extra_files = extra_files or []

        # 创建临时工作目录
        work_dir = Path(tempfile.mkdtemp(prefix="analysis_", dir=self.output_dir))
        files_before = {p for p in work_dir.rglob("*") if p.is_file()}

        try:
            # 准备工作目录
            db_filename = self._prepare_work_dir(work_dir, db_path, extra_files)

            # 构建 prompt
            full_description = self._build_prompt(
                description, db_path, db_filename, extra_files
            )

            # 迭代执行
            messages = [{"role": "user", "content": full_description}]
            max_retries = self.config.max_code_gen_retries

            for attempt in range(max_retries):
                result = await self._generate_and_execute(
                    messages, work_dir, db_path, timeout, attempt
                )

                if result.success:
                    # 收集生成的文件
                    artifacts = self._collect_artifacts(work_dir, files_before)
                    result.artifacts = artifacts
                    return result

                if not result.success and attempt < max_retries - 1:
                    # 添加错误反馈
                    messages.append(
                        {
                            "role": "user",
                            "content": self._build_error_feedback(result, attempt),
                        }
                    )

            return result
        finally:
            # 确保临时目录总是被清理
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _generate_and_execute(
        self,
        messages: List,
        work_dir: Path,
        db_path: Optional[Path],
        timeout: int,
        attempt: int,
    ) -> ExecutionResult:
        """生成并执行代码"""
        # 生成代码
        response = await self._router.acompletion(
            model=self._model_name,
            messages=messages,
        )
        generated_text = response.choices[0].message.content or ""

        code = self.code_generator._extract_code(generated_text)
        if not code or not code.strip():
            return ExecutionResult(
                success=False,
                error="Empty code generated",
                generated_code=generated_text,
            )

        # 检测依赖
        dependencies = self.dependency_detector.detect(code)

        # 执行代码
        executor = LocalCodeExecutor(work_dir=work_dir)
        exec_result = await executor.execute(
            code, dependencies=dependencies, timeout=timeout
        )

        # 判断结果
        judgment = await self._judge_execution(code, exec_result, work_dir)

        return ExecutionResult(
            success=judgment.success,
            stdout=exec_result.stdout or "",
            stderr=exec_result.stderr or "",
            generated_code=code,
            error="" if judgment.success else judgment.reason,
        )

    async def _judge_execution(
        self,
        code: str,
        exec_result,
        work_dir: Path,
    ) -> ExecutionJudgment:
        """判断执行结果"""
        # 截断输出
        stdout = (exec_result.stdout or "")[:4000]
        stderr = (exec_result.stderr or "")[:2000]
        code_preview = code[:4000]

        prompt = f"""Evaluate the code execution result.

**Return Code**: {exec_result.return_code}
**STDOUT**: {stdout}
**STDERR**: {stderr}

**Code Preview**:
```python
{code_preview}
```

The "[truncated for display]" marker means text was truncated for brevity—the script ran in full.

{judgment_prompt()}"""

        response = await self._router.acompletion(
            model=self._model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""

        return parse_llm_xml_to_model(content, ExecutionJudgment, root_tag="judgment")

    def _prepare_work_dir(
        self,
        work_dir: Path,
        db_path: Optional[Path],
        extra_files: List[str],
    ) -> Optional[str]:
        """准备工作目录"""
        db_filename = None

        if db_path and db_path.exists():
            db_filename = db_path.name
            shutil.copy2(db_path, work_dir / db_filename)

        for f_path in extra_files:
            src = Path(f_path)
            if src.exists() and src.is_file():
                if db_path and str(src) == str(db_path):
                    continue
                shutil.copy2(src, work_dir / src.name)

        return db_filename

    def _build_prompt(
        self,
        description: str,
        db_path: Optional[Path],
        db_filename: Optional[str],
        extra_files: List[str],
    ) -> str:
        """构建代码生成 prompt"""
        schema_info = ""
        if db_path and db_path.exists():
            from .data import DataReader

            reader = DataReader(db_path)
            schema = reader.read_schema()
            schema_info = f"""
## Database Schema

{schema.markdown}

**CRITICAL**:
- Use ONLY tables/columns listed above
- Verify table exists before querying
- Handle empty tables gracefully
"""

        files_info = ""
        if db_filename:
            other_files = [
                Path(f).name for f in extra_files if Path(f).name != db_filename
            ]
            files_info = f"""
## Available Files

- Database: `{db_filename}` (in current working directory)
- Output directory: `{self.output_dir}`
- Other files: {other_files if other_files else 'None'}
"""

        return f"""{description}
{schema_info}
{files_info}
## Important Guidelines

- No argparse/sys.argv - all paths provided in context
- Always read and verify data before processing
- Save charts with `plt.savefig('name.png', dpi=150, bbox_inches='tight')`
- Use try-except for file/database operations
- For large data (>50k rows), use sampling for visualization

## Available Libraries

- pandas, numpy: Data manipulation
- matplotlib, seaborn: Visualization
- scipy.stats: Statistical tests
- networkx: Network analysis
- sklearn: Machine learning
"""

    def _build_error_feedback(self, result: ExecutionResult, attempt: int) -> str:
        """构建错误反馈"""
        return f"""## Execution Failed (Attempt {attempt + 1})

**STDOUT**: {result.stdout[:2000]}
**STDERR**: {result.stderr[:1000]}
**Error**: {result.error}

**Generated Code**:
```python
{result.generated_code[:3000]}
```

Please fix the issues and generate corrected code."""

    def _collect_artifacts(self, work_dir: Path, files_before: set) -> List[str]:
        """收集生成的文件"""
        artifact_extensions = {
            ".png",
            ".jpg",
            ".jpeg",
            ".svg",
            ".pdf",
            ".webp",
            ".csv",
            ".json",
            ".txt",
        }
        files_after = {p for p in work_dir.rglob("*") if p.is_file()}
        new_files = files_after - files_before

        artifacts = []
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for idx, p in enumerate(new_files):
            if p.suffix.lower() not in artifact_extensions:
                continue
            dest = self.output_dir / p.name
            if dest.exists() and dest.resolve() != p.resolve():
                dest = self.output_dir / f"{p.stem}_{idx}{p.suffix}"
            if not dest.exists() or dest.resolve() != p.resolve():
                shutil.copy2(p, dest)
            artifacts.append(str(dest))

        # 收集 output_dir 中已有的文件
        for p in self.output_dir.glob("**/*"):
            if p.is_file() and p.suffix.lower() in artifact_extensions:
                if str(p) not in artifacts:
                    artifacts.append(str(p))

        return artifacts


# ─────────────────────────────────────────────────────────────────────────
# ToolRegistry: 工具注册表
# ─────────────────────────────────────────────────────────────────────────


class ToolRegistry:
    """工具注册表"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)
        self._tools: Dict[str, ToolInfo] = {}
        self._tool_classes: Dict[str, Type] = {}
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        """注册内置工具"""
        builtin_tools = {
            "list_directory": (ListDirectoryTool, "List directory contents"),
            "read_file": (ReadFileTool, "Read file contents"),
            "write_file": (WriteFileTool, "Write content to files"),
            "glob": (GlobTool, "Find files matching glob patterns"),
            "search_file_content": (
                SearchFileContentTool,
                "Search for content in files",
            ),
            "replace": (ReplaceTool, "Replace text in files"),
            "run_shell_command": (RunShellCommandTool, "Execute shell commands"),
            "write_todos": (WriteTodoTool, "Manage todo lists"),
            "literature_search": (LiteratureSearchTool, "Search literature"),
            "load_literature": (LoadLiteratureTool, "Load literature entries"),
        }

        for name, (tool_class, description) in builtin_tools.items():
            self._tools[name] = ToolInfo(
                name=name,
                description=description,
                tool_type="builtin",
            )
            self._tool_classes[name] = tool_class

    def list_tools(self) -> Dict[str, ToolInfo]:
        """列出所有工具"""
        return self._tools.copy()

    async def execute_tool(
        self,
        name: str,
        parameters: Dict[str, Any],
    ) -> ToolResult:
        """执行工具。

        Args:
            name: 工具名称。
            parameters: 工具参数字典。

        Returns:
            ToolResult 对象，包含 success、content、error 等字段。
        """
        if name not in self._tool_classes:
            return ToolResult(
                success=False,
                content=f"Tool not found: {name}",
                error="tool_not_found",
            )

        tool_class = self._tool_classes[name]
        tool = tool_class(workspace_path=self.workspace_path)
        return await tool.execute(parameters)


# ─────────────────────────────────────────────────────────────────────────
# 内置工具实现
# ─────────────────────────────────────────────────────────────────────────


class GlobTool:
    """查找匹配 glob 模式的文件"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        pattern = arguments.get("pattern", "")
        path_arg = arguments.get("path", ".")

        search_dir = (self.workspace_path / path_arg).resolve()
        try:
            search_dir.relative_to(self.workspace_path)
        except ValueError:
            return ToolResult(
                success=False,
                content="Path outside workspace",
                error="path_outside_workspace",
            )

        if not search_dir.exists():
            return ToolResult(
                success=False,
                content=f"Path not found: {search_dir}",
                error="path_not_found",
            )

        matches = []
        for item in sorted(search_dir.rglob(pattern)):
            if item.is_file():
                try:
                    matches.append(str(item.relative_to(self.workspace_path)))
                except ValueError:
                    continue

        return ToolResult(
            success=True,
            content=f"Found {len(matches)} files matching '{pattern}'",
            data={"matches": matches, "count": len(matches)},
        )


class ListDirectoryTool:
    """列出目录内容"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        rel_path = arguments.get("path", ".").strip()
        ignore_patterns = arguments.get("ignore", [])

        target_dir = (self.workspace_path / rel_path).resolve()
        try:
            target_dir.relative_to(self.workspace_path)
        except ValueError:
            return ToolResult(
                success=False,
                content="Path outside workspace",
                error="path_outside_workspace",
            )

        if not target_dir.exists() or not target_dir.is_dir():
            return ToolResult(
                success=False,
                content=f"Not a directory: {rel_path}",
                error="not_a_directory",
            )

        entries = []
        for entry in target_dir.iterdir():
            should_ignore = any(fnmatch.fnmatch(entry.name, p) for p in ignore_patterns)
            if not should_ignore:
                entries.append(
                    {
                        "name": entry.name,
                        "type": "directory" if entry.is_dir() else "file",
                    }
                )

        entries.sort(key=lambda x: (x["type"] != "directory", x["name"]))
        return ToolResult(
            success=True,
            content=f"Listed {len(entries)} entries in {rel_path}",
            data={"entries": entries, "path": rel_path},
        )


class ReadFileTool:
    """读取文件内容"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        file_path = arguments.get("path", "").strip()
        limit = arguments.get("limit")

        target_file = (self.workspace_path / file_path).resolve()
        try:
            target_file.relative_to(self.workspace_path)
        except ValueError:
            return ToolResult(
                success=False,
                content="Path outside workspace",
                error="path_outside_workspace",
            )

        if not target_file.exists() or not target_file.is_file():
            return ToolResult(
                success=False,
                content=f"File not found: {file_path}",
                error="file_not_found",
            )

        content = target_file.read_text(encoding="utf-8")
        if limit and len(content) > limit:
            content = content[:limit] + "\n... (truncated)"

        return ToolResult(
            success=True,
            content=f"Read {len(content)} characters from {file_path}",
            data={"path": file_path, "content": content},
        )


class WriteFileTool:
    """写入文件内容"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        file_path = arguments.get("path", "").strip()
        content = arguments.get("content", "")
        create_directories = arguments.get("create_directories", False)

        target_file = (self.workspace_path / file_path).resolve()
        try:
            target_file.relative_to(self.workspace_path)
        except ValueError:
            return ToolResult(
                success=False,
                content="Path outside workspace",
                error="path_outside_workspace",
            )

        if create_directories:
            target_file.parent.mkdir(parents=True, exist_ok=True)

        target_file.write_text(content, encoding="utf-8")
        return ToolResult(
            success=True,
            content=f"Wrote {len(content)} characters to {file_path}",
            data={"path": file_path},
        )


class SearchFileContentTool:
    """搜索文件内容"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        pattern = arguments.get("pattern", "")
        path_arg = arguments.get("path", ".")
        case_sensitive = arguments.get("case_sensitive", False)

        search_dir = (self.workspace_path / path_arg).resolve()
        results = []

        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        for item in search_dir.rglob("*"):
            if not item.is_file():
                continue
            try:
                content = item.read_text(encoding="utf-8", errors="ignore")
                matches = []
                for line_num, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        matches.append({"line": line_num, "text": line})
                if matches:
                    rel_path = item.relative_to(self.workspace_path)
                    results.append({"path": str(rel_path), "matches": matches[:10]})
            except Exception:
                continue

        return ToolResult(
            success=True,
            content=f"Found pattern in {len(results)} files",
            data={"results": results, "count": len(results)},
        )


class ReplaceTool:
    """替换文件中的文本"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        file_path = arguments.get("path", "").strip()
        old_text = arguments.get("old_text", "")
        new_text = arguments.get("new_text", "")

        target_file = (self.workspace_path / file_path).resolve()
        try:
            target_file.relative_to(self.workspace_path)
        except ValueError:
            return ToolResult(
                success=False,
                content="Path outside workspace",
                error="path_outside_workspace",
            )

        if not target_file.exists():
            return ToolResult(
                success=False,
                content=f"File not found: {file_path}",
                error="file_not_found",
            )

        content = target_file.read_text(encoding="utf-8")
        count = content.count(old_text)
        new_content = content.replace(old_text, new_text)
        target_file.write_text(new_content, encoding="utf-8")

        return ToolResult(
            success=True,
            content=f"Replaced {count} occurrence(s) in {file_path}",
            data={"path": file_path, "count": count},
        )


class RunShellCommandTool:
    """在工作区内执行 shell 命令"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        command = (arguments.get("command") or "").strip()
        directory = arguments.get("directory")

        if not command:
            return ToolResult(
                success=False, content="Command is required", error="missing_command"
            )

        exec_dir = self.workspace_path
        if directory:
            exec_dir = (self.workspace_path / directory).resolve()
        try:
            exec_dir.relative_to(self.workspace_path)
        except ValueError:
            return ToolResult(
                success=False,
                content="Directory is outside workspace",
                error="directory_outside_workspace",
            )

        if not exec_dir.exists():
            return ToolResult(
                success=False,
                content=f"Directory not found: {exec_dir}",
                error="directory_not_found",
            )

        if platform.system() == "Windows":
            shell_executable = os.environ.get("ComSpec", "powershell.exe")
            shell_args = (
                ["-NoProfile", "-Command"]
                if shell_executable.endswith("powershell.exe")
                else ["/c"]
            )
        else:
            shell_executable = "/bin/bash"
            shell_args = ["-c"]

        process = await asyncio.create_subprocess_exec(
            shell_executable,
            *shell_args,
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(exec_dir),
        )
        stdout_data, stderr_data = await process.communicate()
        stdout = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
        stderr = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        exit_code = process.returncode or 0

        content_parts = [f"Command: {command}", f"Exit Code: {exit_code}"]
        if stdout:
            content_parts.append(f"\nStdout:\n{stdout}")
        if stderr:
            content_parts.append(f"\nStderr:\n{stderr}")

        return ToolResult(
            success=exit_code == 0,
            content="\n".join(content_parts),
            data={
                "command": command,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
            },
        )


class WriteTodoTool:
    """待办列表（供 ReAct 规划）"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        todos_data = arguments.get("todos", [])
        if not isinstance(todos_data, list):
            return ToolResult(
                success=False,
                content="Invalid argument: 'todos' must be an array",
                error="InvalidArgument",
            )

        in_progress_count = sum(
            1 for t in todos_data if t.get("status") == "in_progress"
        )
        if in_progress_count > 1:
            return ToolResult(
                success=False,
                content="Only one task can be marked as 'in_progress' at a time",
                error="MultipleInProgress",
            )

        if not todos_data:
            content_text = "Todo list cleared."
        else:
            status_icons = {
                "pending": "⏳",
                "in_progress": "🔄",
                "completed": "✅",
                "cancelled": "❌",
            }
            content_text = f"Todo list updated with {len(todos_data)} items.\n\n"
            for todo in todos_data:
                icon = status_icons.get(todo.get("status", "pending"), "•")
                content_text += (
                    f"{icon} {todo.get('description', '')} ({todo.get('status')})\n"
                )

        return ToolResult(
            success=True,
            content=content_text,
            data={"todos": todos_data, "count": len(todos_data)},
        )


class LoadLiteratureTool:
    """加载文献索引"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        path = arguments.get("path", "papers/literature_index.json")
        target_file = (self.workspace_path / path).resolve()

        if not target_file.exists():
            return ToolResult(
                success=False,
                content=f"Literature file not found: {path}",
                error="file_not_found",
            )

        data = json.loads(target_file.read_text(encoding="utf-8"))
        entries = data.get("entries", [])

        return ToolResult(
            success=True,
            content=f"Loaded {len(entries)} literature entries",
            data={"entries": entries, "count": len(entries)},
        )


class LiteratureSearchTool:
    """文献检索"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        from agentsociety2.skills.literature import search_literature_and_save
        from agentsociety2.config import get_llm_router

        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)

        result = await search_literature_and_save(
            query=query,
            workspace_path=self.workspace_path,
            router=get_llm_router("default"),
            limit=limit,
        )

        if result.get("success"):
            return ToolResult(
                success=True,
                content=result.get("content", ""),
                data=result,
            )
        return ToolResult(
            success=False,
            content=result.get("content", ""),
            error=result.get("error"),
        )


# ─────────────────────────────────────────────────────────────────────────
# AnalysisRunner: 内置工具 + 代码执行（多轮对话 / ReAct 工具环）
# ─────────────────────────────────────────────────────────────────────────


class AnalysisRunner:
    """为分析流程执行内置工具与生成的 Python 代码。"""

    def __init__(
        self,
        workspace_path: Path,
        output_dir: Path,
        tool_registry: Optional[Any] = None,
        config: Optional[AnalysisConfig] = None,
    ):
        self.workspace_path = Path(workspace_path)
        self.output_dir = Path(output_dir)
        self.logger = logger
        self._config = config
        self._tool_registry = tool_registry
        self._builtin_tools: Dict[str, Dict[str, Any]] = {}

        profile = config.llm_profile_coder if config else "coder"
        self._router, self._model_name = get_llm_router_and_model(profile)
        self._initialize_builtin_tools()

    def _initialize_builtin_tools(self) -> None:
        tool_classes = {
            "list_directory": (ListDirectoryTool, "List directory contents"),
            "read_file": (ReadFileTool, "Read file contents"),
            "write_file": (WriteFileTool, "Write content to files"),
            "glob": (GlobTool, "Find files matching glob patterns"),
            "search_file_content": (
                SearchFileContentTool,
                "Search for content in files",
            ),
            "replace": (ReplaceTool, "Replace text in files"),
            "run_shell_command": (RunShellCommandTool, "Execute shell commands"),
            "write_todos": (WriteTodoTool, "Manage todo lists"),
            "literature_search": (LiteratureSearchTool, "Search literature"),
            "load_literature": (LoadLiteratureTool, "Load literature entries"),
        }

        for tool_name, (tool_class, description) in tool_classes.items():
            self._builtin_tools[tool_name] = {
                "class": tool_class,
                "description": description,
                "type": "builtin",
            }

        self.logger.info("已初始化 %s 个内置分析工具", len(self._builtin_tools))

    def discover_tools(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "description": info.get("description", ""),
                "type": "builtin",
                "usage": f"Use tool name '{name}' in your analysis plan",
            }
            for name, info in self._builtin_tools.items()
        }

    def discover_tools_with_schemas(self) -> Dict[str, Dict[str, Any]]:
        result = {}
        for name, info in self._builtin_tools.items():
            result[name] = {
                "description": info.get("description", ""),
                "type": "builtin",
                "usage": f"Use tool name '{name}' in your analysis plan",
                "parameters": [],
                "parameters_description": "varies by tool",
            }
        return result

    async def execute_tool(
        self,
        tool_name: str,
        tool_type: str,
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行工具。

        根据工具类型分发到对应的执行器。

        Args:
            tool_name: 工具名称。
            tool_type: 工具类型（builtin 或 code_executor）。
            parameters: 工具参数字典。

        Returns:
            执行结果字典，包含 success、error 等字段。
        """
        if tool_type == "builtin":
            return await self._execute_builtin_tool(tool_name, parameters)
        if tool_type == "code_executor":
            return await self._execute_code(parameters)
        return {"success": False, "error": f"Unknown tool type: {tool_type}"}

    async def _execute_builtin_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        if tool_name not in self._builtin_tools:
            return {"success": False, "error": f"Built-in tool '{tool_name}' not found"}

        tool_info = self._builtin_tools[tool_name]
        tool_class = tool_info["class"]
        tool = tool_class(workspace_path=self.workspace_path)
        result = await tool.execute(parameters)
        return {
            "success": result.success,
            "content": result.content,
            "error": result.error,
            "data": result.data,
        }

    def _discover_database_schema(self, db_path: str) -> Optional[str]:
        db_path_obj = Path(db_path)
        schema = extract_database_schema(db_path_obj)
        if not schema:
            return None
        return format_database_schema_markdown(
            schema, include_row_counts=True, db_path=db_path_obj
        )

    async def _execute_code(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        code_generator = CodeGenerator()
        dependency_detector = DependencyDetector()

        code_description = (
            parameters.get("code_description") or parameters.get("description") or ""
        )
        if not code_description:
            return {"success": False, "error": "No code description provided"}

        db_path = parameters.get("db_path")
        extra_files = parameters.get("extra_files", [])
        default_timeout = self._config.code_execution_timeout if self._config else 600
        timeout = parameters.get("timeout", default_timeout)

        work_dir = Path(tempfile.mkdtemp(prefix="analysis_", dir=self.output_dir))
        local_executor = LocalCodeExecutor(work_dir=work_dir)
        files_before_execution = {p for p in work_dir.rglob("*") if p.is_file()}

        db_filename = Path(db_path).name if db_path else "db.sqlite"

        schema_info = ""
        if db_path:
            discovered_schema = self._discover_database_schema(db_path)
            if discovered_schema:
                schema_info = f"""
## Database Schema

{discovered_schema}

**CRITICAL - DATA VALIDATION REQUIREMENTS**:
- The schema above is the ONLY source of truth. Use ONLY tables and columns listed there.
- Do NOT assume any other table exists. If a table is not in the schema, it does not exist.
- Your code MUST start by reading and validating the database structure.
- Do NOT hardcode table or column names without verification.
- If a table is empty (0 rows), your code should handle this gracefully.

**MANDATORY CODE PATTERN**:
```python
import sqlite3
conn = sqlite3.connect('{db_filename}')
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
actual_tables = {{row[0] for row in cursor.fetchall()}}
print(f"Available tables: {{actual_tables}}")

for table in actual_tables:
    cursor.execute(f"SELECT COUNT(*) FROM {{table}}")
    count = cursor.fetchone()[0]
    print(f"Table {{table}}: {{count}} rows")
```
"""
                self.logger.info("数据库 schema 已包含在代码生成提示中")

        if db_path:
            src_db = Path(db_path)
            if src_db.exists() and src_db.is_file():
                db_filename = src_db.name
                shutil.copy2(src_db, work_dir / db_filename)

        for f_path in extra_files:
            src = Path(f_path)
            if not src.exists() or not src.is_file():
                continue
            if db_path and str(Path(db_path)) == str(src):
                continue
            shutil.copy2(src, work_dir / src.name)

        file_path_info = ""
        if db_path:
            db_filename = db_filename or Path(db_path).name
            other_files_list = [
                f"- {Path(f_path).name}"
                for f_path in extra_files
                if Path(f_path).name != db_filename
            ]
            other_files_str = (
                "\n".join(other_files_list) if other_files_list else "None"
            )
            file_path_info = f"""
## Available Files

- Database: `{db_filename}` (in current working directory)
- Output directory: `{self.output_dir}` (save all output files here)
- Other files: {other_files_str if other_files_str != "None" else "None"}

"""

        full_description = f"""{code_description}
{schema_info}
{file_path_info}
## Important Guidelines

- **No Command-Line Arguments**: Do NOT use argparse, sys.argv, or any command-line argument parsing. All file paths are provided in the context above and files are already in the current working directory.
- **Imports**: If you use sys, add `import sys` at the top. Use standard imports: sqlite3, pandas, etc., as needed.
- **File Reading**: ALWAYS read and examine file contents FIRST before processing. For databases, query the schema programmatically. For other files, read and inspect their structure and content first. Do NOT hardcode assumptions about file structure.
- **Database Schema**: Use ONLY tables from the schema above. ALWAYS verify the database structure before processing. Do NOT hardcode table or column names.
- **Error Handling**: Use try-except blocks for file/database operations. If the core task cannot be completed, exit with `sys.exit(1)` (and ensure `import sys` is present).
- **Type Safety**: SQLite often stores mixed types. Use `pd.to_numeric(..., errors='coerce')` for numeric conversion.
- **JSON Serialization**: When using json.dumps or writing JSON, convert numpy/pandas types (int64, float64) to native Python: use `int(x)` or `float(x)` for scalars, or `df.astype(object)` before to_dict.
- **Output Files**: Save charts with plt.savefig('chart.png') in current directory, or to output directory. PNG/CSV/JSON files will be collected automatically.

## Available Libraries

Use these libraries for analysis and visualization:
- **pandas/numpy**: Data manipulation
- **matplotlib/seaborn**: Static visualizations (preferred for reports)
- **scipy.stats**: Statistical tests (t-test, ANOVA, chi-square, etc.)
- **statsmodels**: Regression and time series analysis
- **networkx**: Network/graph analysis for agent interactions
- **sklearn**: Machine learning (clustering, dimension reduction)

## Visualization Best Practices

- Use seaborn for statistical plots (violin, box, swarm, heatmap)
- Use plt.subplots() for multi-panel figures
- Add proper labels, titles, and legends
- Save as PNG with `plt.savefig('name.png', dpi=150, bbox_inches='tight')`

## Performance & Memory Safety (MANDATORY)

- **Large Dataset Sampling**: If a DataFrame has > 50,000 rows, you MUST use sampling for complex visualizations (scatter plots, pair plots, swarm plots, etc.): `df_sample = df.sample(n=min(10000, len(df)), random_state=42)`. Use the sampled data for plotting only; use full dataset for statistical aggregation.
- **Static Plots for Large Data**: Do NOT attempt to render interactive HTML plots for datasets > 5,000 points. Always use static images (`plt.savefig()`) instead.
- **Memory Efficiency**: For very large tables, prefer SQL aggregation over loading all data into pandas. Use `pd.read_sql_query()` with aggregation queries when possible."""

        max_retries = self._config.max_code_gen_retries if self._config else 5
        conversation_messages: List[AllMessageValues] = []
        generated_code: Optional[str] = None
        exec_result = None

        initial_prompt = code_generator._build_prompt(full_description)
        conversation_messages.append({"role": "user", "content": initial_prompt})

        try:
            for current_try in range(max_retries):
                response = await code_generator._router.acompletion(
                    model=code_generator._model_name,
                    messages=conversation_messages,
                )

                generated_text = response.choices[0].message.content  # type: ignore
                if not generated_text:
                    if current_try < max_retries - 1:
                        conversation_messages.append(
                            {
                                "role": "user",
                                "content": "Code generator returned empty content. Please generate valid Python code.",
                            }
                        )
                    continue

                generated_code = code_generator._extract_code(generated_text)
                if not generated_code or not generated_code.strip():
                    if current_try < max_retries - 1:
                        conversation_messages.append(
                            {
                                "role": "user",
                                "content": "Code generator returned empty code. Please generate valid Python code.",
                            }
                        )
                    continue

                conversation_messages.append(
                    {"role": "assistant", "content": generated_code}
                )

                detected_dependencies = dependency_detector.detect(generated_code)
                exec_result = await local_executor.execute(
                    generated_code,
                    dependencies=detected_dependencies,
                    timeout=timeout,
                )

                judgment = await self._judge_execution_result(
                    generated_code, exec_result, work_dir, files_before_execution
                )

                if judgment.success:
                    break

                if judgment.should_retry and current_try < max_retries - 1:
                    self.logger.info("代码执行失败，将重试。原因: %s", judgment.reason)
                    execution_output = f"""## Code Execution Result (Attempt {current_try + 1})

**Return Code**: {exec_result.return_code if exec_result else "N/A"}

**STDOUT**:
```
{exec_result.stdout if exec_result else "No output"}
```

**STDERR**:
```
{exec_result.stderr if exec_result else "No error"}
```

**Generated Code**:
```python
{generated_code}
```

**Analysis**: {judgment.reason}

**What to fix**: {judgment.retry_instruction}

Please generate corrected code that addresses the issues above."""
                    conversation_messages.append(
                        {"role": "user", "content": execution_output}
                    )
                else:
                    break

            if not exec_result:
                self.logger.warning("所有尝试后仍无执行结果")
                return {
                    "success": False,
                    "error": "No execution result after all attempts",
                    "code_generated": generated_code or "",
                }

            final_judgment = await self._judge_execution_result(
                generated_code, exec_result, work_dir, files_before_execution
            )

            if not final_judgment.success:
                self.logger.warning(
                    "All %s attempts failed. Reason: %s",
                    max_retries,
                    final_judgment.reason,
                )
                return {
                    "success": False,
                    "error": f"Failed after {max_retries} attempts. {final_judgment.reason}",
                    "code_generated": generated_code or "",
                }

            artifact_extensions = {
                ".png",
                ".jpg",
                ".jpeg",
                ".svg",
                ".pdf",
                ".webp",
                ".csv",
                ".json",
                ".txt",
            }
            files_after_execution = {
                p
                for p in work_dir.rglob("*")
                if p.is_file()
                and p not in files_before_execution
                and p.suffix.lower() in artifact_extensions
            }
            artifacts: List[str] = []
            self.output_dir.mkdir(parents=True, exist_ok=True)
            for idx, p in enumerate(files_after_execution):
                dest = self.output_dir / p.name
                if dest.exists() and dest.resolve() != p.resolve():
                    stem, suf = p.stem, p.suffix
                    dest = self.output_dir / f"{stem}_{idx}{suf}"
                if not dest.exists() or dest.resolve() != p.resolve():
                    shutil.copy2(p, dest)
                artifacts.append(str(dest))

            for p in self.output_dir.glob("**/*"):
                if p.is_file() and p.suffix.lower() in artifact_extensions:
                    if str(p) not in artifacts:
                        artifacts.append(str(p))

            return {
                "success": True,
                "code_generated": generated_code or "",
                "stdout": exec_result.stdout or "",
                "stderr": exec_result.stderr or "",
                "artifacts": artifacts,
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _judge_execution_result(
        self,
        generated_code: str,
        exec_result: Any,
        work_dir: Path,
        files_before_execution: set,
    ) -> CodeExecutionJudgment:
        """判断代码执行结果是否成功。

        检查执行返回码、输出文件、错误信息等，通过 LLM 判断执行是否成功，
        以及是否需要重试。

        Args:
            generated_code: 生成的 Python 代码。
            exec_result: 代码执行结果，包含 return_code、stdout、stderr。
            work_dir: 工作目录，用于检查生成的文件。
            files_before_execution: 执行前已存在的文件集合。

        Returns:
            CodeExecutionJudgment 对象，包含 success、reason、should_retry 等字段。
        """
        artifact_extensions = {
            ".png",
            ".jpg",
            ".jpeg",
            ".svg",
            ".pdf",
            ".webp",
            ".csv",
            ".json",
            ".txt",
        }
        files_after = {
            p
            for p in work_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in artifact_extensions
        }
        new_files = files_after - files_before_execution
        output_dir_files = list(self.output_dir.glob("**/*"))

        def _trunc(s: str, max_len: int = 4000) -> str:
            if not s:
                return s
            t = str(s).strip()
            if len(t) <= max_len:
                return t
            return t[
                :max_len
            ] + "\n[output truncated for display, full length=%d]" % len(t)

        stdout_trunc = _trunc(exec_result.stdout if exec_result else "", 4000)
        stderr_trunc = _trunc(exec_result.stderr if exec_result else "", 2000)
        code_preview_len = 4000
        code_trunc = (generated_code or "")[:code_preview_len]
        code_suffix = (
            "\n[code truncated for display, full length=%d]" % len(generated_code or "")
            if len(generated_code or "") > code_preview_len
            else ""
        )

        execution_summary = f"""## Code Execution Result

**IMPORTANT**: STDOUT, STDERR, and Generated Code below may be truncated for display. The "[truncated for display]" marker means WE cut the text for brevity—the script ran in full and its output was complete. Do NOT treat display truncation as script incompleteness. Judge by: return code, files created, and the visible content.

**Return Code**: {exec_result.return_code if exec_result else "N/A"}

**STDOUT**:
```
{stdout_trunc or "No output"}
```

**STDERR**:
```
{stderr_trunc or "No error"}
```

**New Files Created in Work Directory**: {len(new_files)} files
**Output Directory Files**: {len(output_dir_files)} files

**Generated Code** (preview):
```python
{code_trunc}{code_suffix}
```

You should analyze the execution result and determine:
1. Did the code execute successfully (exit 0)?
2. Did it produce the expected output or files matching the task?
3. Are there any errors in stderr that indicate failure?
4. Is the result reasonable and complete for the described task (not partial or wrong output)?
5. success=true only if the outcome is both correct and complete.

{judgment_prompt()}"""

        messages: List[AllMessageValues] = [
            {"role": "user", "content": execution_summary}
        ]

        response = await self._router.acompletion(
            model=self._model_name,
            messages=messages,
        )

        content = response.choices[0].message.content  # type: ignore
        if not content:
            raise XmlParseError("Empty judgment content", raw_content="")

        return parse_llm_xml_to_model(
            content, CodeExecutionJudgment, root_tag="judgment"
        )
