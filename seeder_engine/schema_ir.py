"""Canonical schema representation shared by adapters and the seeder."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class DataType:
    name: str
    parameters: tuple[Any, ...] = ()
    enum_name: str | None = None
    is_array: bool = False
    element_type: str | None = None
    enum_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForeignKey:
    columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]
    on_delete: str | None = None
    on_update: str | None = None


@dataclass(frozen=True)
class Index:
    columns: tuple[str, ...]
    unique: bool = False
    name: str | None = None

    @property
    def subjects(self) -> tuple["IndexSubject", ...]:
        return tuple(IndexSubject(column) for column in self.columns)


@dataclass(frozen=True)
class IndexSubject:
    name: str


@dataclass(frozen=True)
class Column:
    name: str
    data_type: DataType
    pk: bool = False
    nullable: bool = True
    default: Any = None
    unique: bool = False
    generated: bool = False
    references: ForeignKey | None = None

    @property
    def not_null(self) -> bool:
        return not self.nullable

    @property
    def type(self) -> str:
        return self.data_type.enum_name or self.data_type.name


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    indexes: tuple[Index, ...] = ()
    constraints: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Enum:
    name: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Relationship:
    source_table: str
    source_columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]
    cardinality: str | None = None


@dataclass(frozen=True)
class Schema:
    tables: tuple[Table, ...]
    enums: tuple[Enum, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def table_map(self) -> dict[str, Table]:
        return {table.name: table for table in self.tables}

    @property
    def enum_map(self) -> dict[str, Enum]:
        return {enum.name: enum for enum in self.enums}


class SchemaAdapter(Protocol):
    source_format: str

    def detect(self, source: str) -> bool: ...

    def parse(self, source: str) -> Schema: ...

    def capabilities(self) -> dict[str, str]: ...
