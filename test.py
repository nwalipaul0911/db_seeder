import os
import json
import pytest
import glob
from pydbml import PyDBML
from pydbml.classes import Enum
from seeder_engine.main import (
    CURRENT_VERSION_RE,
    build_fk_map,
    entity_pairs,
    is_sequence_version,
    parse_xor_from_note,
)
from seeder_engine.field_types import parse_sql_type
from datetime import datetime
import re

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
            target_col = meta["target_column"]
            valid_vals = {
                str(row[target_col])
                for row in all_data[target_table].values()
                if row.get(target_col) is not None
            }

            for row_id, row_data in all_data[src_table].items():
                val = row_data.get(col_name)
                if val is None:
                    continue
                assert str(val) in valid_vals, (
                    f"FK Integrity Error: {src_table}[{row_id}] points to "
                    f"{target_table}.{target_col}={val}, which doesn't exist."
                )


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
        ("started_at", "completed_at"),
        ("initiated_at", "completed_at"),
        ("opened_at", "closed_at"),
        ("effective_date", "expiration_date"),
        ("effective_date", "cancellation_date"),
        ("detected_at", "resolved_at"),
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
            raw = col.type if isinstance(col.type, str) else str(col.type)
            col_type, _ = parse_sql_type(str(raw))

            for r_id, row in all_data[t_name].items():
                val = row.get(col.name)
                if val is None:
                    continue

                if col_type in ["int", "integer", "serial", "bigint", "smallint"]:
                    assert str(val).lstrip("-").isdigit() or isinstance(
                        val, int
                    ), f"Type mismatch: {t_name}.{col.name} expected int, got {val!r}"
                elif col_type in ["bool", "boolean"]:
                    assert isinstance(val, bool) or str(val).lower() in [
                        "true",
                        "false",
                    ], f"Type mismatch: {t_name}.{col.name} expected bool"
                elif col_type in ["float", "decimal", "double", "numeric", "money"]:
                    try:
                        float(val)
                    except (TypeError, ValueError):
                        pytest.fail(
                            f"Type mismatch: {t_name}.{col.name} expected numeric, got {val!r}"
                        )


def test_self_referential_fks_form_a_tree(all_data, fk_map, dbml_obj):
    """Nullable self-FKs should have both roots and children when the table is large."""
    not_null_by_table = {
        t.name: {c.name: c.not_null or c.pk for c in t.columns} for t in dbml_obj.tables
    }
    for src_table, columns in fk_map.items():
        rows = all_data.get(src_table, {})
        if len(rows) < 10:
            continue
        for col_name, meta in columns.items():
            if meta["target_table"] != src_table:
                continue
            if not_null_by_table[src_table].get(col_name):
                continue
            values = [row.get(col_name) for row in rows.values()]
            assert any(
                v is None for v in values
            ), f"{src_table}.{col_name} has no root rows"
            assert any(
                v is not None for v in values
            ), f"{src_table}.{col_name} never points at a parent"


def test_flag_timestamp_alignment(all_data, dbml_obj):
    """is_X / has_X flags stay consistent with an X_at timestamp when both exist."""
    for table in dbml_obj.tables:
        names = {c.name for c in table.columns}
        rows = all_data[table.name]
        for col in table.columns:
            n = col.name.lower()
            if not (n.startswith("is_") or n.startswith("has_")):
                continue
            flag = n.split("_", 1)[1]
            ts_name = f"{flag}_at" if f"{flag}_at" in names else None
            if col.name == "is_deleted" and "deleted_at" in names:
                ts_name = "deleted_at"
            if ts_name is None:
                continue
            for r_id, row in rows.items():
                if row.get(col.name):
                    assert row.get(ts_name) is not None, (
                        f"{table.name}[{r_id}]: {col.name}=true but {ts_name} is null"
                    )
                else:
                    assert row.get(ts_name) is None, (
                        f"{table.name}[{r_id}]: {col.name}=false but {ts_name} is set"
                    )

        if "is_active" in names:
            for r_id, row in rows.items():
                if row.get("is_active"):
                    for col in table.columns:
                        if col.name.lower().startswith("revoked"):
                            assert row.get(col.name) is None, (
                                f"{table.name}[{r_id}]: is_active but {col.name} is set"
                            )
                elif "revoked_at" in names:
                    assert row.get("revoked_at") is not None, (
                        f"{table.name}[{r_id}]: inactive but revoked_at is null"
                    )


