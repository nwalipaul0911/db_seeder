"""DBML adapter that converts PyDBML objects into the canonical Schema IR."""

from typing import Any

from pydbml import PyDBML

from .schema_ir import (
    Column,
    DataType,
    Enum,
    ForeignKey,
    Index,
    Relationship,
    Schema,
    Table,
)


class DBMLAdapter:
    source_format = "dbml"

    def detect(self, source: str) -> bool:
        text = source.lstrip().lower()
        return text.startswith(("table ", "enum ", "project ", "tablegroup "))

    def parse(self, source: str) -> Schema:
        database = PyDBML.parse(source)
        enums = tuple(self._convert_enum(enum) for enum in database.enums)
        relationships = tuple(
            relationship
            for ref in database.refs
            for relationship in self._convert_reference(ref)
        )
        tables = tuple(
            self._convert_table(table, relationships) for table in database.tables
        )
        return Schema(
            tables=tables,
            enums=enums,
            relationships=relationships,
            metadata={"source_format": self.source_format},
        )

    def capabilities(self) -> dict[str, str]:
        return {
            "tables": "supported",
            "columns": "supported",
            "primary_keys": "supported",
            "foreign_keys": "supported",
            "unique_constraints": "supported",
            "indexes": "supported",
            "enums": "supported",
            "defaults": "supported",
            "check_constraints": "partial",
            "generated_columns": "partial",
        }

    def _convert_enum(self, enum: Any) -> Enum:
        values = tuple(item.name for item in getattr(enum, "items", []))
        return Enum(name=enum.name, values=values)

    def _convert_type(self, raw_type: Any) -> DataType:
        if hasattr(raw_type, "name") and hasattr(raw_type, "items"):
            return DataType(name="enum", enum_name=raw_type.name)
        return DataType(name=str(raw_type).lower())

    def _convert_table(
        self, table: Any, relationships: tuple[Relationship, ...]
    ) -> Table:
        table_relationships = [
            relationship
            for relationship in relationships
            if relationship.source_table == table.name
        ]
        reference_by_column = {
            column: ForeignKey(
                columns=relationship.source_columns,
                target_table=relationship.target_table,
                target_columns=relationship.target_columns,
            )
            for relationship in table_relationships
            for column in relationship.source_columns
        }
        columns = tuple(
            Column(
                name=column.name,
                data_type=self._convert_type(column.type),
                pk=bool(column.pk),
                nullable=not (column.pk or column.not_null),
                default=column.default,
                unique=bool(column.unique),
                generated=bool(getattr(column, "increment", False)),
                references=reference_by_column.get(column.name),
            )
            for column in table.columns
        )
        primary_key = tuple(column.name for column in table.columns if column.pk)
        indexes = tuple(self._convert_index(index) for index in table.indexes)
        return Table(
            name=table.name,
            columns=columns,
            primary_key=primary_key,
            indexes=indexes,
        )

    def _convert_index(self, index: Any) -> Index:
        columns = tuple(subject.name for subject in getattr(index, "subjects", []))
        return Index(
            columns=columns,
            unique=bool(getattr(index, "unique", False)),
            name=getattr(index, "name", None),
        )

    def _convert_reference(self, ref: Any) -> list[Relationship]:
        if ref.type == "<>":
            cardinality = "many-to-many"
        else:
            cardinality = "many-to-one"
        if ref.type in [">", "-"]:
            source_columns, target_columns = ref.col1, ref.col2
        else:
            source_columns, target_columns = ref.col2, ref.col1
        return [
            Relationship(
                source_table=source_columns[0].table.name,
                source_columns=tuple(column.name for column in source_columns),
                target_table=target_columns[0].table.name,
                target_columns=tuple(column.name for column in target_columns),
                cardinality=cardinality,
            )
        ]
