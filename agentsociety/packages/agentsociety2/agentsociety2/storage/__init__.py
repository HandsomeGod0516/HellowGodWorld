"""儲存模組 - 提供實驗資料的儲存與回放功能。

本模組包含：

**ReplayWriter** — 回放資料寫入器：
- 寫入 SQLite 資料庫
- 支援動態表註冊

**動態表與後設資料**：
- ``ColumnDef``: 列定義與語義後設資料
- ``TableSchema``: 表結構定義
- ``ReplayDatasetSpec``: 資料集級 replay 後設資料

使用示例::

    from agentsociety2.storage import ReplayWriter, ColumnDef, TableSchema

    # 建立寫入器
    writer = ReplayWriter("replay.db")

    # 註冊動態表
    writer.register_table(TableSchema(
        name="custom_data",
        columns=[ColumnDef(name="key", dtype="TEXT")]
    ))

    # 寫入資料
    await writer.write("custom_data", {"key": "value"})
"""

from .replay_writer import ReplayWriter
from .replay_metadata import ReplayDatasetSpec
from .table_schema import ColumnDef, TableSchema

__all__ = [
    "ReplayWriter",
    "ColumnDef",
    "TableSchema",
    "ReplayDatasetSpec",
]