def test_entity_type_id_pairs(all_data, dbml_obj):
    """entity_type / entity_id pairs (any prefix) point at a real seeded table and row."""
    for table in dbml_obj.tables:
        pairs = entity_pairs(table.columns)
        if not pairs:
            continue
        for type_col, id_col in pairs:
            for r_id, row in all_data[table.name].items():
                target = row.get(type_col)
                entity_id = row.get(id_col)
                if target is None or entity_id is None:
                    continue
                assert target in all_data, (
                    f"{table.name}[{r_id}].{type_col}={target} is not a seeded table"
                )
                ids = {
                    str(r.get(k))
                    for r in all_data[target].values()
                    for k in r
                    if k.endswith("_id") or k == "id"
                }
                assert str(entity_id) in ids, (
                    f"{table.name}[{r_id}].{id_col}={entity_id} not found in {target}"
                )


def test_version_sequences(all_data, dbml_obj, fk_map):
    """Unique (parent, version) indexes are 1..n per parent, not random ints."""
    for table in dbml_obj.tables:
        for index in table.indexes or []:
            if not index.unique:
                continue
            cols = [
                s.name
                for s in getattr(index, "subjects", [])
                if hasattr(s, "name")
            ]
            version_cols = [c for c in cols if is_sequence_version(c)]
            fk_cols = [c for c in cols if c in fk_map.get(table.name, {})]
            if not version_cols or not fk_cols:
                continue
            vcol, fcol = version_cols[0], fk_cols[0]
            grouped = {}
            for row in all_data[table.name].values():
                parent = row.get(fcol)
                if parent is None:
                    continue
                grouped.setdefault(parent, []).append(row[vcol])
            for parent, versions in grouped.items():
                assert sorted(versions) == list(range(1, len(versions) + 1)), (
                    f"{table.name}.{vcol} for {fcol}={parent} is {sorted(versions)}, "
                    f"expected 1..{len(versions)}"
                )


def test_current_version_tracks_children(all_data, dbml_obj, fk_map):
    """current_version on a parent matches max(child version) when children exist."""
    for table in dbml_obj.tables:
        current_cols = [
            c.name for c in table.columns if CURRENT_VERSION_RE.match(c.name)
        ]
        if not current_cols:
            continue
        pk = next((c.name for c in table.columns if c.pk), None)
        if not pk:
            continue
        for child in dbml_obj.tables:
            child_fks = fk_map.get(child.name, {})
            fk_col = next(
                (
                    col
                    for col, meta in child_fks.items()
                    if meta["target_table"] == table.name
                    and meta["target_column"] == pk
                ),
                None,
            )
            seq_cols = [c.name for c in child.columns if is_sequence_version(c.name)]
            if fk_col is None or not seq_cols:
                continue
            seq_col = seq_cols[0]
            maxima = {}
            for row in all_data[child.name].values():
                parent_id = row.get(fk_col)
                ver = row.get(seq_col)
                if parent_id is None or not isinstance(ver, int):
                    continue
                maxima[str(parent_id)] = max(maxima.get(str(parent_id), 0), ver)
            for row in all_data[table.name].values():
                parent_id = str(row.get(pk))
                if parent_id in maxima:
                    for current_col in current_cols:
                        assert row[current_col] == maxima[parent_id], (
                            f"{table.name}[{parent_id}].{current_col}="
                            f"{row[current_col]} != max child {seq_col} {maxima[parent_id]}"
                        )
            break


def test_timestamps_are_spread_out(all_data):
    """Date generators use a historical range, not a single 'now' instant."""
    stamps = []
    for rows in all_data.values():
        for row in rows.values():
            for val in row.values():
                if isinstance(val, str) and re.match(r"\d{4}-\d{2}-\d{2}T", val):
                    stamps.append(val)
    if len(stamps) < 10:
        return
    unique = set(stamps)
    assert len(unique) > 5, "Timestamps collapsed to a handful of identical values"
    dates = {s[:10] for s in stamps}
    assert len(dates) > 1, "All timestamps fall on the same calendar day"


def test_nullable_columns_are_sometimes_null(all_data, dbml_obj):
    """Nullable columns that are not flag-driven should not be filled on every row."""
    for table in dbml_obj.tables:
        rows = all_data[table.name]
        if len(rows) < 20:
            continue
        names = {c.name for c in table.columns}
        forced = set()
        for a, b in parse_xor_from_note(getattr(table, "note", None)):
            present = [c for c in (a, b) if c in names]
            if len(present) == 1:
                forced.add(present[0])
        for col in table.columns:
            if col.pk or col.not_null or col.unique or col.name in forced:
                continue
            n = col.name.lower()
            # These are driven by a sibling flag, so all-null can be correct.
            if n.endswith("_at") and (
                f"is_{n[:-3]}" in names or n in {"deleted_at", "revoked_at"}
            ):
                continue
            if n.startswith("revoked"):
                continue
            values = [row.get(col.name) for row in rows.values()]
            assert any(v is None for v in values), (
                f"{table.name}.{col.name} is nullable but never null"
            )
