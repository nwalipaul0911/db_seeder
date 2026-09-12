# DB Seeder

Generate realistic fake JSON data from [DBML](https://dbml.dbdiagram.io/), SQL DDL, or Prisma schemas. The seeder walks tables in foreign-key order, honors unique and composite-unique constraints, and writes one JSON file per table.

## Requirements

- Python 3.12+
- A schema file in DBML, SQL DDL, or Prisma format

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

1. Place your schema at `schema.dbml`, `schema.sql`, or `schema.prisma` (or pass another path with `--schema`).
2. Set how many rows to generate in `config.yaml`.
3. Run the seeder:

```bash
python -m seeder_engine.main
```

Rows are written to `data/<table_name>.json`.

The web app keeps uploaded inputs and generated runs isolated:

```text
uploads/
  schemas/<format-hash>/schema.<format>
  runs/<format-hash>/<run-id>/
    schema.<format>
    manifest.json
    <table>.json
    generated_data.zip
```

The schema key is derived from the schema content, format, and SQL dialect. Each generation receives a unique run ID, so repeated generations never overwrite earlier output, even when they use the same output name.

### Command-line options

```bash
python -m seeder_engine.main --schema schema.dbml --config config.yaml --output data
```

SQL DDL is also supported:

```bash
python -m seeder_engine.main --schema schema.sql --format sql --dialect postgres --config config.yaml --output data
```

Prisma schemas are supported as well:

```bash
python -m seeder_engine.main --schema schema.prisma --format prisma --config config.yaml --output data
```

The Flask UI accepts DBML, SQL DDL, and Prisma schemas. Select the format explicitly when possible. For SQL DDL, select the matching dialect: SQLite, PostgreSQL, or MySQL.

| Flag | Default | Description |
| --- | --- | --- |
| `--schema` | `schema.dbml` | Path to the DBML schema |
| `--config` | `config.yaml` | Path to the YAML row-count config |
| `--output` | `data` | Directory for generated JSON files |

```bash
python -m seeder_engine.main --help
```

The browser UI can be started with:

```bash
python app.py
```

Then open `http://127.0.0.1:5000` to paste or upload DBML and generate data.

## Logging

The CLI, schema adapters, validator, seeder, and Flask web app write logs to the console and to `logs/seeder.log`. The file uses rotation with up to three 5 MB backups. The `logs/` directory is created automatically.

Use environment variables to adjust logging without changing code:

```bash
SEEDER_LOG_LEVEL=DEBUG SEEDER_LOG_FILE=/tmp/db-seeder.log python app.py
```

Supported levels include `DEBUG`, `INFO`, `WARNING`, and `ERROR`. Generation failures include a traceback in the log file while the web UI continues to show a concise error message.

In the web UI:

- Paste a schema or upload a `.dbml`, `.sql`, or `.prisma` file.
- Choose the schema format and SQL dialect, then set the rows per table.
- Use **Validate schema** before generation to check table, enum, and reference counts.
- Use **Generate data** to inspect each generated JSON table or download all tables as a ZIP archive.
- Use **Seed history** to reopen data from previous runs. Each run can be downloaded, its table JSON can be viewed, or the run can be cleared individually.
- Select multiple history entries, or use **Select all**, and choose **Clear checked history** to remove them together.

Each generation receives a unique run ID. History is bounded by `SEEDER_MAX_RUNS` (100 by default) and can also be cleared manually.

## Configuration

`config.yaml` controls row counts:

```yaml
Num_of_entries:
  departments: 5
  employees: 100
  default: 100
```

- Keys match DBML table names.
- Tables not listed use `default` (100 if omitted).
- The web UI accepts optional advanced JSON configuration for per-table counts and `xor_groups`.

## How data is generated

Tables are topologically sorted from foreign keys so parents exist before children. Many-to-many refs (`<>`) are skipped.

For each column:

| Rule | Behavior |
| --- | --- |
| Primary key | Integer keys use row IDs; UUID keys use generated UUID4 values |
| Foreign key | Random existing value from the parent table |
| Unique FK | Distinct parent keys until they run out |
| Enum | Random value from the schema enum when the adapter exposes its values |
| Named fields | Faker values when the column name matches types such as `email`, `first_name`, `city`, `phone` |
| Type-based | `int`, `varchar`, `date`, `timestamp`, `bool`, `uuid`, and similar SQL types |
| Native values | JSON/JSONB values and SQL array values are emitted as JSON-compatible objects/lists; supported SQL defaults are evaluated |
| Generated columns | Computed columns are omitted; identity/autoincrement keys remain available for relational generation |
| Unique columns | Retried until a unused value is found |
| Composite unique indexes | Whole row retried (up to 100 attempts) until the combination is unique |

If a unique combination cannot be found after 100 attempts, a warning is printed and that row is skipped.

## Output format

Each file is a JSON object keyed by row ID:

```json
{
  "1": {
    "id": "1",
    "name": "Engineering",
    "created_at": "2026-08-20T12:00:00"
  }
}
```

## Tests

After generating data, the legacy data tests read `schema.dbml` and `data/` (not the CLI flags):

```bash
python -m seeder_engine.main --schema schema.dbml --output data
python -m pytest test.py
```

Run the complete test suite with the project environment:

```bash
source venv/bin/activate
python -m pytest
```

Tests check:

- NOT NULL and UNIQUE constraints
- Enum membership
- Foreign-key referential integrity
- Composite unique indexes
- Parent tables not fully unused
- Simple created/updated chronology
- Primitive type shapes (int, bool, numeric)

## Project layout

| File | Role |
| --- | --- |
| `seeder_engine/` | Packaged DBML parsing and data generation engine |
| `seeder_engine/main.py` | Parse DBML, sort tables, generate and write JSON |
| `seeder_engine/field_types.py` | Column-name and SQL-type generators |
| `seeder_engine/datetime_generator.py` | Date and timestamp helpers |
| `seeder_engine/schema_ir.py` | Canonical schema model and adapter protocol |
| `seeder_engine/dbml_adapter.py` | DBML-to-Schema IR adapter |
| `seeder_engine/sql_adapter.py` | SQL DDL-to-Schema IR adapter |
| `seeder_engine/prisma_adapter.py` | Prisma-to-Schema IR adapter |
| `seeder_engine/schema_loader.py` | Schema format detection and adapter selection |
| `seeder_engine/schema_validator.py` | DBML validation and schema inspection |
| `seeder_engine/config.yaml` | Default package configuration |
| `web_app/` | Separate Flask UI package and templates |
| `app.py` | Root launcher for the Flask UI |
| `config.yaml` | Project-level CLI configuration |
| `test.py` | Constraint checks against `data/` |
| `schema.dbml` | Input schema (not committed) |

## Limitations

- Many-to-many relationships (`<>`) are not seeded.
- Circular foreign keys raise an error.
- Self-referential FKs are not treated as sort dependencies; values still come from already generated rows in the same table when possible.
- SQL DDL is parsed into the common schema model; some dialect-specific semantics, including inline MySQL enum values, SQL check enforcement, and function defaults, may not be preserved in generated JSON.
- SQL entered in the web UI should begin with a `CREATE TABLE` statement for auto-detection. For scripts containing `CREATE DATABASE` or `USE`, select **SQL DDL** and the correct dialect explicitly.
- Date generators currently default to “now” unless callers pass an explicit range.
- Output is JSON files, not SQL `INSERT` statements.
