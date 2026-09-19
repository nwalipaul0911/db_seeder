import os
import json
import hashlib
import shutil
import os
import re
import sqlite3
import tempfile
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename
import yaml

from seeder_engine.main import seed
from seeder_engine.database import store_seeded_data
from seeder_engine.schema_loader import adapter_for_source, load_schema_file
from seeder_engine.schema_validator import (
    SchemaValidationError,
    validate_schema as validate_input_schema,
)
from seeder_engine.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

# Flask app setup: the UI templates live alongside this module and uploads are kept
# under the project root so generated data remains available for browsing and download.
app = Flask(__name__, template_folder=str(PACKAGE_DIR / "templates"))
configure_logging(PROJECT_DIR / "logs")
load_dotenv()  # Load environment variables from .env file
app.config["UPLOAD_FOLDER"] = os.path.join(PROJECT_DIR, "uploads")
app.config["S3_BUCKET"] = os.environ.get("SEEDER_S3_BUCKET", "")
app.config["S3_PREFIX"] = os.environ.get("SEEDER_S3_PREFIX", "runs").strip("/")
app.config["DEBUG"] = os.environ.get("FLASK_DEBUG", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
    "True",
    "TRUE",
    "ON",
    "enabled",
    "Enabled",
    "ENABLED",
}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

READ_ONLY_SQL_RE = re.compile(r"^(?:SELECT|WITH|EXPLAIN|PRAGMA)\b", re.IGNORECASE)


