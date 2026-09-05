from seeder_engine.schema_ir import Schema
from seeder_engine.schema_validator import validate_schema
from seeder_engine.sql_adapter import SQLAdapter


SQL = """
CREATE TABLE users (
  id BIGINT PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
  id BIGINT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  total DECIMAL(12, 2),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX idx_orders_user ON orders(user_id, id);
"""


def test_sql_adapter_builds_schema_ir():
    schema = SQLAdapter("postgres").parse(SQL)

    assert isinstance(schema, Schema)
    assert schema.metadata["dialect"] == "postgres"
    assert schema.table_map["users"].primary_key == ("id",)
    assert schema.table_map["users"].columns[1].unique is True
    assert schema.table_map["orders"].columns[2].data_type.parameters == (12, 2)
    assert schema.relationships[0].target_table == "users"
    references = schema.table_map["orders"].columns[1].references
    assert references is not None
    assert references.target_columns == ("id",)
    assert schema.table_map["orders"].indexes[0].columns == ("user_id", "id")


def test_sql_adapter_detects_ddl():
    assert SQLAdapter().detect("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    assert not SQLAdapter().detect("Table users { id int [pk] }")


def test_sqlite_adapter_handles_string_constraint_values():
  source = """
  PRAGMA foreign_keys = ON;
  CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'admin'))
  );
  CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
  );
  CREATE INDEX idx_orders_user ON orders(user_id);
  """

  schema = SQLAdapter("sqlite").parse(source)

  assert schema.table_map["users"].columns[1].default == "'customer'"
  assert schema.table_map["users"].constraints[0]["type"] == "check"
  assert schema.relationships[0].target_table == "users"
  assert schema.table_map["orders"].indexes[0].columns == ("user_id",)


def test_repository_mysql_schema_validates():
  source = open("schema_mysql.sql", encoding="utf-8").read()

  summary = validate_schema(source, source_format="sql", dialect="mysql")

  assert summary["table_count"] == 11
  assert summary["reference_count"] == 15
  schema = SQLAdapter("mysql").parse(source)
  assert schema.table_map["users"].indexes[0].columns == ("role",)


def test_sql_adapter_preserves_checks_composite_uniques_and_enums():
  source = """
  CREATE TYPE status AS ENUM ('active', 'inactive');
  CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    status status NOT NULL,
    age INTEGER CHECK (age >= 0),
    email TEXT,
    CONSTRAINT uq_account UNIQUE (email, status),
    CHECK (age < 150)
  );
  """

  schema = SQLAdapter("postgres").parse(source)
  account = schema.table_map["accounts"]

  assert schema.enum_map["status"].values == ("active", "inactive")
  assert account.columns[1].data_type.enum_name == "status"
  assert {constraint["type"] for constraint in account.constraints} == {"check", "unique"}
  assert any(
    index.unique and index.columns == ("email", "status")
    for index in account.indexes
  )
  assert any(constraint.get("column") == "age" for constraint in account.constraints)


def test_repository_postgres_schema_validates():
  source = open("schemas/schema.sql", encoding="utf-8").read()

  summary = validate_schema(source, source_format="sql", dialect="postgres")

  assert summary["table_count"] == 11
  assert summary["enum_count"] == 3
  assert summary["reference_count"] == 15
