"""存储模块 - 提供实验数据的存储与回放功能。

本模块包含：

**ReplayWriter** — 回放数据写入器：
- 写入 SQLite 数据库
- 支持动态表注册

**动态表与元数据**：
- ``ColumnDef``: 列定义与语义元数据
- ``TableSchema``: 表结构定义
- ``ReplayDatasetSpec``: 数据集级 replay 元数据

使用示例::

    from agentsociety2.storage import ReplayWriter, ColumnDef, TableSchema

    # 创建写入器
    writer = ReplayWriter("replay.db")

    # 注册动态表
    writer.register_table(TableSchema(
        name="custom_data",
        columns=[ColumnDef(name="key", dtype="TEXT")]
    ))

    # 写入数据
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