def schema_identity(schema_text: str, schema_format: str, dialect: str) -> str:
    """Return a stable identifier for a schema so runs can be grouped by content."""
    digest = hashlib.sha256(
        f"{schema_format}:{dialect}:{schema_text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{schema_format}-{digest}"


def schema_filename(schema_format: str) -> str:
    """Map each schema type to its canonical on-disk filename."""
    return {
        "sql": "schema.sql",
        "prisma": "schema.prisma",
    }.get(schema_format, "schema.dbml")


class RunStore:
    """Persist generated runs either on disk or in an S3-compatible bucket."""

    def __init__(self):
        self.bucket = app.config.get("S3_BUCKET", "")
        self.prefix = app.config.get("S3_PREFIX", "runs").strip("/")

    @property
    def uses_s3(self):
        """Return True when all run bundles should be stored in S3."""
        return bool(self.bucket)

    def _client(self):
        """Create an S3 client with optional endpoint override for local testing."""
        from importlib import import_module

        boto3 = import_module("boto3")
        endpoint_url = os.environ.get("AWS_ENDPOINT_URL", "").strip()
        client_options = {"endpoint_url": endpoint_url} if endpoint_url else {}
        return boto3.client("s3", **client_options)

    def _key(self, schema_key, run_id, filename=""):
        """Build the object key used for exporting generated data to storage."""
        parts = [self.prefix, schema_key, run_id]
        if filename:
            parts.append(filename)
        return "/".join(part for part in parts if part)

    def local_root(self):
        """Return the on-disk root directory that contains every run bundle."""
        return Path(app.config["UPLOAD_FOLDER"]) / "runs"

    def local_run(self, schema_key, run_id):
        """Return the local directory for a specific schema/run pair."""
        return self.local_root() / schema_key / run_id

    def upload(self, run_dir, schema_key, run_id):
        """Upload a completed run directory to object storage when S3 is enabled."""
        if not self.uses_s3:
            return
        client = self._client()
        for path in run_dir.rglob("*"):
            if path.is_file():
                client.upload_file(
                    str(path),
                    self.bucket,
                    self._key(schema_key, run_id, str(path.relative_to(run_dir))),
                )

    def list_runs(self):
        """Return a list of tracked runs, regardless of whether storage is local or S3."""
        if not self.uses_s3:
            if not self.local_root().exists():
                return []
            return [
                {
                    "schema_key": path.parent.parent.name,
                    "run_id": path.parent.name,
                    "manifest": json.loads(path.read_text(encoding="utf-8")),
                }
                for path in self.local_root().glob("*/*/manifest.json")
            ]

        client = self._client()
        paginator = client.get_paginator("list_objects_v2")
        runs = {}
        for page in paginator.paginate(Bucket=self.bucket, Prefix=f"{self.prefix}/"):
            for item in page.get("Contents", []):
                parts = item["Key"].split("/")
                if (
                    len(parts) == 4
                    and parts[0] == self.prefix
                    and parts[3] == "manifest.json"
                ):
                    key = (parts[1], parts[2])
                    runs[key] = {
                        "schema_key": parts[1],
                        "run_id": parts[2],
                        "manifest": None,
                    }
        for run in runs.values():
            run["manifest"] = json.loads(
                self.read_file(run["schema_key"], run["run_id"], "manifest.json")
            )
        return list(runs.values())

    def read_file(self, schema_key, run_id, filename):
        """Read a file from the requested run, whether it is stored locally or in S3."""
        if self.uses_s3:
            return (
                self._client()
                .get_object(
                    Bucket=self.bucket, Key=self._key(schema_key, run_id, filename)
                )["Body"]
                .read()
            )
        return self.local_run(schema_key, run_id).joinpath(filename).read_bytes()

    def has_run(self, schema_key, run_id):
        """Return a run manifest if the given schema/run exists and matches metadata."""
        try:
            payload = json.loads(self.read_file(schema_key, run_id, "manifest.json"))
        except (OSError, ValueError, KeyError):
            return None
        if payload.get("schema_key") != schema_key or payload.get("run_id") != run_id:
            return None
        return payload

    def delete(self, schema_key, run_id):
        """Delete an entire run bundle from the configured storage backend."""
        if not self.uses_s3:
            shutil.rmtree(self.local_run(schema_key, run_id))
            return
        client = self._client()
        paginator = client.get_paginator("list_objects_v2")
        keys = [
            {"Key": item["Key"]}
            for page in paginator.paginate(
                Bucket=self.bucket, Prefix=f"{self._key(schema_key, run_id)}/"
            )
            for item in page.get("Contents", [])
        ]
        if keys:
            client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})

    def cleanup(self, current_run=None):
        """Keep the newest runs and delete older ones when the configured cap is exceeded."""
        max_runs = int(os.environ.get("SEEDER_MAX_RUNS", "100"))
        runs = sorted(
            self.list_runs(),
            key=lambda item: item["manifest"].get("created_at", ""),
            reverse=True,
        )
        for item in runs[max_runs:]:
            if current_run and (item["schema_key"], item["run_id"]) == current_run:
                continue
            self.delete(item["schema_key"], item["run_id"])


def run_store():
    return RunStore()


def create_run_directories(schema_text: str, schema_format: str, dialect: str):
    """Create a unique storage directory for a generated dataset and return its IDs."""
    schema_key = schema_identity(schema_text, schema_format, dialect)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    upload_root = Path(app.config["UPLOAD_FOLDER"])
    output_dir = upload_root / "runs" / schema_key / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    return schema_key, run_id, output_dir


def build_runtime_config(rows_per_table: int, overrides=None):
    """Build the runtime seeding config, applying user overrides only when valid."""
    config = {
        "seed": 42,
        "null_probability": 0.35,
        "default_probability": 0.7,
        "self_fk_root_probability": 0.35,
        "Num_of_entries": {"default": int(rows_per_table)},
    }
    if isinstance(overrides, dict):
        for key in (
            "seed",
            "null_probability",
            "default_probability",
            "self_fk_root_probability",
        ):
            if key in overrides:
                config[key] = overrides[key]
        if isinstance(overrides.get("Num_of_entries"), dict):
            config["Num_of_entries"].update(overrides["Num_of_entries"])
        if isinstance(overrides.get("xor_groups"), dict):
            config["xor_groups"] = overrides["xor_groups"]
    return config


