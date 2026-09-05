import json

from web_app.app import app


def test_history_lists_completed_runs(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    run_dir = tmp_path / "runs" / "dbml-example" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "users.json").write_text(json.dumps({"1": {"id": "1"}}))
    (run_dir / "generated_data.zip").write_bytes(b"zip")
    (run_dir / "manifest.json").write_text(json.dumps({
        "schema_key": "dbml-example",
        "run_id": "run-1",
        "source_format": "dbml",
        "output_path": "runs/dbml-example/run-1",
        "created_at": "2026-09-05T12:00:00+00:00",
    }))

    response = app.test_client().get("/history")

    assert response.status_code == 200
    assert response.get_json()[0]["files"] == [{"name": "users.json", "row_count": 1}]


def test_history_runs_can_be_deleted_individually_and_in_bulk(tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    for run_id in ("run-1", "run-2"):
        run_dir = tmp_path / "runs" / "dbml-example" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(json.dumps({
            "schema_key": "dbml-example",
            "run_id": run_id,
        }))

    response = app.test_client().delete("/history/dbml-example/run-1")
    assert response.status_code == 200
    assert not (tmp_path / "runs" / "dbml-example" / "run-1").exists()

    response = app.test_client().post("/history/delete", json={
        "runs": [{"schema_key": "dbml-example", "run_id": "run-2"}],
    })
    assert response.status_code == 200
    assert response.get_json()["deleted"] == 1
    assert not (tmp_path / "runs" / "dbml-example" / "run-2").exists()