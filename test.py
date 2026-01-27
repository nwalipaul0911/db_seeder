import os
import json
import pytest
import glob
from pydbml import PyDBML
from main import build_fk_map
from datetime import datetime

# Configuration
DATA_DIR = "data"
DBML_FILE = "schema.dbml"

# ===================== FIXTURES =====================


@pytest.fixture(scope="session")
def dbml_obj():
    """Parses the DBML once for the session."""
    with open(DBML_FILE, "r") as f:
        return PyDBML.parse_file(f)


@pytest.fixture(scope="session")
def fk_map(dbml_obj):
    return build_fk_map(dbml_obj.refs)


@pytest.fixture(scope="session")
def all_data():
    data = {}
    files = glob.glob(f"{DATA_DIR}/*.json")
    for file_path in files:
        table_name = os.path.basename(file_path).replace(".json", "")
        with open(file_path, "r") as f:
            data[table_name] = json.load(f)
    return data


# ===================== TEST CASES =====================


def test_schema_constraints(all_data, dbml_obj):
    """
    Validates NOT NULL and UNIQUE constraints as defined in the DBML.
    """
    for table in dbml_obj.tables:
        t_name = table.name
        assert t_name in all_data, f"Table {t_name} missing from data directory."

        table_rows = all_data[t_name]

        for col in table.columns:
            col_name = col.name

            # 1. Test NOT NULL Constraint
            if col.not_null or col.pk:
                for r_id, row in table_rows.items():
                    val = row.get(col_name)
                    assert (
                        val is not None
                    ), f"NOT NULL violation: {t_name}.{col_name} is null at ID {r_id}"

            # 2. Test UNIQUE Constraint
            if col.unique or col.pk:
                values = [
                    row.get(col_name)
                    for row in table_rows.values()
                    if row.get(col_name) is not None
                ]
                assert len(values) == len(
                    set(values)
                ), f"UNIQUE violation: {t_name}.{col_name} has duplicate values."


def test_enum_adherence(all_data, dbml_obj):
    """
    Ensures values in Enum columns match the allowed values in DBML Enums.
    """
    # Map enum names to their allowed values
    enum_map = {e.name: [i.name for i in e.items] for e in dbml_obj.enums}

    for table in dbml_obj.tables:
        t_name = table.name
        for col in table.columns:
            # Check if the column type is a defined Enum
            from pydbml.classes import Enum

            if isinstance(col.type, Enum):
                allowed_values = enum_map.get(col.type.name, [])
                for r_id, row in all_data[t_name].items():
                    val = row.get(col.name)
                    if val is not None:
                        assert val in allowed_values, (
                            f"Enum Violation: {t_name}.{col.name} has value '{val}', "
                            f"but allowed values are {allowed_values}"
                        )


def test_strict_referential_integrity(all_data, fk_map):
    """Validates Foreign Keys using the official fk_map."""
    for src_table, columns in fk_map.items():
        for col_name, meta in columns.items():
            target_table = meta["target_table"]
            valid_pks = set(all_data[target_table].keys())

            for row_id, row_data in all_data[src_table].items():
                val = row_data.get(col_name)
                if val is None:
                    continue
                assert (
                    str(val) in valid_pks
                ), f"FK Integrity Error: {src_table}[{row_id}] points to {target_table}.{val}, which doesn't exist."


def test_orphan_parent_records(all_data, fk_map):
    """Ensures parent records are utilized (No 0% utilization)."""
    parent_to_children = {}
    for src_table, columns in fk_map.items():
        for col_name, meta in columns.items():
            target = meta["target_table"]
            parent_to_children.setdefault(target, []).append((src_table, col_name))

    for parent_table, children in parent_to_children.items():
        if parent_table not in all_data:
            continue

        parent_ids = set(all_data[parent_table].keys())
        referenced_ids = set()

        for child_table, fk_col in children:
            if child_table in all_data:
                for row in all_data[child_table].values():
                    val = str(row.get(fk_col))
                    if val in parent_ids:
                        referenced_ids.add(val)

        utilization = (len(referenced_ids) / len(parent_ids)) * 100 if parent_ids else 0
        assert (
            utilization > 0
        ), f"Table '{parent_table}' is fully orphaned. No child records point to it."


def test_composite_indexes(all_data, dbml_obj):
    """
    Finds all unique indexes in the DBML (composite or single)
    and verifies the generated data respects them.
    """
    for table in dbml_obj.tables:
        t_name = table.name
        if t_name not in all_data or not table.indexes:
            continue

        for index in table.indexes:
            if index.unique:
                # In PyDBML, columns/elements are referred to as 'subjects'
                # We check if the subject is a Column object with a 'name' attribute
                idx_cols = [
                    s.name for s in getattr(index, "subjects", []) if hasattr(s, "name")
                ]

                # If 'subjects' was empty or didn't exist, skip to prevent () duplicates
                if not idx_cols:
                    continue

                seen = set()
                table_rows = all_data[t_name]

                for r_id, row in table_rows.items():
                    # Create a unique tuple of values for the indexed columns
                    combo = tuple(row.get(c) for c in idx_cols)

                    assert combo not in seen, (
                        f"Composite Unique Violation in {t_name}: "
                        f"The combination {dict(zip(idx_cols, combo))} is duplicated. "
                        f"Check row ID {r_id}"
                    )
                    seen.add(combo)


def test_generic_chronology(all_data):
    """
    For any table with standard timestamp naming conventions,
    ensures the timeline of the record is logical.
    """
    timestamp_pairs = [
        ("created_at", "updated_at"),
        ("created_at", "last_seen"),
        ("installed_at", "updated_at"),
    ]

    for table_name, rows in all_data.items():
        for r_id, row in rows.items():
            for start_col, end_col in timestamp_pairs:
                start_val = row.get(start_col)
                end_val = row.get(end_col)

                if start_val and end_val:
                    # Generic ISO string comparison or datetime conversion
                    start_dt = datetime.fromisoformat(str(start_val).replace("Z", ""))
                    end_dt = datetime.fromisoformat(str(end_val).replace("Z", ""))

                    assert end_dt >= start_dt, (
                        f"Chronology Error in {table_name}[{r_id}]: "
                        f"{end_col} ({end_val}) is earlier than {start_col} ({start_val})"
                    )


def test_data_type_adherence(all_data, dbml_obj):
    """
    Ensures JSON values match the expected primitive types from DBML.
    """
    for table in dbml_obj.tables:
        t_name = table.name
        for col in table.columns:
            col_type = (
                col.type.lower() if isinstance(col.type, str) else str(col.type).lower()
            )

            for r_id, row in all_data[t_name].items():
                val = row.get(col.name)
                if val is None:
                    continue

                if col_type in ["int", "integer", "serial"]:
                    assert str(val).isdigit() or isinstance(
                        val, int
                    ), f"Type mismatch: {t_name}.{col.name} expected int"
                elif col_type in ["bool", "boolean"]:
                    assert isinstance(val, bool) or str(val).lower() in [
                        "true",
                        "false",
                    ], f"Type mismatch: {t_name}.{col.name} expected bool"
                elif col_type in ["float", "decimal", "double"]:
                    try:
                        float(val)
                    except ValueError:
                        pytest.fail(
                            f"Type mismatch: {t_name}.{col.name} expected numeric"
                        )
