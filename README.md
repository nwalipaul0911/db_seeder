# DB Seeder

Generate realistic fake JSON data from a [DBML](https://dbml.dbdiagram.io/) schema. The seeder walks tables in foreign-key order, honors unique and composite-unique constraints, fills enums, and writes one JSON file per table.

## Requirements

- Python 3.12+
- A `schema.dbml` file in the project root (this file is gitignored; you supply your own)

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

1. Place your schema at `schema.dbml` (or pass another path with `--schema`).
2. Set how many rows to generate in `config.yaml`.
3. Run the seeder:

```bash
python main.py
```

Rows are written to `data/<table_name>.json`.

### Command-line options

```bash
python main.py --schema schema.dbml --config config.yaml --output data
```

| Flag | Default | Description |
| --- | --- | --- |
| `--schema` | `schema.dbml` | Path to the DBML schema |
| `--config` | `config.yaml` | Path to the YAML row-count config |
| `--output` | `data` | Directory for generated JSON files |

```bash
python main.py --help
```

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

## How data is generated

Tables are topologically sorted from foreign keys so parents exist before children. Many-to-many refs (`<>`) are skipped.

For each column:

| Rule | Behavior |
| --- | --- |
| Primary key | Sequential string IDs (`"1"`, `"2"`, …) |
| Foreign key | Random existing value from the parent table |
| Unique FK | Distinct parent keys until they run out |
| Enum | Random value from the DBML enum |
| Named fields | Faker values when the column name matches types such as `email`, `first_name`, `city`, `phone` |
| Type-based | `int`, `varchar`, `date`, `timestamp`, `bool`, `uuid`, and similar SQL types |
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

After generating data, tests read `schema.dbml` and `data/` (not the CLI flags):

```bash
python main.py --schema schema.dbml --output data
pytest test.py
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
| `main.py` | CLI, parse DBML, sort tables, generate and write JSON |
| `field_types.py` | Column-name and SQL-type generators |
| `datetime_generator.py` | Date and timestamp helpers |
| `config.yaml` | Per-table row counts |
| `test.py` | Constraint checks against `data/` |
| `schema.dbml` | Input schema (not committed) |

## Limitations

- Many-to-many relationships (`<>`) are not seeded.
- Circular foreign keys raise an error.
- Self-referential FKs are not treated as sort dependencies; values still come from already generated rows in the same table when possible.
- Date generators currently default to “now” unless callers pass an explicit range.
- Output is JSON files, not SQL `INSERT` statements.
