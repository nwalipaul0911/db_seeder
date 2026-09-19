import json
import sqlite3

from web_app.app import app, generate_from_schema


DBML = """
Table users {
    id int [pk]
}

Table posts {
    id int [pk]
    user_id int
}

Ref: posts.user_id > users.id
"""


def test_web_generation_persists_sqlite_database(tmp_path):
    database_name, row_counts = generate_from_schema(
        DBML, 2, tmp_path, "dbml", "sqlite"
    )

    assert database_name == "seeded_data.sqlite3"
    assert row_counts == {"users": 2, "posts": 2}
    assert not list(tmp_path.glob("*.json"))
    with sqlite3.connect(tmp_path / database_name) as connection:
        assert connection.execute("SELECT COUNT(*) FROM posts").fetchone() == (2,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_generate_endpoint_returns_database_metadata(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)

    response = app.test_client().post(
        "/generate",
        data={
            "dbml_text": DBML,
            "schema_format": "dbml",
            "rows_per_table": "2",
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["database_name"] == "seeded_data.sqlite3"
    assert payload["row_count"] == 4
    assert payload["row_counts"] == {"users": 2, "posts": 2}
    assert list(tmp_path.glob("schemas/**")) == []
    assert list(tmp_path.glob("runs/*/*/schema.dbml"))


def test_history_lists_completed_runs(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    run_dir = tmp_path / "runs" / "dbml-example" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "users.json").write_text(json.dumps({"1": {"id": "1"}}))
    (run_dir / "generated_data.zip").write_bytes(b"zip")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_key": "dbml-example",
                "run_id": "run-1",
                "source_format": "dbml",
                "output_path": "runs/dbml-example/run-1",
                "created_at": "2026-09-05T12:00:00+00:00",
            }
        )
    )

    response = app.test_client().get("/history")

    assert response.status_code == 200
    assert response.get_json()[0]["files"] == [{"name": "users.json", "row_count": 1}]


def test_s3_client_omits_empty_endpoint_url(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "")

    class FakeBoto3:
        def client(self, service_name, **kwargs):
            assert service_name == "s3"
            assert "endpoint_url" not in kwargs
            return object()

    import sys

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    from web_app.app import RunStore

    assert RunStore()._client() is not None


def test_history_runs_can_be_deleted_individually_and_in_bulk(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    for run_id in ("run-1", "run-2"):
        run_dir = tmp_path / "runs" / "dbml-example" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_key": "dbml-example",
                    "run_id": run_id,
                }
            )
        )

    response = app.test_client().delete("/history/dbml-example/run-1")
    assert response.status_code == 200
    assert not (tmp_path / "runs" / "dbml-example" / "run-1").exists()

    response = app.test_client().post(
        "/history/delete",
        json={
            "runs": [{"schema_key": "dbml-example", "run_id": "run-2"}],
        },
    )
    assert response.status_code == 200
    assert response.get_json()["deleted"] == 1
    assert not (tmp_path / "runs" / "dbml-example" / "run-2").exists()


def test_sql_workspace_lists_and_queries_generated_database(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    database_name, _ = generate_from_schema(
        DBML, 2, tmp_path / "runs" / "dbml-example" / "run-1", "dbml", "sqlite"
    )
    run_dir = tmp_path / "runs" / "dbml-example" / "run-1"
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_key": "dbml-example",
                "run_id": "run-1",
                "source_format": "dbml",
                "created_at": "2026-09-12T12:00:00+00:00",
                "row_counts": {"users": 2, "posts": 2},
                "database_name": database_name,
            }
        )
    )

    runs = app.test_client().get("/api/sql/runs")
    assert runs.status_code == 200
    assert runs.get_json()[0]["schema_key"] == "dbml-example"

    response = app.test_client().post(
        "/api/sql/query",
        json={
            "run": "dbml-example/run-1",
            "query": "SELECT COUNT(*) AS total FROM posts;",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["rows"] == [{"total": 2}]


def test_sql_query_returns_all_rows_for_scrollable_output(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    database_name, _ = generate_from_schema(
        DBML, 12, tmp_path / "runs" / "dbml-example" / "run-1", "dbml", "sqlite"
    )
    run_dir = tmp_path / "runs" / "dbml-example" / "run-1"
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_key": "dbml-example",
                "run_id": "run-1",
                "source_format": "dbml",
                "created_at": "2026-09-12T12:00:00+00:00",
                "database_name": database_name,
            }
        )
    )

    response = app.test_client().post(
        "/api/sql/query",
        json={
            "run": "dbml-example/run-1",
            "query": "SELECT id FROM users ORDER BY id",
        },
    )
    assert response.status_code == 200
    assert len(response.get_json()["rows"]) == 12


def test_sql_workspace_rejects_writes_and_invalid_runs(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    client = app.test_client()

    response = client.post(
        "/api/sql/query",
        json={
            "run": "../outside",
            "query": "DELETE FROM users",
        },
    )
    assert response.status_code == 400

    response = client.post(
        "/api/sql/query",
        json={
            "run": "missing/run",
            "query": "SELECT 1",
        },
    )
    assert response.status_code == 404


def test_sql_workspace_materializes_legacy_json_run(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    run_dir = tmp_path / "runs" / "dbml-legacy" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "users.json").write_text(json.dumps({"1": {"id": 1, "name": "Ada"}}))
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_key": "dbml-legacy",
                "run_id": "run-1",
                "source_format": "dbml",
                "created_at": "2026-09-12T12:00:00+00:00",
            }
        )
    )

    response = app.test_client().post(
        "/api/sql/query",
        json={
            "run": "dbml-legacy/run-1",
            "query": "SELECT name FROM users",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["rows"] == [{"name": "Ada"}]
    assert (run_dir / "seeded_data.sqlite3").is_file()

    runs = app.test_client().get("/api/sql/runs").get_json()
    assert runs[0]["row_counts"] == {"users": 1}
