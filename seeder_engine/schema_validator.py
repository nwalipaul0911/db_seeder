"""Validation and inspection helpers for DBML schemas."""

from collections import defaultdict
from typing import Any

from .schema_loader import load_schema
from .logging_config import get_logger

logger = get_logger(__name__)


class SchemaValidationError(ValueError):
    """Raised when DBML cannot be parsed or violates relational rules."""


def _build_fk_map(relationships):
    foreign_keys = []
    for relationship in relationships:
        if relationship.cardinality == "many-to-many":
            continue
        for source, target in zip(
            relationship.source_columns, relationship.target_columns
        ):
            foreign_keys.append(
                {
                    "table": relationship.source_table,
                    "column": source,
                    "target_table": relationship.target_table,
                    "target_column": target,
                }
            )
    return foreign_keys


def _check_circular_dependencies(table_names: set[str], foreign_keys: list[dict[str, str]]):
    graph = defaultdict(set)
    for foreign_key in foreign_keys:
        source = foreign_key["table"]
        target = foreign_key["target_table"]
        if source != target:
            graph[source].add(target)

    visiting = set()
    visited = set()

    def visit(table_name):
        if table_name in visiting:
            raise SchemaValidationError(
                f"Circular foreign-key dependency detected at table '{table_name}'."
            )
        if table_name in visited:
            return
        visiting.add(table_name)
        for dependency in graph[table_name]:
            visit(dependency)
        visiting.remove(table_name)
        visited.add(table_name)

    for table_name in table_names:
        visit(table_name)


def validate_schema(
    schema_text: str, source_format: str = "auto", dialect: str = "sqlite"
) -> dict[str, Any]:
    """Parse a supported schema and return a compact summary."""
    if not schema_text.strip():
        raise SchemaValidationError("DBML text is required.")

    try:
        schema = load_schema(schema_text, source_format, dialect)
    except Exception as exc:
        logger.exception("Schema parsing failed: format=%s dialect=%s", source_format, dialect)
        raise SchemaValidationError(f"DBML parse failed: {exc}") from exc

    table_names = [table.name for table in schema.tables]
    duplicate_tables = sorted(
        name for name in set(table_names) if table_names.count(name) > 1
    )
    if duplicate_tables:
        raise SchemaValidationError(
            f"Duplicate table names: {', '.join(duplicate_tables)}."
        )

    table_summaries = []
    for table in schema.tables:
        column_names = [column.name for column in table.columns]
        duplicate_columns = sorted(
            name for name in set(column_names) if column_names.count(name) > 1
        )
        if duplicate_columns:
            raise SchemaValidationError(
                f"Table '{table.name}' has duplicate columns: "
                f"{', '.join(duplicate_columns)}."
            )
        table_summaries.append(
            {"name": table.name, "columns": column_names, "column_count": len(column_names)}
        )

    foreign_keys = _build_fk_map(schema.relationships)
    known_tables = set(table_names)
    known_columns = {
        table.name: {column.name for column in table.columns}
        for table in schema.tables
    }
    for foreign_key in foreign_keys:
        if foreign_key["target_table"] not in known_tables:
            raise SchemaValidationError(
                f"Foreign key '{foreign_key['table']}.{foreign_key['column']}' "
                f"references missing table '{foreign_key['target_table']}'."
            )
        if foreign_key["target_column"] not in known_columns[foreign_key["target_table"]]:
            raise SchemaValidationError(
                f"Foreign key '{foreign_key['table']}.{foreign_key['column']}' "
                f"references missing column "
                f"'{foreign_key['target_table']}.{foreign_key['target_column']}'."
            )

    _check_circular_dependencies(known_tables, foreign_keys)

    result = {
        "valid": True,
        "table_count": len(schema.tables),
        "enum_count": len(schema.enums),
        "reference_count": len(foreign_keys),
        "tables": table_summaries,
        "foreign_keys": foreign_keys,
        "enums": [enum.name for enum in schema.enums],
    }
    logger.info(
        "Schema validated: format=%s dialect=%s tables=%d references=%d",
        source_format,
        dialect,
        result["table_count"],
        result["reference_count"],
    )
    return result


def validate_dbml(dbml_text: str) -> dict[str, Any]:
    """Backward-compatible DBML validation helper."""
    return validate_schema(dbml_text, source_format="dbml")
