"""Database persistence for generated seed data."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .schema_ir import Column, DataType, Schema, Table


class SQLiteSeedStore:
    """Persist a generated schema and its rows in a SQLite database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, schema: Schema, generated_data: dict[str, dict[str, dict[str, Any]]]) -> dict[str, int]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(f".{self.path.name}.tmp")
        temporary_path.unlink(missing_ok=True)
        try:
            with sqlite3.connect(temporary_path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                self._create_tables(connection, schema)
                row_counts = {}
                for table in schema.tables:
                    rows = generated_data.get(table.name, {})
                    self._insert_rows(connection, table, rows)
                    row_counts[table.name] = len(rows)
                connection.commit()
            os.replace(temporary_path, self.path)
            return row_counts
        finally:
            temporary_path.unlink(missing_ok=True)

    def _create_tables(self, connection: sqlite3.Connection, schema: Schema) -> None:
        for table in schema.tables:
            columns = [self._column_sql(column) for column in table.columns]
            primary_key = table.primary_key or tuple(
                column.name for column in table.columns if column.pk
            )
            if primary_key:
                columns.append(
                    f"PRIMARY KEY ({', '.join(self._quote(name) for name in primary_key)})"
                )
            for constraint in table.constraints:
                if constraint.get("type") == "unique":
                    names = constraint.get("columns", ())
                    columns.append(
                        f"UNIQUE ({', '.join(self._quote(name) for name in names)})"
                    )
            for relationship in schema.relationships:
                if relationship.source_table != table.name:
                    continue
                source = ", ".join(self._quote(name) for name in relationship.source_columns)
                target = ", ".join(self._quote(name) for name in relationship.target_columns)
                columns.append(
                    f"FOREIGN KEY ({source}) REFERENCES "
                    f"{self._quote(relationship.target_table)} ({target})"
                )
            ddl = (
                f"CREATE TABLE {self._quote(table.name)} ("
                f"{', '.join(columns)})"
            )
            connection.execute(ddl)
            for index in table.indexes:
                unique = "UNIQUE " if index.unique else ""
                index_name = index.name or f"idx_{table.name}_{'_'.join(index.columns)}"
                columns_sql = ", ".join(self._quote(name) for name in index.columns)
                connection.execute(
                    f"CREATE {unique}INDEX {self._quote(index_name)} "
                    f"ON {self._quote(table.name)} ({columns_sql})"
                )

    def _insert_rows(
        self,
        connection: sqlite3.Connection,
        table: Table,
        rows: dict[str, dict[str, Any]],
    ) -> None:
        if not rows or not table.columns:
            return
        column_names = [column.name for column in table.columns]
        placeholders = ", ".join("?" for _ in column_names)
        quoted_columns = ", ".join(self._quote(name) for name in column_names)
        values = [
            [self._sqlite_value(row.get(column.name), column) for column in table.columns]
            for row in rows.values()
        ]
        connection.executemany(
            f"INSERT INTO {self._quote(table.name)} ({quoted_columns}) VALUES ({placeholders})",
            values,
        )

    @staticmethod
    def _quote(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def _column_sql(self, column: Column) -> str:
        definition = f"{self._quote(column.name)} {self._sqlite_type(column.data_type)}"
        if column.not_null and not column.pk:
            definition += " NOT NULL"
        if column.unique and not column.pk:
            definition += " UNIQUE"
        return definition

    @staticmethod
    def _sqlite_type(data_type: DataType) -> str:
        if data_type.is_array or data_type.name in {"json", "jsonb"}:
            return "TEXT"
        if data_type.name in {"int", "bigint", "smallint", "serial", "bigserial"}:
            return "INTEGER"
        if data_type.name in {"decimal", "float", "real", "double", "numeric", "money"}:
            return "REAL"
        if data_type.name == "bool":
            return "INTEGER"
        return "TEXT"

    @staticmethod
    def _sqlite_value(value: Any, column: Column) -> Any:
        if value is None:
            return None
        if column.data_type.is_array or column.data_type.name in {"json", "jsonb"}:
            return json.dumps(value, separators=(",", ":"))
        if column.data_type.name == "bool":
            return int(value)
        return value


def store_seeded_data(
    schema: Schema,
    generated_data: dict[str, dict[str, dict[str, Any]]],
    database_path: str | Path,
) -> dict[str, int]:
    """Persist generated rows and return counts keyed by table name."""
    return SQLiteSeedStore(database_path).write(schema, generated_data)
