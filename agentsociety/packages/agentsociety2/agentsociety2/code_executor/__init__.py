"""
``agentsociety2.code_executor``：代码生成与执行。

该包聚合了三类能力：

- **代码生成**：通过大模型生成可执行的 Python 脚本（见 :class:`~agentsociety2.code_executor.code_generator.CodeGenerator`）。
- **依赖推断**：通过 AST 静态分析导入语句推断第三方依赖（见 :class:`~agentsociety2.code_executor.dependency_detector.DependencyDetector`）。
- **代码执行**：本地子进程执行（见 :class:`~agentsociety2.code_executor.local_executor.LocalCodeExecutor`）

对外导出对象见 ``__all__``。
"""

from agentsociety2.code_executor.code_generator import CodeGenerator
from agentsociety2.code_executor.dependency_detector import DependencyDetector
from agentsociety2.code_executor.local_executor import LocalCodeExecutor
from agentsociety2.code_executor.models import ExecutionResult

__all__ = [
    "CodeGenerator",
    "DependencyDetector",
    "LocalCodeExecutor",
    "ExecutionResult",
]
