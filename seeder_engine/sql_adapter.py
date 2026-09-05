"""SQL DDL adapter backed by sqlglot."""

import re
from typing import Any

from sqlglot import expressions as exp
from sqlglot import parse

from .schema_ir import Column, DataType, Enum, ForeignKey, Index, Relationship, Schema, Table


class SQLAdapter:
    source_format = "sql"
    supported_dialects = ("sqlite", "postgres", "mysql")

    def __init__(self, dialect: str = "sqlite"):
        if dialect not in self.supported_dialects:
            raise ValueError(f"Unsupported SQL dialect: {dialect}")
        self.dialect = dialect

    def detect(self, source: str) -> bool:
        text = re.sub(r"(?s)/\*.*?\*/|--[^\n]*(?:\n|$)", " ", source).lstrip().lower()
        return text.startswith(("create table", "create unique index", "create index"))

    def parse(self, source: str) -> Schema:
        statements = parse(source, read=self.dialect)
        table_builders: dict[str, dict[str, Any]] = {}
        relationships: list[Relationship] = []
        indexes: list[Index] = []
        enums: list[Enum] = []

        for statement in statements:
            if isinstance(statement, exp.Create) and statement.args.get("kind") == "TYPE":
                enum = self._parse_enum(statement)
                if enum is not None:
                    enums.append(enum)
            elif isinstance(statement, exp.Create) and statement.args.get("kind") == "TABLE":
                self._parse_create_table(statement, table_builders, relationships, indexes)
            elif isinstance(statement, exp.Create) and statement.args.get("kind") == "INDEX":
                indexes.append(self._parse_index(statement))

        tables = []
        for table_name, builder in table_builders.items():
            table_indexes = tuple(
                index for index in indexes if index.name and index.name.startswith(f"{table_name}:")
            )
            all_indexes = table_indexes + tuple(builder.get("indexes", []))
            cleaned_indexes = tuple(
                Index(columns=index.columns, unique=index.unique, name=index.name.split(":", 1)[-1])
                for index in all_indexes
            )
            table_relationships = [
                relationship
                for relationship in relationships
                if relationship.source_table == table_name
            ]
            references = {
                column: ForeignKey(
                    columns=relationship.source_columns,
                    target_table=relationship.target_table,
                    target_columns=relationship.target_columns,
                    on_delete=relationship.cardinality if relationship.cardinality in {"CASCADE", "RESTRICT", "SET NULL"} else None,
                )
                for relationship in table_relationships
                for column in relationship.source_columns
            }
            columns = tuple(
                column if column.references is not None else Column(
                    name=column.name,
                    data_type=column.data_type,
                    pk=column.pk,
                    nullable=column.nullable,
                    default=column.default,
                    unique=column.unique,
                    generated=column.generated,
                    references=references.get(column.name),
                )
                for column in builder["columns"]
            )
            tables.append(
                Table(
                    name=table_name,
                    columns=columns,
                    primary_key=builder["primary_key"],
                    indexes=cleaned_indexes,
                    constraints=tuple(builder["constraints"]),
                )
            )

        return Schema(
            tables=tuple(tables),
            enums=tuple(enums),
            relationships=tuple(relationships),
            metadata={"source_format": self.source_format, "dialect": self.dialect},
        )

    def capabilities(self) -> dict[str, str]:
        return {
            "tables": "supported",
            "columns": "supported",
            "primary_keys": "supported",
            "foreign_keys": "supported",
            "unique_constraints": "supported",
            "indexes": "supported",
            "enums": "partial",
            "defaults": "supported",
            "check_constraints": "partial",
            "generated_columns": "partial",
        }

    def _sql(self, value) -> str:
        if hasattr(value, "sql"):
            return value.sql(dialect=self.dialect)
        return str(value)

    @staticmethod
    def _name(value) -> str:
        return value.name if hasattr(value, "name") else str(value)

    def _parse_create_table(self, statement, builders, relationships, indexes):
        schema = statement.this
        table = schema.this
        table_name = table.name
        builder = builders.setdefault(
            table_name, {"columns": [], "primary_key": (), "constraints": []}
        )
        table_primary_key = []
        for item in schema.expressions:
            if isinstance(item, exp.IndexColumnConstraint):
                builder.setdefault("indexes", []).append(
                    Index(
                        columns=tuple(self._name(expression.this) for expression in item.expressions),
                        unique=False,
                        name=self._name(item.this),
                    )
                )
            elif isinstance(item, exp.ColumnDef):
                column, checks = self._parse_column(item)
                builder["columns"].append(column)
                builder["constraints"].extend(checks)
                if column.pk:
                    table_primary_key.append(column.name)
                for constraint in item.args.get("constraints", []):
                    reference = constraint.args.get("kind")
                    if isinstance(reference, exp.Reference):
                        relationships.append(
                            self._parse_inline_foreign_key(
                                table_name, item.name, reference
                            )
                        )
            elif isinstance(item, exp.PrimaryKey):
                table_primary_key.extend(self._column_names(item.expressions))
            elif isinstance(item, exp.ForeignKey):
                relationships.append(self._parse_foreign_key(table_name, item))
            elif isinstance(item, exp.Constraint):
                self._parse_constraint(table_name, item, builder, relationships)
        if table_primary_key:
            builder["primary_key"] = tuple(table_primary_key)
            primary_key_names = set(table_primary_key)
            builder["columns"] = [
                Column(
                    name=column.name,
                    data_type=column.data_type,
                    pk=column.name in primary_key_names,
                    nullable=column.nullable,
                    default=column.default,
                    unique=column.unique,
                    generated=column.generated,
                    references=column.references,
                )
                for column in builder["columns"]
            ]

    def _parse_column(self, definition) -> tuple[Column, list[dict[str, Any]]]:
        constraints = definition.args.get("constraints", [])
        primary_key = any(isinstance(item.args.get("kind"), exp.PrimaryKeyColumnConstraint) for item in constraints)
        unique = any(isinstance(item.args.get("kind"), exp.UniqueColumnConstraint) for item in constraints)
        not_null = any(isinstance(item.args.get("kind"), exp.NotNullColumnConstraint) for item in constraints)
        default = None
        checks = []
        for item in constraints:
            constraint = item.args.get("kind")
            if isinstance(constraint, exp.DefaultColumnConstraint):
                default = self._sql(constraint.this)
            elif isinstance(constraint, exp.CheckColumnConstraint):
                checks.append({"type": "check", "expression": self._sql(constraint.this), "column": definition.name})
        return Column(
            name=definition.name,
            data_type=self._parse_type(definition.args["kind"]),
            pk=primary_key,
            nullable=not (primary_key or not_null),
            default=default,
            unique=unique,
            generated=False,
        ), checks

    def _parse_type(self, data_type) -> DataType:
        if data_type.this == exp.DType.USERDEFINED:
            user_type = data_type.args.get("kind")
            return DataType(name="enum", enum_name=self._name(user_type))
        name = data_type.this.name.lower() if hasattr(data_type.this, "name") else str(data_type.this).lower()
        parameters = tuple(
            int(parameter.this.this)
            for parameter in data_type.expressions
            if isinstance(parameter, exp.DataTypeParam) and isinstance(parameter.this, exp.Literal) and not parameter.this.is_string
        )
        return DataType(name=name, parameters=parameters)

    def _parse_foreign_key(self, source_table: str, definition) -> Relationship:
        reference = definition.args["reference"]
        target = reference.this.this
        options = " ".join(self._sql(option).upper() for option in reference.args.get("options", []))
        return Relationship(
            source_table=source_table,
            source_columns=tuple(self._column_names(definition.expressions)),
            target_table=self._name(target),
            target_columns=tuple(self._name(column) for column in reference.this.expressions),
            cardinality=options or "many-to-one",
        )

    def _parse_inline_foreign_key(
        self, source_table: str, source_column: str, reference
    ) -> Relationship:
        target = reference.this.this
        options = " ".join(
            str(option).upper() for option in reference.args.get("options", [])
        )
        return Relationship(
            source_table=source_table,
            source_columns=(source_column,),
            target_table=self._name(target),
            target_columns=tuple(self._name(column) for column in reference.this.expressions),
            cardinality=options or "many-to-one",
        )

    def _parse_constraint(self, table_name, constraint, builder, relationships):
        for expression in constraint.expressions:
            if isinstance(expression, exp.PrimaryKey):
                names = tuple(self._column_names(expression.expressions))
                builder["primary_key"] = names
            elif isinstance(expression, exp.UniqueColumnConstraint):
                columns = self._column_names(expression.this.expressions)
                builder["constraints"].append({"type": "unique", "columns": tuple(columns)})
                builder.setdefault("indexes", []).append(
                    Index(columns=tuple(columns), unique=True, name=constraint.name or "unique")
                )
            elif isinstance(expression, exp.ForeignKey):
                relationships.append(self._parse_foreign_key(table_name, expression))
            elif isinstance(expression, exp.Check):
                builder["constraints"].append(
                    {"type": "check", "expression": self._sql(expression.this)}
                )

    def _parse_index(self, statement) -> Index:
        index = statement.this
        columns = tuple(
            self._name(item.this)
            for item in index.args.get("params").args.get("columns", [])
            if isinstance(item, exp.Ordered) and isinstance(item.this, exp.Column)
        )
        return Index(
            columns=columns,
            unique=bool(statement.args.get("unique")),
            name=f"{self._name(index.args['table'])}:{self._name(index)}",
        )

    def _parse_enum(self, statement) -> Enum | None:
        data_type = statement.args.get("expression")
        if not data_type or data_type.this != exp.DType.ENUM:
            return None
        values = tuple(
            literal.this.strip("'")
            for literal in data_type.expressions
            if isinstance(literal, exp.Literal) and literal.is_string
        )
        return Enum(name=self._name(statement.this), values=values)

    @staticmethod
    def _column_names(expressions) -> list[str]:
        return [expression.name if hasattr(expression, "name") else str(expression) for expression in expressions]
