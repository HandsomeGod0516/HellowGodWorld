"""Replay data writer for storing simulation state to SQLite.

Replay storage has two layers:

- Storage schema: actual SQLite tables registered via :class:`TableSchema`
- Semantic metadata: dataset and column catalog tables used by replay export and
  downstream analysis
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from .replay_metadata import (
    COLUMN_CATALOG_TABLE,
    DATASET_CATALOG_TABLE,
    ReplayDatasetSpec,
)
from .table_schema import ColumnDef, TableSchema


def _quote_identifier(name: str) -> str:
    """Quote SQLite identifiers for raw SQL statements."""
    return '"' + name.replace('"', '""') + '"'


class ReplayWriter:
    """Thread-safe async SQLite writer for replay data.

    This class handles writing simulation state data to SQLite database
    for later replay and analysis. It uses SQLAlchemy/SQLModel for ORM support.
    """

    def __init__(self, db_path: Path):
        """创建回放写入器。

        :param db_path: SQLite 数据库文件路径。
        """
        self._db_path = db_path
        self._engine: Optional[AsyncEngine] = None
        self._session_maker: Optional[sessionmaker] = None
        self._lock = asyncio.Lock()
        self._registered_tables: Set[str] = set()
        self._registered_datasets: Set[str] = set()

    async def init(self) -> None:
        """初始化数据库连接并创建 replay catalog 表。"""
        # Ensure parent directory exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create async engine
        connection_string = f"sqlite+aiosqlite:///{self._db_path}"
        self._engine = create_async_engine(connection_string, echo=False)

        # Create session maker
        self._session_maker = sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

        await self._ensure_catalog_tables()


    async def close(self) -> None:
        """关闭数据库连接并释放资源。"""
        if self._engine:
            await self._engine.dispose()
            self._engine = None

    # ==================== Generic Table Operations ====================

    async def _ensure_catalog_tables(self) -> None:
        """Create replay metadata catalog tables."""
        dataset_schema = TableSchema(
            name=DATASET_CATALOG_TABLE,
            columns=[
                ColumnDef("dataset_id", "TEXT", nullable=False),
                ColumnDef("table_name", "TEXT", nullable=False),
                ColumnDef("module_name", "TEXT", nullable=False),
                ColumnDef("kind", "TEXT", nullable=False),
                ColumnDef("title", "TEXT"),
                ColumnDef("description", "TEXT"),
                ColumnDef("entity_key", "TEXT"),
                ColumnDef("step_key", "TEXT"),
                ColumnDef("time_key", "TEXT"),
                ColumnDef("default_order_json", "JSON", nullable=False),
                ColumnDef("capabilities_json", "JSON", nullable=False),
                ColumnDef("version", "INTEGER", nullable=False, default="1"),
                ColumnDef("created_at", "TIMESTAMP", nullable=False),
            ],
            primary_key=["dataset_id"],
            indexes=[["table_name"], ["module_name"], ["kind"]],
        )
        column_schema = TableSchema(
            name=COLUMN_CATALOG_TABLE,
            columns=[
                ColumnDef("dataset_id", "TEXT", nullable=False),
                ColumnDef("column_name", "TEXT", nullable=False),
                ColumnDef("sqlite_type", "TEXT", nullable=False),
                ColumnDef("logical_type", "TEXT"),
                ColumnDef("analysis_role", "TEXT"),
                ColumnDef("title", "TEXT"),
                ColumnDef("description", "TEXT"),
                ColumnDef("unit", "TEXT"),
                ColumnDef("enum_json", "JSON"),
                ColumnDef("example_json", "JSON"),
                ColumnDef("nullable", "INTEGER", nullable=False),
                ColumnDef("tags_json", "JSON", nullable=False),
            ],
            primary_key=["dataset_id", "column_name"],
            indexes=[["dataset_id"], ["logical_type"], ["analysis_role"]],
        )
        await self.register_table(dataset_schema)
        await self.register_table(column_schema)

    async def register_dataset(
        self,
        spec: ReplayDatasetSpec,
        columns: List[ColumnDef],
    ) -> None:
        """Register replay dataset and column metadata."""
        await self._ensure_catalog_tables()
        dataset_row = {
            "dataset_id": spec.dataset_id,
            "table_name": spec.table_name,
            "module_name": spec.module_name,
            "kind": spec.kind,
            "title": spec.title,
            "description": spec.description,
            "entity_key": spec.entity_key,
            "step_key": spec.step_key,
            "time_key": spec.time_key,
            "default_order_json": spec.default_order,
            "capabilities_json": spec.capabilities,
            "version": spec.version,
            "created_at": datetime.now(),
        }
        column_rows = [
            {
                "dataset_id": spec.dataset_id,
                "column_name": column.name,
                "sqlite_type": column.type,
                "logical_type": column.logical_type,
                "analysis_role": column.analysis_role,
                "title": column.title,
                "description": column.description,
                "unit": column.unit,
                "enum_json": column.enum_values,
                "example_json": column.example,
                "nullable": 1 if column.nullable else 0,
                "tags_json": column.tags,
            }
            for column in columns
        ]

        async with self._lock:
            async with self._session_maker() as session:
                await session.execute(
                    text(
                        f"INSERT OR REPLACE INTO {DATASET_CATALOG_TABLE} "
                        "(dataset_id, table_name, module_name, kind, title, description, "
                        "entity_key, step_key, time_key, default_order_json, capabilities_json, version, created_at) "
                        "VALUES (:dataset_id, :table_name, :module_name, :kind, :title, :description, "
                        ":entity_key, :step_key, :time_key, :default_order_json, :capabilities_json, :version, :created_at)"
                    ),
                    self._process_data_for_write(dataset_row),
                )
                await session.execute(
                    text(
                        f"DELETE FROM {COLUMN_CATALOG_TABLE} WHERE dataset_id = :dataset_id"
                    ),
                    {"dataset_id": spec.dataset_id},
                )
                if column_rows:
                    await session.execute(
                        text(
                            f"INSERT INTO {COLUMN_CATALOG_TABLE} "
                            "(dataset_id, column_name, sqlite_type, logical_type, analysis_role, title, description, unit, enum_json, example_json, nullable, tags_json) "
                            "VALUES (:dataset_id, :column_name, :sqlite_type, :logical_type, :analysis_role, :title, :description, :unit, :enum_json, :example_json, :nullable, :tags_json)"
                        ),
                        [self._process_data_for_write(row) for row in column_rows],
                    )
                await session.commit()
        self._registered_datasets.add(spec.dataset_id)

    async def register_table(self, schema: TableSchema) -> None:
        """动态注册并创建新表（用于环境模块自定义回放表）。

        :param schema: 表结构定义。
        """
        if schema.name in self._registered_tables:
            return

        # Check if table already exists in SQLModel metadata (it shouldn't for dynamic ones)
        if schema.name in SQLModel.metadata.tables:
            self._registered_tables.add(schema.name)
            return

        async with self._lock:
            # Create table using SQLAlchemy Core
            # We construct a Table object and create it
            async with self._engine.begin() as conn:
                 # Use raw SQL for dynamic creation as it's easier to map TableSchema to SQL than constructing SA Table dynamically
                create_sql = schema.to_create_sql()
                await conn.execute(text(create_sql))
                await self._ensure_table_columns(conn, schema)
                
                # Create indexes
                for index_sql in schema.to_index_sql():
                    await conn.execute(text(index_sql))

            self._registered_tables.add(schema.name)

    async def _ensure_table_columns(self, conn: Any, schema: TableSchema) -> None:
        """Add newly declared columns to an existing dynamic replay table."""
        result = await conn.execute(
            text(f"PRAGMA table_info({_quote_identifier(schema.name)})")
        )
        existing_columns = {row[1] for row in result.fetchall()}
        for column in schema.columns:
            if column.name in existing_columns:
                continue
            await conn.execute(text(self._column_add_sql(schema.name, column)))

    @staticmethod
    def _column_add_sql(table_name: str, column: ColumnDef) -> str:
        parts = [
            "ALTER TABLE",
            _quote_identifier(table_name),
            "ADD COLUMN",
            _quote_identifier(column.name),
            column.type,
        ]
        if column.default is not None:
            parts.append(f"DEFAULT {column.default}")
        return " ".join(parts)

    async def write(self, table_name: str, data: Dict[str, Any]) -> None:
        """向指定表写入一行（通用写入，支持动态表）。

        :param table_name: 表名。
        :param data: 列名到值的映射。
        """
        # For statically defined tables, allow using generic write but process specially?
        # Actually, for dynamic tables, we need to construct INSERT statement
        
        # Prepare data (handle datetime and JSON)
        processed_data = self._process_data_for_write(data)

        async with self._lock:
           async with self._session_maker() as session:
                # Use raw SQL for generic write to arbitrary tables (simplest for dynamic schemas)
                # Or reflect table... but reflection is slow.
                # Since we know the schema from register_table, we could cache SA Table objects.
                # But here we just use what we have.
                
                columns = list(processed_data.keys())
                placeholders = ", ".join([f":{col}" for col in columns])
                columns_str = ", ".join(_quote_identifier(col) for col in columns)
                sql = text(
                    f"INSERT OR REPLACE INTO {_quote_identifier(table_name)} "
                    f"({columns_str}) VALUES ({placeholders})"
                )
                
                await session.execute(sql, processed_data)
                await session.commit()

    async def write_batch(
        self, table_name: str, data_list: List[Dict[str, Any]]
    ) -> None:
        """批量写入多行（单事务）。"""
        if not data_list:
            return

        processed_list = [self._process_data_for_write(d) for d in data_list]
        columns = list(processed_list[0].keys())
        placeholders = ", ".join([f":{col}" for col in columns])
        columns_str = ", ".join(_quote_identifier(col) for col in columns)
        sql = text(
            f"INSERT OR REPLACE INTO {_quote_identifier(table_name)} "
            f"({columns_str}) VALUES ({placeholders})"
        )

        async with self._lock:
            async with self._session_maker() as session:
                await session.execute(sql, processed_list)
                await session.commit()
    
    def _process_data_for_write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """将数据转换为适合 SQLite/SQLAlchemy 的形式（datetime/JSON 等）。"""
        new_data = {}
        for k, v in data.items():
            if isinstance(v, datetime):
                new_data[k] = v # SQLAlchemy handles datetime
            elif isinstance(v, (dict, list)):
                # If we use JSON type in SA, we can pass dict directly. 
                # But for dynamic tables created via SQL, they might be TEXT columns.
                # We need to trust how they were defined.
                # However, our dynamic schema defines JSON as "JSON" type (which is TEXT affinity in SQLite)
                # It's safer to dump string for raw SQL execution unless we use SA Table bound parameter
                new_data[k] = json.dumps(v, ensure_ascii=False)
            else:
                new_data[k] = v
        return new_data