def generate_from_schema(
    schema_text: str,
    rows_per_table: int,
    output_dir: Path,
    schema_format: str,
    dialect: str,
    config_overrides=None,
):
    """Generate seeded data for a schema and persist the runtime artifacts in a run folder."""
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_format = adapter_for_source(
        schema_text, schema_format, dialect
    ).source_format
    config_path = output_dir / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            build_runtime_config(rows_per_table, config_overrides), f, sort_keys=False
        )

    # Save the source schema alongside the generated data so the run remains reproducible.
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
    """Delete stale runs while keeping the newest `SEEDER_MAX_RUNS` records."""
    store = run_store()
    current = None
    if current_run is not None:
        current = (current_run.parent.parent.name, current_run.parent.name)
    store.cleanup(current)


@app.route("/")
def index():
    """Serve the main schema upload and generation page."""
    return render_template("index.html")


@app.route("/sql")
def sql_workspace():
    """Serve the SQL explorer page for inspecting seeded run data."""
    return render_template("sql.html")


def sql_database_path(run: str):
    """Resolve a local SQLite database path from a schema_key/run_id pair if it exists."""
    parts = run.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        return None
    if run_store().uses_s3:
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
    """Make old JSON-only runs queryable without changing their original output files."""
    tables = {}
    for json_path in sorted(run_dir.glob("*.json")):
        if json_path.name == "manifest.json":
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(payload, dict)
            and payload
            and all(isinstance(row, dict) for row in payload.values())
        ):
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
                quoted_columns = ", ".join(
                    '"' + name.replace('"', '""') + '" TEXT' for name in columns
                )
                connection.execute(f"CREATE TABLE {quoted_table} ({quoted_columns})")
                placeholders = ", ".join("?" for _ in columns)
                values = [
                    [
                        (
                            json.dumps(row.get(name), separators=(",", ":"))
                            if isinstance(row.get(name), (dict, list))
                            else row.get(name)
                        )
                        for name in columns
                    ]
                    for row in rows
                ]
                connection.executemany(
                    f"INSERT INTO {quoted_table} ({', '.join('"' + name.replace('"', '""') + '"' for name in columns)}) VALUES ({placeholders})",
                    values,
                )
        os.replace(temporary_path, database_path)
    finally:
        # Ensure temporary files do not linger if conversion fails mid-way.
        temporary_path.unlink(missing_ok=True)


