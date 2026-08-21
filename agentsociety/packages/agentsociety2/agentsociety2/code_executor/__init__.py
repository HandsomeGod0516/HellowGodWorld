"""
``agentsociety2.code_executor``：程式碼生成與執行。

該包聚合了三類能力：

- **程式碼生成**：透過大模型生成可執行的 Python 指令碼（見 :class:`~agentsociety2.code_executor.code_generator.CodeGenerator`）。
- **依賴推斷**：透過 AST 靜態分析匯入語句推斷第三方依賴（見 :class:`~agentsociety2.code_executor.dependency_detector.DependencyDetector`）。
- **程式碼執行**：本地子程序執行（見 :class:`~agentsociety2.code_executor.local_executor.LocalCodeExecutor`）

對外匯出物件見 ``__all__``。
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
