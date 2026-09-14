import os
import json
import hashlib
import shutil
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename
import yaml

from seeder_engine.main import seed
from seeder_engine.database import store_seeded_data
from seeder_engine.schema_loader import adapter_for_source
from seeder_engine.schema_loader import load_schema_file
from seeder_engine.schema_validator import (
    SchemaValidationError,
    validate_schema as validate_input_schema,
)
from seeder_engine.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

app = Flask(__name__, template_folder=str(PACKAGE_DIR / "templates"))
configure_logging(PROJECT_DIR / "logs")
app.config["UPLOAD_FOLDER"] = os.path.join(PROJECT_DIR, "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

READ_ONLY_SQL_RE = re.compile(r"^(?:SELECT|WITH|EXPLAIN|PRAGMA)\b", re.IGNORECASE)


def schema_identity(schema_text: str, schema_format: str, dialect: str) -> str:
    digest = hashlib.sha256(
        f"{schema_format}:{dialect}:{schema_text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{schema_format}-{digest}"


def schema_filename(schema_format: str) -> str:
    return {
        "sql": "schema.sql",
        "prisma": "schema.prisma",
    }.get(schema_format, "schema.dbml")


def create_run_directories(schema_text: str, schema_format: str, dialect: str):
    schema_key = schema_identity(schema_text, schema_format, dialect)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    upload_root = Path(app.config["UPLOAD_FOLDER"])
    schema_dir = upload_root / "schemas" / schema_key
    output_dir = upload_root / "runs" / schema_key / run_id
    schema_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=False)
    return schema_key, run_id, schema_dir, output_dir


def build_runtime_config(rows_per_table: int, overrides=None):
    config = {
        "seed": 42,
        "null_probability": 0.35,
        "default_probability": 0.7,
        "self_fk_root_probability": 0.35,
        "Num_of_entries": {"default": int(rows_per_table)},
    }
    if isinstance(overrides, dict):
        for key in ("seed", "null_probability", "default_probability", "self_fk_root_probability"):
            if key in overrides:
                config[key] = overrides[key]
        if isinstance(overrides.get("Num_of_entries"), dict):
            config["Num_of_entries"].update(overrides["Num_of_entries"])
        if isinstance(overrides.get("xor_groups"), dict):
            config["xor_groups"] = overrides["xor_groups"]
    return config


def generate_from_schema(schema_text: str, rows_per_table: int, output_dir: Path, schema_format: str, dialect: str, config_overrides=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_format = adapter_for_source(schema_text, schema_format, dialect).source_format
    config_path = output_dir / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(build_runtime_config(rows_per_table, config_overrides), f, sort_keys=False)

    schema_path = output_dir / schema_filename(resolved_format)
    schema_path.write_text(schema_text, encoding="utf-8")

    generated_data = seed(
        str(schema_path),
        str(config_path),
        None,
        resolved_format,
        dialect,
        persist_json=False,
    )
    schema = load_schema_file(schema_path, resolved_format, dialect)
    database_name = "seeded_data.sqlite3"
    database_path = output_dir / database_name
    row_counts = store_seeded_data(schema, generated_data, database_path)
    return database_name, row_counts


def cleanup_history(current_run=None):
    runs_root = Path(app.config["UPLOAD_FOLDER"]) / "runs"
    if not runs_root.exists():
        return
    max_runs = int(os.environ.get("SEEDER_MAX_RUNS", "100"))
    manifests = sorted(
        runs_root.glob("*/*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for manifest_path in manifests[max_runs:]:
        run_dir = manifest_path.parent
        if current_run is not None and run_dir == current_run:
            continue
        shutil.rmtree(run_dir, ignore_errors=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sql")
def sql_workspace():
    return render_template("sql.html")


def sql_database_path(run: str):
    parts = run.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        return None
    runs_root = (Path(app.config["UPLOAD_FOLDER"]) / "runs").resolve()
    run_dir = (runs_root / parts[0] / parts[1]).resolve()
    if runs_root not in run_dir.parents:
        return None
    database_path = run_dir / "seeded_data.sqlite3"
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if manifest.get("schema_key") != parts[0] or manifest.get("run_id") != parts[1]:
        return None
    if not database_path.is_file():
        materialize_legacy_json(run_dir, database_path)
    if not database_path.is_file():
        return None
    return database_path


def materialize_legacy_json(run_dir: Path, database_path: Path):
    """Make old JSON-only runs queryable without changing their source files."""
    tables = {}
    for json_path in sorted(run_dir.glob("*.json")):
        if json_path.name == "manifest.json":
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload and all(isinstance(row, dict) for row in payload.values()):
            tables[json_path.stem] = list(payload.values())
    if not tables:
        return
    temporary_path = database_path.with_name(f".{database_path.name}.legacy.tmp")
    try:
        with sqlite3.connect(temporary_path) as connection:
            for table_name, rows in tables.items():
                columns = sorted({name for row in rows for name in row})
                if not columns:
                    continue
                quoted_table = '"' + table_name.replace('"', '""') + '"'
                quoted_columns = ", ".join('"' + name.replace('"', '""') + '" TEXT' for name in columns)
                connection.execute(f"CREATE TABLE {quoted_table} ({quoted_columns})")
                placeholders = ", ".join("?" for _ in columns)
                values = [
                    [json.dumps(row.get(name), separators=(",", ":")) if isinstance(row.get(name), (dict, list)) else row.get(name) for name in columns]
                    for row in rows
                ]
                connection.executemany(
                    f"INSERT INTO {quoted_table} ({', '.join('"' + name.replace('"', '""') + '"' for name in columns)}) VALUES ({placeholders})",
                    values,
                )
        os.replace(temporary_path, database_path)
    finally:
        temporary_path.unlink(missing_ok=True)


@app.route("/api/sql/runs")
def sql_runs():
    runs_root = Path(app.config["UPLOAD_FOLDER"]) / "runs"
    runs = []
    for manifest_path in runs_root.glob("*/*/manifest.json"):
        run_dir = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        database_path = sql_database_path(f"{run_dir.parent.name}/{run_dir.name}")
        if database_path is None:
            continue
        row_counts = manifest.get("row_counts", {})
        if not row_counts:
            try:
                with sqlite3.connect(database_path) as connection:
                    table_names = [
                        row[0] for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                        )
                    ]
                    row_counts = {
                        table: connection.execute(
                            f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                        ).fetchone()[0]
                        for table in table_names
                    }
            except sqlite3.Error:
                continue
        runs.append({
            "schema_key": manifest.get("schema_key", run_dir.parent.name),
            "run_id": manifest.get("run_id", run_dir.name),
            "label": f"{manifest.get('source_format', 'schema').upper()} · {manifest.get('run_id', run_dir.name)}",
            "created_at": manifest.get("created_at", ""),
            "row_counts": row_counts,
        })
    runs.sort(key=lambda run: run["created_at"], reverse=True)
    return jsonify(runs)


@app.route("/api/sql/query", methods=["POST"])
def run_sql_query():
    payload = request.get_json(silent=True) or {}
    run = payload.get("run")
    query = payload.get("query", "")
    if not isinstance(run, str) or not isinstance(query, str):
        return jsonify({"error": "A database run and SQL query are required."}), 400
    query = query.strip()
    if not query or not READ_ONLY_SQL_RE.match(query):
        return jsonify({"error": "Only read-only SELECT, WITH, EXPLAIN, and PRAGMA queries are allowed."}), 400
    if query.rstrip().endswith(";"):
        query = query.rstrip()[:-1].rstrip()
    if ";" in query:
        return jsonify({"error": "Only one SQL statement may be executed at a time."}), 400
    database_path = sql_database_path(run)
    if database_path is None:
        return jsonify({"error": "Database run not found."}), 404
    try:
        uri = f"file:{database_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(query)
            columns = [description[0] for description in cursor.description or ()]
            rows = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"columns": columns, "rows": rows, "row_count": len(rows)})


@app.route("/history")
def seed_history():
    runs_root = Path(app.config["UPLOAD_FOLDER"]) / "runs"
    history = []
    if not runs_root.exists():
        return jsonify(history)

    for manifest_path in runs_root.glob("*/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            output_dir = manifest_path.parent
            if manifest.get("row_counts"):
                manifest["files"] = [
                    {"name": table, "row_count": count}
                    for table, count in sorted(manifest["row_counts"].items())
                ]
            else:
                json_files = sorted(
                    path for path in output_dir.glob("*.json") if path.name != "manifest.json"
                )
                manifest["files"] = [
                    {
                        "name": path.name,
                        "row_count": len(json.loads(path.read_text(encoding="utf-8"))),
                    }
                    for path in json_files
                ]
            if "database_name" not in manifest:
                database_path = output_dir / "seeded_data.sqlite3"
                if database_path.is_file():
                    manifest["database_name"] = database_path.name
            history.append(manifest)
        except (OSError, ValueError, TypeError):
            logger.warning("Skipping unreadable history manifest %s", manifest_path, exc_info=True)
            continue

    history.sort(key=lambda run: run.get("created_at", ""), reverse=True)
    return jsonify(history)


def history_run_directory(schema_key: str, run_id: str):
    runs_root = (Path(app.config["UPLOAD_FOLDER"]) / "runs").resolve()
    run_dir = (runs_root / schema_key / run_id).resolve()
    if runs_root not in run_dir.parents:
        return None
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if manifest.get("schema_key") != schema_key or manifest.get("run_id") != run_id:
        return None
    return run_dir


@app.route("/history/<schema_key>/<run_id>", methods=["DELETE"])
def delete_history_run(schema_key, run_id):
    run_dir = history_run_directory(schema_key, run_id)
    if run_dir is None:
        logger.warning("History run not found for deletion: schema=%s run=%s", schema_key, run_id)
        return jsonify({"error": "History run not found."}), 404
    shutil.rmtree(run_dir)
    logger.info("Deleted history run: schema=%s run=%s", schema_key, run_id)
    return jsonify({"message": "History run deleted."})


@app.route("/history/delete", methods=["POST"])
def delete_history_runs():
    payload = request.get_json(silent=True) or {}
    runs = payload.get("runs", [])
    if not isinstance(runs, list) or not runs:
        return jsonify({"error": "At least one history run is required."}), 400

    deleted = 0
    for item in runs:
        if not isinstance(item, dict):
            continue
        schema_key = item.get("schema_key", "")
        run_id = item.get("run_id", "")
        if not isinstance(schema_key, str) or not isinstance(run_id, str):
            continue
        run_dir = history_run_directory(schema_key, run_id)
        if run_dir is not None:
            shutil.rmtree(run_dir)
            deleted += 1
            logger.info("Deleted history run: schema=%s run=%s", schema_key, run_id)
    return jsonify({"message": f"Deleted {deleted} history run(s).", "deleted": deleted})


@app.route("/validate", methods=["POST"])
def validate_schema():
    schema_text = (request.form.get("dbml_text") or "").strip()
    source_format = request.form.get("schema_format", "auto")
    dialect = request.form.get("dialect", "sqlite")
    uploaded_file = request.files.get("dbml_file")
    if uploaded_file and uploaded_file.filename:
        schema_text = uploaded_file.read().decode("utf-8", errors="replace").strip()

    try:
        return jsonify(validate_input_schema(schema_text, source_format, dialect))
    except SchemaValidationError as exc:
        logger.warning("Schema validation rejected request: %s", exc)
        return jsonify({"valid": False, "error": str(exc)}), 400


@app.route("/generate", methods=["POST"])
def generate_data():
    dbml_text = (request.form.get("dbml_text") or "").strip()
    schema_format = request.form.get("schema_format", "auto")
    dialect = request.form.get("dialect", "sqlite")
    uploaded_file = request.files.get("dbml_file")
    rows_per_table = request.form.get("rows_per_table", "100")
    output_name = request.form.get("output_name", "generated_data")
    config_overrides = {}
    config_text = (request.form.get("config_json") or "").strip()
    if config_text:
        try:
            config_overrides = json.loads(config_text)
            if not isinstance(config_overrides, dict):
                raise ValueError("Configuration must be a JSON object.")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"error": f"Invalid advanced configuration: {exc}"}), 400

    try:
        rows_per_table = max(1, int(rows_per_table))
    except ValueError:
        rows_per_table = 100

    if uploaded_file and uploaded_file.filename:
        dbml_text = uploaded_file.read().decode("utf-8", errors="replace").strip()

    if not dbml_text:
        return jsonify({"error": "DBML text or a DBML file is required."}), 400

    try:
        validate_input_schema(dbml_text, schema_format, dialect)
    except SchemaValidationError as exc:
        logger.warning("Generation validation rejected request: %s", exc)
        return jsonify({"error": str(exc)}), 400

    try:
        resolved_format = adapter_for_source(dbml_text, schema_format, dialect).source_format
        schema_key, run_id, schema_dir, session_dir = create_run_directories(
            dbml_text, resolved_format, dialect
        )
        source_path = schema_dir / schema_filename(resolved_format)
        source_path.write_text(dbml_text, encoding="utf-8")
        database_name, row_counts = generate_from_schema(
            dbml_text, rows_per_table, session_dir, resolved_format, dialect, config_overrides
        )
        manifest = {
            "schema_key": schema_key,
            "run_id": run_id,
            "source_format": resolved_format,
            "dialect": dialect if resolved_format == "sql" else None,
            "source_path": str(source_path.relative_to(Path(app.config["UPLOAD_FOLDER"]))),
            "output_path": str(session_dir.relative_to(Path(app.config["UPLOAD_FOLDER"]))),
            "output_name": secure_filename(output_name) or "generated_data",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database_name": database_name,
            "row_counts": row_counts,
        }
        (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        cleanup_history(session_dir)
    except Exception as exc:
        error_id = uuid4().hex[:12]
        logger.exception("Web generation failed (error_id=%s)", error_id)
        return jsonify({"error": "Schema generation failed.", "error_id": error_id}), 400

    relative_root = os.path.relpath(session_dir, Path(app.config["UPLOAD_FOLDER"]))
    return jsonify({
        "message": "Data generated successfully",
        "row_counts": row_counts,
        "database_name": database_name,
        "output_dir": str(session_dir),
        "output_root": relative_root,
        "schema_key": schema_key,
        "run_id": run_id,
        "row_count": sum(row_counts.values()),
    })


@app.route("/download/<path:relative_path>")
def download_file(relative_path):
    root_dir = Path(app.config["UPLOAD_FOLDER"]).resolve()
    target_path = (root_dir / relative_path).resolve()
    if root_dir not in target_path.parents and target_path != root_dir:
        return jsonify({"error": "Invalid file path."}), 400
    if not target_path.exists() or not target_path.is_file():
        return jsonify({"error": "File not found."}), 404
    return send_file(target_path, as_attachment=True)


@app.route("/view/<path:relative_path>")
def view_json(relative_path):
    root_dir = Path(app.config["UPLOAD_FOLDER"]).resolve()
    target_path = (root_dir / relative_path).resolve()
    if root_dir not in target_path.parents and target_path != root_dir:
        return jsonify({"error": "Invalid file path."}), 400
    if target_path.suffix.lower() != ".json":
        return jsonify({"error": "Only JSON files can be viewed."}), 400
    if not target_path.exists() or not target_path.is_file():
        return jsonify({"error": "File not found."}), 404
    return send_file(target_path, mimetype="application/json", as_attachment=False)


@app.route("/upload", methods=["POST"])
def upload_dbml():
    file = request.files.get("dbml_file")
    if not file or file.filename == "":
        return jsonify({"error": "No DBML file uploaded."}), 400

    filename = secure_filename(file.filename)
    contents = file.read()
    extension = Path(filename).suffix.lower()
    source_format = {".sql": "sql", ".prisma": "prisma", ".dbml": "dbml"}.get(extension, "auto")
    schema_key = schema_identity(contents.decode("utf-8", errors="replace"), source_format, "sqlite")
    target_dir = Path(app.config["UPLOAD_FOLDER"]) / "schemas" / schema_key
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    target_path.write_bytes(contents)

    return jsonify({
        "message": "DBML file uploaded successfully",
        "filename": filename,
        "path": str(target_path),
    })


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"},
        host="0.0.0.0",
        port=5000,
    )
