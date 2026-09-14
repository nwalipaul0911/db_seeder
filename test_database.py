import sqlite3

from seeder_engine.main import seed
from seeder_engine.database import store_seeded_data
from seeder_engine.schema_loader import load_schema


DBML = """
Table users {
  id int [pk]
  name varchar
}

Table posts {
  id int [pk]
  user_id int
  metadata json
}

Ref: posts.user_id > users.id
"""


def test_store_seeded_data_creates_relational_sqlite_database(tmp_path):
    schema = load_schema(DBML, source_format="dbml")
    generated_data = {
        "users": {
            "1": {"id": 1, "name": "Ada"},
        },
        "posts": {
            "1": {"id": 1, "user_id": 1, "metadata": {"featured": True}},
        },
    }

    database_path = tmp_path / "seeded_data.sqlite3"
    counts = store_seeded_data(schema, generated_data, database_path)

    assert counts == {"users": 1, "posts": 1}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name FROM users").fetchone() == ("Ada",)
        assert connection.execute("SELECT user_id FROM posts").fetchone() == (1,)
        assert connection.execute("SELECT metadata FROM posts").fetchone() == ('{"featured":true}',)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_store_seeded_data_rejects_invalid_foreign_keys(tmp_path):
    schema = load_schema(DBML, source_format="dbml")
    generated_data = {
        "users": {},
        "posts": {"1": {"id": 1, "user_id": 99, "metadata": None}},
    }

    try:
        store_seeded_data(schema, generated_data, tmp_path / "invalid.sqlite3")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("invalid foreign key was accepted")


def test_store_seeded_data_replaces_existing_database_atomically(tmp_path):
    schema = load_schema(DBML, source_format="dbml")
    database_path = tmp_path / "seeded_data.sqlite3"
    database_path.write_bytes(b"previous database")

    store_seeded_data(
        schema,
        {
            "users": {"1": {"id": 1, "name": "Ada"}},
            "posts": {"1": {"id": 1, "user_id": 1, "metadata": None}},
        },
        database_path,
    )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM posts").fetchone() == (1,)


def test_seed_generates_foreign_keys_for_composite_primary_keys(tmp_path):
    schema_path = tmp_path / "schema.sql"
    config_path = tmp_path / "config.yaml"
    schema_path.write_text(
        """
        CREATE TABLE users (id UUID PRIMARY KEY);
        CREATE TABLE groups (id UUID PRIMARY KEY);
        CREATE TABLE memberships (
            user_id UUID REFERENCES users(id),
            group_id UUID REFERENCES groups(id),
            PRIMARY KEY (user_id, group_id)
        );
        """
    )
    config_path.write_text("seed: 42\nNum_of_entries:\n  default: 3\n")

    from seeder_engine.schema_loader import load_schema_file

    schema = load_schema_file(schema_path, "sql", "postgres")
    generated = seed(schema_path, config_path, None, "sql", "postgres", persist_json=False)
    store_seeded_data(schema, generated, tmp_path / "seeded.sqlite3")

    with sqlite3.connect(tmp_path / "seeded.sqlite3") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
