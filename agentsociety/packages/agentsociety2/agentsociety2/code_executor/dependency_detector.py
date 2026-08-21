"""
依賴檢測器：使用 AST 靜態分析 import 語句，推斷需要安裝的第三方依賴。

該模組不執行匯入，不會訪問網路；僅解析程式碼文字並返回“可能需要安裝”的 pip 包名列表。
"""

import ast
import sys
from typing import List, Set, Dict

from agentsociety2.logger import get_logger

logger = get_logger()


class DependencyDetector:
    """依賴檢測器（AST 靜態分析）。

    只關注 ``import x`` / ``from x import y``，並將頂層模組名對映為 pip 包名（可透過
    :data:`~agentsociety2.code_executor.dependency_detector.DependencyDetector.IMPORT_TO_PACKAGE` 擴充套件）。

    .. note::
       該推斷是啟發式的：無法覆蓋動態匯入、條件匯入、執行時外掛機制等場景。
    """

    # 標準庫模組列表
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

    # 匯入名到安裝包名的對映
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
        # 網路分析
        "networkx": "networkx",
        "community": "python-louvain",
        # 高階視覺化
        "plotly": "plotly",
        "bokeh": "bokeh",
        "altair": "altair",
        # 地理視覺化
        "folium": "folium",
        "geopandas": "geopandas",
        # 機器學習
        "xgboost": "xgboost",
        "lightgbm": "lightgbm",
        # 文字分析
        "wordcloud": "wordcloud",
        "textblob": "textblob",
        # 缺失值分析
        "missingno": "missingno",
    }

    def __init__(self):
        """初始化依賴檢測器（無狀態）。"""
        ...

    def _is_standard_library(self, module_name: str) -> bool:
        """判斷模組是否屬於標準庫。"""
        # 處理相對匯入
        if module_name.startswith("."):
            return False

        # 處理 __future__ 等特殊匯入
        if module_name == "__future__":
            return True

        # 檢查是否在標準庫列表中
        root_module = module_name.split(".")[0]
        return root_module in self.STANDARD_LIBRARY

    def _normalize_package_name(self, module_name: str) -> str:
        """將匯入名歸一化為 pip 安裝包名。"""
        # 獲取根模組名
        root_module = module_name.split(".")[0]

        # 如果存在對映，使用對映後的名稱
        if root_module in self.IMPORT_TO_PACKAGE:
            return self.IMPORT_TO_PACKAGE[root_module]

        return root_module

    def _extract_imports_from_ast(self, code: str) -> Set[str]:
        """從程式碼 AST 中提取“疑似第三方”的頂層模組名集合。"""
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
                    # 處理相對匯入（node.level > 0）
                    if node.level > 0:
                        # 相對匯入通常不需要外部依賴，跳過
                        continue
                    if node.module:
                        root_module = node.module.split(".")[0]
                        if not self._is_standard_library(root_module):
                            imports.add(root_module)
        except SyntaxError as e:
            logger.warning(f"AST解析失敗: {e}")
        except Exception as e:
            logger.warning(f"AST解析時出現異常: {e}")

        return imports

    def detect(self, code: str) -> List[str]:
        """從程式碼中檢測依賴包（基於 AST import 分析）。

        :param code: Python 程式碼字串。
        :returns: 依賴包名列表（去重並排序，已按對映規則轉換為 pip 包名）。
        """
        imports = self._extract_imports_from_ast(code)

        normalized_dependencies: Set[str] = set()
        for module_name in imports:
            normalized_name = self._normalize_package_name(module_name)
            normalized_dependencies.add(normalized_name)

        return sorted(list(normalized_dependencies))
