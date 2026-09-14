"""Prisma schema adapter for the canonical Schema IR."""

import re
from typing import Any

from .schema_ir import Column, DataType, ForeignKey, Index, Enum, Relationship, Schema, Table


_BLOCK_RE = re.compile(r"(?ms)^\s*(model|enum)\s+(\w+)\s*\{(.*?)^\s*\}")


class PrismaAdapter:
    source_format = "prisma"

    def detect(self, source: str) -> bool:
        return bool(re.search(r"(?m)^\s*(model|enum)\s+\w+\s*\{", source))

    def parse(self, source: str) -> Schema:
        cleaned = self._strip_comments(source)
        enum_names = {
            name for kind, name, _ in _BLOCK_RE.findall(cleaned) if kind == "enum"
        }
        enums = []
        models = []
        for kind, name, body in _BLOCK_RE.findall(cleaned):
            if kind == "enum":
                enums.append(Enum(name=name, values=self._enum_values(body)))
            else:
                models.append((name, body))

        relationships = []
        table_builders = {}
        for model_name, body in models:
            builder = {
                "columns": [],
                "primary_key": (),
                "indexes": [],
                "constraints": [],
                "relation_specs": [],
            }
            for line in self._logical_lines(body):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("@@"):
                    self._parse_model_attribute(line, builder)
                    continue
                field = self._parse_field(line, enum_names)
                if field is None:
                    relation = self._parse_relation_field(model_name, line)
                    if relation:
                        builder["relation_specs"].append(relation)
                    continue
                column, attributes = field
                if column is not None:
                    builder["columns"].append(column)
                    if column.pk:
                        builder["primary_key"] += (column.name,)
                relation = self._relation_spec(model_name, column, attributes)
                if relation:
                    builder["relation_specs"].append(relation)
            table_builders[model_name] = builder

        for model_name, builder in table_builders.items():
            for relation in builder["relation_specs"]:
                relationships.append(
                    Relationship(
                        source_table=model_name,
                        source_columns=relation["fields"],
                        target_table=relation["target_table"],
                        target_columns=relation["references"],
                        cardinality="many-to-one",
                    )
                )

        tables = []
        for model_name, builder in table_builders.items():
            table_relationships = [
                relation for relation in relationships
                if relation.source_table == model_name
            ]
            references = {
                column: ForeignKey(
                    columns=relation.source_columns,
                    target_table=relation.target_table,
                    target_columns=relation.target_columns,
                )
                for relation in table_relationships
                for column in relation.source_columns
            }
            columns = tuple(
                Column(
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
                    name=model_name,
                    columns=columns,
                    primary_key=builder["primary_key"],
                    indexes=tuple(builder["indexes"]),
                    constraints=tuple(builder["constraints"]),
                )
            )

        return Schema(
            tables=tuple(tables),
            enums=tuple(enums),
            relationships=tuple(relationships),
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
            "check_constraints": "unsupported",
            "generated_columns": "partial",
        }

    @staticmethod
    def _strip_comments(source: str) -> str:
        return re.sub(r"(?m)//.*$", "", source)

    @staticmethod
    def _logical_lines(body: str) -> list[str]:
        lines = []
        current = ""
        depth = 0
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            current = f"{current} {line}".strip()
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                lines.append(current)
                current = ""
                depth = 0
        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _enum_values(body: str) -> tuple[str, ...]:
        values = []
        for line in body.splitlines():
            match = re.match(r"\s*(\w+)\b", line)
            if match and not line.strip().startswith("@@"):
                values.append(match.group(1))
        return tuple(values)

    def _parse_field(self, line: str, enum_names: set[str]):
        parts = line.split(None, 2)
        if len(parts) < 2 or parts[0].startswith("@"): 
            return None
        name, raw_type = parts[0], parts[1]
        attribute_text = parts[2] if len(parts) == 3 else ""
        is_list = raw_type.endswith("[]")
        is_optional = raw_type.endswith("?")
        base_type = raw_type.rstrip("[]?")
        attributes = self._attributes(attribute_text)
        scalar_types = {
            "String": "string", "Int": "int", "BigInt": "bigint",
            "Float": "decimal", "Decimal": "decimal", "Boolean": "bool",
            "DateTime": "timestamp", "Json": "json", "Bytes": "binary",
        }
        if base_type not in scalar_types and base_type not in enum_names:
            return None
        data_type = (
            DataType(
                name="enum",
                enum_name=base_type,
                is_array=is_list,
                element_type="enum" if is_list else None,
            )
            if base_type in enum_names
            else DataType(
                name=scalar_types[base_type],
                is_array=is_list,
                element_type=scalar_types[base_type] if is_list else None,
            )
        )
        primary_key = any(name == "id" for name, _ in attributes)
        unique = any(name == "unique" for name, _ in attributes)
        generated = False
        default = None
        for attribute_name, argument in attributes:
            if attribute_name == "default":
                if argument in {"autoincrement()"}:
                    generated = True
                elif argument in {"uuid()", "cuid()", "now()"}:
                    default = argument
                else:
                    default = self._literal(argument)
        return Column(
            name=name,
            data_type=data_type,
            pk=primary_key,
            nullable=not primary_key and is_optional,
            default=default,
            unique=unique,
            generated=generated,
        ), attributes

    def _parse_model_attribute(self, line: str, builder: dict[str, Any]):
        match = re.match(r"@@(id|unique|index)\s*\((.*)\)", line)
        if not match:
            return
        attribute_name, arguments = match.groups()
        columns = self._list_argument(arguments)
        if attribute_name == "id":
            builder["primary_key"] = tuple(columns)
        else:
            builder["indexes"].append(
                Index(columns=tuple(columns), unique=attribute_name == "unique")
            )
            if attribute_name == "unique":
                builder["constraints"].append({"type": "unique", "columns": tuple(columns)})

    def _relation_spec(self, model_name, column, attributes):
        for name, argument in attributes:
            if name != "relation" or not argument:
                continue
            fields = self._named_argument(argument, "fields")
            references = self._named_argument(argument, "references")
            if fields and references:
                target = column.data_type.enum_name if column else None
                return {
                    "target_table": target,
                    "fields": tuple(fields),
                    "references": tuple(references),
                }
        return None

    def _parse_relation_field(self, model_name: str, line: str):
        parts = line.split(None, 2)
        if len(parts) < 3:
            return None
        target_type = parts[1].rstrip("[]?")
        attributes = self._attributes(parts[2])
        for name, argument in attributes:
            if name != "relation" or not argument:
                continue
            fields = self._named_argument(argument, "fields")
            references = self._named_argument(argument, "references")
            if fields and references:
                return {
                    "target_table": target_type,
                    "fields": tuple(fields),
                    "references": tuple(references),
                }
        return None

    @staticmethod
    def _attributes(text: str) -> list[tuple[str, str | None]]:
        attributes = []
        for match in re.finditer(r"@(\w+)", text):
            start = match.end()
            if start >= len(text) or text[start] != "(":
                attributes.append((match.group(1), None))
                continue
            depth = 0
            end = start
            for end in range(start, len(text)):
                if text[end] == "(":
                    depth += 1
                elif text[end] == ")":
                    depth -= 1
                    if depth == 0:
                        break
            attributes.append((match.group(1), text[start + 1:end]))
        return attributes

    @staticmethod
    def _named_argument(text: str, name: str) -> list[str]:
        match = re.search(rf"\b{name}\s*:\s*\[([^]]+)\]", text)
        if not match:
            return []
        return [value.strip() for value in match.group(1).split(",")]

    @staticmethod
    def _list_argument(text: str) -> list[str]:
        match = re.search(r"\[([^]]+)\]", text)
        if match:
            return [value.strip() for value in match.group(1).split(",")]
        return [text.strip()]

    @staticmethod
    def _literal(value: str | None):
        if value is None:
            return None
        value = value.strip()
        if value.lower() in {"true", "false"}:
            return value.lower() == "true"
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
        return value.strip("'\"")