@app.route("/api/sql/runs")
def sql_runs():
    """List available seeded runs with their row counts for the SQL explorer."""
    store = run_store()
    runs = []
    for item in store.list_runs():
        schema_key = item["schema_key"]
        run_id = item["run_id"]
        manifest = item["manifest"]
        run_dir = store.local_run(schema_key, run_id)
        row_counts = manifest.get("row_counts", {})
        database_path = (
            None if store.uses_s3 else sql_database_path(f"{schema_key}/{run_id}")
        )
        if not row_counts and database_path is not None:
            try:
                with sqlite3.connect(database_path) as connection:
                    table_names = [
                        row[0]
                        for row in connection.execute(
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
        runs.append(
            {
                "schema_key": manifest.get("schema_key", schema_key),
                "run_id": manifest.get("run_id", run_id),
                "label": f"{manifest.get('source_format', 'schema').upper()} · {manifest.get('run_id', run_id)}",
                "created_at": manifest.get("created_at", ""),
                "row_counts": row_counts,
            }
        )
    runs.sort(key=lambda run: run["created_at"], reverse=True)
    return jsonify(runs)


@app.route("/api/sql/query", methods=["POST"])
def run_sql_query():
    """Execute a read-only SQL query against the requested generated database."""
    payload = request.get_json(silent=True) or {}
    run = payload.get("run")
    query = payload.get("query", "")
    if not isinstance(run, str) or not isinstance(query, str):
        return jsonify({"error": "A database run and SQL query are required."}), 400
    query = query.strip()
    if not query or not READ_ONLY_SQL_RE.match(query):
        return (
            jsonify(
                {
                    "error": "Only read-only SELECT, WITH, EXPLAIN, and PRAGMA queries are allowed."
                }
            ),
            400,
        )
    if query.rstrip().endswith(";"):
        query = query.rstrip()[:-1].rstrip()
    if ";" in query:
        return (
            jsonify({"error": "Only one SQL statement may be executed at a time."}),
            400,
        )
    parts = run.split("/")
    store = run_store()
    database_path = sql_database_path(run)
    temporary_path = None
    if store.uses_s3 and len(parts) == 2 and all(parts):
        try:
            temporary_file = tempfile.NamedTemporaryFile(
                suffix=".sqlite3", delete=False
            )
            temporary_file.write(
                store.read_file(parts[0], parts[1], "seeded_data.sqlite3")
            )
            temporary_file.close()
            temporary_path = Path(temporary_file.name)
            database_path = temporary_path
        except (OSError, KeyError):
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
            database_path = None
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
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
    return jsonify({"columns": columns, "rows": rows, "row_count": len(rows)})


@app.route("/history")
def seed_history():
    """Return the generated run history enriched with file and table row counts."""
    store = run_store()
    history = []
    for item in store.list_runs():
        try:
            manifest = item["manifest"]
            output_dir = store.local_run(item["schema_key"], item["run_id"])
            if manifest.get("row_counts"):
                manifest["files"] = [
                    {"name": table, "row_count": count}
                    for table, count in sorted(manifest["row_counts"].items())
                ]
            else:
                json_files = (
                    []
                    if store.uses_s3
                    else sorted(
                        path
                        for path in output_dir.glob("*.json")
                        if path.name != "manifest.json"
                    )
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
            logger.warning(
                "Skipping unreadable history manifest for %s/%s",
                item["schema_key"],
                item["run_id"],
                exc_info=True,
            )
            continue

    history.sort(key=lambda run: run.get("created_at", ""), reverse=True)
    return jsonify(history)


def history_run_directory(schema_key: str, run_id: str):
    """Validate that a schema/run ID matches a real local history entry."""
    store = run_store()
    if store.uses_s3:
        return store.has_run(schema_key, run_id)
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
    """Delete a single historical run bundle after validating its identity."""
    if history_run_directory(schema_key, run_id) is None:
        logger.warning(
            "History run not found for deletion: schema=%s run=%s", schema_key, run_id
        )
        return jsonify({"error": "History run not found."}), 404
    run_store().delete(schema_key, run_id)
    logger.info("Deleted history run: schema=%s run=%s", schema_key, run_id)
    return jsonify({"message": "History run deleted."})


@app.route("/history/delete", methods=["POST"])
def delete_history_runs():
    """Delete multiple historical runs in one request while guaranteeing valid IDs."""
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
        if history_run_directory(schema_key, run_id) is not None:
            run_store().delete(schema_key, run_id)
            deleted += 1
            logger.info("Deleted history run: schema=%s run=%s", schema_key, run_id)
    return jsonify(
        {"message": f"Deleted {deleted} history run(s).", "deleted": deleted}
    )


@app.route("/validate", methods=["POST"])
def validate_schema():
    """Validate schema content from either inline text or an uploaded file."""
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
    """Generate seeded data from schema input and store the result in a run directory."""
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
        resolved_format = adapter_for_source(
            dbml_text, schema_format, dialect
        ).source_format
        schema_key, run_id, session_dir = create_run_directories(
            dbml_text, resolved_format, dialect
        )
        database_name, row_counts = generate_from_schema(
            dbml_text,
            rows_per_table,
            session_dir,
            resolved_format,
            dialect,
            config_overrides,
        )
        manifest = {
            "schema_key": schema_key,
            "run_id": run_id,
            "source_format": resolved_format,
            "dialect": dialect if resolved_format == "sql" else None,
            "source_path": str(
                (session_dir / schema_filename(resolved_format)).relative_to(
                    Path(app.config["UPLOAD_FOLDER"])
                )
            ),
            "output_path": str(
                session_dir.relative_to(Path(app.config["UPLOAD_FOLDER"]))
            ),
            "output_name": secure_filename(output_name) or "generated_data",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database_name": database_name,
            "row_counts": row_counts,
        }
        (session_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        store = run_store()
        store.upload(session_dir, schema_key, run_id)
        if store.uses_s3:
            shutil.rmtree(session_dir)
        cleanup_history(session_dir)
    except Exception as exc:
        error_id = uuid4().hex[:12]
        logger.exception("Web generation failed (error_id=%s)", error_id)
        return (
            jsonify({"error": "Schema generation failed.", "error_id": error_id}),
            400,
        )

    relative_root = os.path.relpath(session_dir, Path(app.config["UPLOAD_FOLDER"]))
    return jsonify(
        {
            "message": "Data generated successfully",
            "row_counts": row_counts,
            "database_name": database_name,
            "output_dir": str(session_dir),
            "output_root": relative_root,
            "schema_key": schema_key,
            "run_id": run_id,
            "row_count": sum(row_counts.values()),
        }
    )


@app.route("/download/<path:relative_path>")
def download_file(relative_path):
    """Serve a generated file for download, including historical run artifacts."""
    parts = relative_path.split("/")
    if len(parts) == 4 and parts[0] == "runs":
        schema_key, run_id, filename = parts[1], parts[2], "/".join(parts[3:])
        store = run_store()
        try:
            contents = store.read_file(schema_key, run_id, filename)
        except (OSError, KeyError):
            return jsonify({"error": "File not found."}), 404
        if store.uses_s3:
            return send_file(
                BytesIO(contents), download_name=Path(filename).name, as_attachment=True
            )
    root_dir = Path(app.config["UPLOAD_FOLDER"]).resolve()
    target_path = (root_dir / relative_path).resolve()
    if root_dir not in target_path.parents and target_path != root_dir:
        return jsonify({"error": "Invalid file path."}), 400
    if not target_path.exists() or not target_path.is_file():
        return jsonify({"error": "File not found."}), 404
    return send_file(target_path, as_attachment=True)


@app.route("/view/<path:relative_path>")
def view_json(relative_path):
    """Open a JSON artifact in the browser without triggering a download."""
    parts = relative_path.split("/")
    if len(parts) == 4 and parts[0] == "runs":
        schema_key, run_id, filename = parts[1], parts[2], "/".join(parts[3:])
        if not filename.endswith(".json"):
            return jsonify({"error": "Only JSON files can be viewed."}), 400
        store = run_store()
        try:
            contents = store.read_file(schema_key, run_id, filename)
        except (OSError, KeyError):
            return jsonify({"error": "File not found."}), 404
        if store.uses_s3:
            return send_file(
                BytesIO(contents), mimetype="application/json", as_attachment=False
            )
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
    """Accept a schema file upload and return a stable schema identity for the client."""
    file = request.files.get("dbml_file")
    if not file or file.filename == "":
        return jsonify({"error": "No DBML file uploaded."}), 400

    filename = secure_filename(file.filename or "")
    contents = file.read()
    extension = Path(filename).suffix.lower()
    source_format = {".sql": "sql", ".prisma": "prisma", ".dbml": "dbml"}.get(
        extension, "auto"
    )
    schema_key = schema_identity(
        contents.decode("utf-8", errors="replace"), source_format, "sqlite"
    )
    return jsonify(
        {
            "message": "Schema file loaded successfully",
            "filename": filename,
            "schema_key": schema_key,
        }
    )


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"},
        host="0.0.0.0",
        port=5000,
    )
