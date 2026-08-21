"""動態回放表的 schema 定義（供環境模組註冊）。

環境模組（例如 social_media、mobility_space）可透過 :class:`~agentsociety2.storage.table_schema.TableSchema`
與 :class:`~agentsociety2.storage.table_schema.ColumnDef` 宣告自己的回放表結構，並在執行時呼叫
:meth:`agentsociety2.storage.replay_writer.ReplayWriter.register_table` 建立表、呼叫
:meth:`agentsociety2.storage.replay_writer.ReplayWriter.write` 寫入行資料。

除 SQL 列定義外，``ColumnDef`` 還可攜帶語義後設資料（描述、邏輯型別、分析角色等），
由 :class:`~agentsociety2.storage.ReplayWriter` 的 catalog 表單獨持久化。
"""

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

# SQLite column types
ColumnType = Literal["INTEGER", "REAL", "TEXT", "BLOB", "TIMESTAMP", "JSON"]


@dataclass
class ColumnDef:
    """表列定義（SQLite）。

    :param name: 列名。
    :param type: SQLite 列型別（見 :data:`~agentsociety2.storage.table_schema.ColumnType`）。
    :param nullable: 是否允許 NULL。
    :param default: 預設值表示式（例如 ``CURRENT_TIMESTAMP``）。
    :param title: 可選，人類可讀的列標題。
    :param description: 可選，語義描述（供回放/匯出/分析使用）。
    :param logical_type: 可選，邏輯型別（例如 ``geo.lng``、``money``）。
    :param analysis_role: 可選，分析角色（例如 ``measure``）。
    :param unit: 可選，單位字串（供分析/報告使用）。
    :param enum_values: 可選，離雜湊的列舉值列表。
    :param example: 可選，示例值。
    :param tags: 可選，自由標籤列表。
    """

    name: str
    type: ColumnType
    nullable: bool = True
    default: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    logical_type: Optional[str] = None
    analysis_role: Optional[str] = None
    unit: Optional[str] = None
    enum_values: Optional[list[Any]] = None
    example: Optional[Any] = None
    tags: list[str] = field(default_factory=list)

    def to_sql(self) -> str:
        """:returns: 列定義的 SQL 片段。"""
        parts = [self.name, self.type]
        if not self.nullable:
            parts.append("NOT NULL")
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")
        return " ".join(parts)


@dataclass
class TableSchema:
    """資料庫表結構定義（用於動態建表）。

    :param name: 表名。
    :param columns: 列定義列表。
    :param primary_key: 主鍵列名列表。
    :param indexes: 索引定義列表（每項為列名列表）。
    """
    name: str
    columns: List[ColumnDef]
    primary_key: List[str] = field(default_factory=list)
    indexes: List[List[str]] = field(default_factory=list)

    def to_create_sql(self) -> str:
        """:returns: ``CREATE TABLE`` SQL 語句。"""
        column_defs = [col.to_sql() for col in self.columns]

        # Add primary key constraint
        if self.primary_key:
            pk_cols = ", ".join(self.primary_key)
            column_defs.append(f"PRIMARY KEY ({pk_cols})")

        columns_sql = ",\n    ".join(column_defs)
        return f"CREATE TABLE IF NOT EXISTS {self.name} (\n    {columns_sql}\n)"

    def to_index_sql(self) -> List[str]:
        """:returns: ``CREATE INDEX`` SQL 語句列表。"""
        statements = []
        for idx_cols in self.indexes:
            idx_name = f"idx_{self.name}_{'_'.join(idx_cols)}"
            cols = ", ".join(idx_cols)
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {self.name}({cols})"
            )
        return statements
