"""Select a schema adapter and load input into the canonical Schema IR."""

from pathlib import Path

from .dbml_adapter import DBMLAdapter
from .schema_ir import Schema
from .sql_adapter import SQLAdapter
from .prisma_adapter import PrismaAdapter


def adapter_for_source(source: str, source_format: str = "auto", dialect: str = "sqlite"):
    if source_format == "dbml":
        return DBMLAdapter()
    if source_format == "sql":
        return SQLAdapter(dialect)
    if source_format == "prisma":
        return PrismaAdapter()
    prisma_adapter = PrismaAdapter()
    if prisma_adapter.detect(source):
        return prisma_adapter
    dbml_adapter = DBMLAdapter()
    if dbml_adapter.detect(source):
        return dbml_adapter
    sql_adapter = SQLAdapter(dialect)
    if sql_adapter.detect(source):
        return sql_adapter
    raise ValueError("Unable to detect schema format. Choose DBML or SQL explicitly.")


def load_schema(source: str, source_format: str = "auto", dialect: str = "sqlite") -> Schema:
    return adapter_for_source(source, source_format, dialect).parse(source)


def load_schema_file(path: str | Path, source_format: str = "auto", dialect: str = "sqlite") -> Schema:
    schema_path = Path(path)
    detected_format = source_format
    if detected_format == "auto":
        suffix = schema_path.suffix.lower()
        if suffix == ".sql":
            detected_format = "sql"
        elif suffix == ".dbml":
            detected_format = "dbml"
        elif suffix == ".prisma":
            detected_format = "prisma"
    return load_schema(schema_path.read_text(encoding="utf-8"), detected_format, dialect)
