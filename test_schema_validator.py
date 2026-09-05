import pytest

from seeder_engine.schema_validator import SchemaValidationError, validate_dbml


VALID_DBML = """
Table users {
  id int [pk]
}

Table posts {
  id int [pk]
  user_id int
}

Ref: posts.user_id > users.id
"""


def test_valid_dbml_returns_schema_summary():
    summary = validate_dbml(VALID_DBML)

    assert summary["valid"] is True
    assert summary["table_count"] == 2
    assert summary["reference_count"] == 1
    assert summary["tables"][1]["name"] == "posts"


def test_empty_dbml_is_rejected():
    with pytest.raises(SchemaValidationError, match="DBML text is required"):
        validate_dbml("   ")


def test_malformed_dbml_is_rejected():
    with pytest.raises(SchemaValidationError, match="DBML parse failed"):
        validate_dbml("Table users { id int }")


def test_circular_dependencies_are_rejected():
    circular_dbml = """
    Table users {
      id int [pk]
      team_id int
    }

    Table teams {
      id int [pk]
      owner_id int
    }

    Ref: users.team_id > teams.id
    Ref: teams.owner_id > users.id
    """

    with pytest.raises(SchemaValidationError, match="Circular foreign-key"):
        validate_dbml(circular_dbml)
