"""
依赖检测器：使用 AST 静态分析 import 语句，推断需要安装的第三方依赖。

该模块不执行导入，不会访问网络；仅解析代码文本并返回“可能需要安装”的 pip 包名列表。
"""

import ast
import sys
from typing import List, Set, Dict

from agentsociety2.logger import get_logger

logger = get_logger()


class DependencyDetector:
    """依赖检测器（AST 静态分析）。

    只关注 ``import x`` / ``from x import y``，并将顶层模块名映射为 pip 包名（可通过
    :data:`~agentsociety2.code_executor.dependency_detector.DependencyDetector.IMPORT_TO_PACKAGE` 扩展）。

    .. note::
       该推断是启发式的：无法覆盖动态导入、条件导入、运行时插件机制等场景。
    """

    # 标准库模块列表
    STANDARD_LIBRARY = {
        *sys.builtin_module_names,
        "os",
        "sys",
        "json",
        "datetime",
        "time",
        "math",
        "random",
        "collections",
        "itertools",
        "functools",
        "operator",
        "pathlib",
        "shutil",
        "subprocess",
        "threading",
        "multiprocessing",
        "concurrent",
        "asyncio",
        "typing",
        "dataclasses",
        "enum",
        "abc",
        "contextlib",
        "copy",
        "hashlib",
        "base64",
        "urllib",
        "http",
        "email",
        "csv",
        "xml",
        "sqlite3",
        "pickle",
        "gzip",
        "zipfile",
        "tarfile",
        "io",
        "tempfile",
        "logging",
        "warnings",
        "traceback",
        "inspect",
        "importlib",
        "pkgutil",
        "unittest",
        "doctest",
        "argparse",
        "getopt",
        "configparser",
        "re",
        "string",
        "textwrap",
        "unicodedata",
        "codecs",
        "locale",
        "__future__",
    }

    # 导入名到安装包名的映射
    IMPORT_TO_PACKAGE: Dict[str, str] = {
        "PIL": "Pillow",
        "cv2": "opencv-python",
        "sklearn": "scikit-learn",
        "yaml": "PyYAML",
        "lxml": "lxml",
        "dateutil": "python-dateutil",
        "json_repair": "json-repair",
        "numpy": "numpy",
        "pandas": "pandas",
        "openpyxl": "openpyxl",
        "xlrd": "xlrd",
        "xlwt": "xlwt",
        "xlutils": "xlutils",
        "pyarrow": "pyarrow",
        "h5py": "h5py",
        "matplotlib": "matplotlib",
        "seaborn": "seaborn",
        "scipy": "scipy",
        "statsmodels": "statsmodels",
        "pyodbc": "pyodbc",
        "requests": "requests",
        "httpx": "httpx",
        "tqdm": "tqdm",
        "json5": "json5",
        "pyyaml": "PyYAML",
        # 网络分析
        "networkx": "networkx",
        "community": "python-louvain",
        # 高级可视化
        "plotly": "plotly",
        "bokeh": "bokeh",
        "altair": "altair",
        # 地理可视化
        "folium": "folium",
        "geopandas": "geopandas",
        # 机器学习
        "xgboost": "xgboost",
        "lightgbm": "lightgbm",
        # 文本分析
        "wordcloud": "wordcloud",
        "textblob": "textblob",
        # 缺失值分析
        "missingno": "missingno",
    }

    def __init__(self):
        """初始化依赖检测器（无状态）。"""
        ...

    def _is_standard_library(self, module_name: str) -> bool:
        """判断模块是否属于标准库。"""
        # 处理相对导入
        if module_name.startswith("."):
            return False

        # 处理 __future__ 等特殊导入
        if module_name == "__future__":
            return True

        # 检查是否在标准库列表中
        root_module = module_name.split(".")[0]
        return root_module in self.STANDARD_LIBRARY

    def _normalize_package_name(self, module_name: str) -> str:
        """将导入名归一化为 pip 安装包名。"""
        # 获取根模块名
        root_module = module_name.split(".")[0]

        # 如果存在映射，使用映射后的名称
        if root_module in self.IMPORT_TO_PACKAGE:
            return self.IMPORT_TO_PACKAGE[root_module]

        return root_module

    def _extract_imports_from_ast(self, code: str) -> Set[str]:
        """从代码 AST 中提取“疑似第三方”的顶层模块名集合。"""
        imports: Set[str] = set()
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_module = alias.name.split(".")[0]
                        if not self._is_standard_library(root_module):
                            imports.add(root_module)
                elif isinstance(node, ast.ImportFrom):
                    # 处理相对导入（node.level > 0）
                    if node.level > 0:
                        # 相对导入通常不需要外部依赖，跳过
                        continue
                    if node.module:
                        root_module = node.module.split(".")[0]
                        if not self._is_standard_library(root_module):
                            imports.add(root_module)
        except SyntaxError as e:
            logger.warning(f"AST解析失败: {e}")
        except Exception as e:
            logger.warning(f"AST解析时出现异常: {e}")

        return imports

    def detect(self, code: str) -> List[str]:
        """从代码中检测依赖包（基于 AST import 分析）。

        :param code: Python 代码字符串。
        :returns: 依赖包名列表（去重并排序，已按映射规则转换为 pip 包名）。
        """
        imports = self._extract_imports_from_ast(code)

        normalized_dependencies: Set[str] = set()
        for module_name in imports:
            normalized_name = self._normalize_package_name(module_name)
            normalized_dependencies.add(normalized_name)

        return sorted(list(normalized_dependencies))
