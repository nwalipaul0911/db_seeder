import os
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename
import yaml

from seeder_engine.main import seed

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

app = Flask(__name__, template_folder=str(PACKAGE_DIR / "templates"))
app.config["UPLOAD_FOLDER"] = os.path.join(PROJECT_DIR, "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def build_runtime_config(rows_per_table: int):
    config = {
        "seed": 42,
        "null_probability": 0.35,
        "default_probability": 0.7,
        "self_fk_root_probability": 0.35,
        "Num_of_entries": {"default": int(rows_per_table)},
    }
    return config


def generate_from_dbml(dbml_text: str, rows_per_table: int, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(build_runtime_config(rows_per_table), f, sort_keys=False)

    schema_path = output_dir / "schema.dbml"
    schema_path.write_text(dbml_text, encoding="utf-8")

    seed(str(schema_path), str(config_path), str(output_dir))
    json_files = sorted(output_dir.glob("*.json"))
    archive_path = output_dir / "generated_data.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for json_file in json_files:
            archive.write(json_file, arcname=json_file.name)
    return [json_file.name for json_file in json_files], archive_path.name


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate_data():
    dbml_text = (request.form.get("dbml_text") or "").strip()
    uploaded_file = request.files.get("dbml_file")
    rows_per_table = request.form.get("rows_per_table", "100")
    output_name = request.form.get("output_name", "generated_data")

    try:
        rows_per_table = max(1, int(rows_per_table))
    except ValueError:
        rows_per_table = 100

    if uploaded_file and uploaded_file.filename:
        dbml_text = uploaded_file.read().decode("utf-8", errors="replace").strip()

    if not dbml_text:
        return jsonify({"error": "DBML text or a DBML file is required."}), 400

    session_dir = Path(app.config["UPLOAD_FOLDER"]) / secure_filename(output_name or f"generated_{uuid4().hex[:8]}")
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        files, archive_name = generate_from_dbml(dbml_text, rows_per_table, session_dir)
    except Exception as exc:
        return jsonify({"error": f"DBML generation failed: {exc}"}), 400

    relative_root = os.path.relpath(session_dir, Path(app.config["UPLOAD_FOLDER"]))
    return jsonify({
        "message": "Data generated successfully",
        "files": files,
        "archive_name": archive_name,
        "output_dir": str(session_dir),
        "output_root": relative_root,
        "row_count": len(files) * rows_per_table,
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
    target_dir = Path(app.config["UPLOAD_FOLDER"]) / "uploaded"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    file.save(target_path)

    return jsonify({
        "message": "DBML file uploaded successfully",
        "filename": filename,
        "path": str(target_path),
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
