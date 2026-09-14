import os
import random
import re
import json
import yaml
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from faker import Faker
from argparse import ArgumentParser

from .datetime_generator import later_than, in_the_future, as_datetime, is_date_only
from .field_types import (
    INTEGER_TYPES,
    configure_generators,
    generate_value,
    parse_sql_type,
)
from .datetime_generator import generate_timestamp
from .schema_loader import load_schema_file
from .schema_ir import Table
from .logging_config import configure_logging, get_logger

logger = get_logger(__name__)

# ======================================================
# GLOBAL STATE (PER RUN)
# ======================================================

USED_UNIQUE_VALUES = {}  # "table.column" -> set(values)
USED_COMPOSITE_UNIQUES = {}  # "table.(col1,col2)" -> set(tuple)
VERSION_COUNTERS = {}  # (table, col, parent_key) -> last int
PARENT_USAGE = defaultdict(int)  # (src_table, col, parent_val) -> count

DEFAULT_CONFIG = {
    "seed": 42,
    "null_probability": 0.35,
    "default_probability": 0.7,
    "self_fk_root_probability": 0.35,
    "Num_of_entries": {"default": 100},
}

FUTURE_NAME_RE = re.compile(r"(expire|expiry|due|valid_until)", re.I)
VERSION_NAME_RE = re.compile(
    r"(^|_)(version_number|config_version|revision|rev)(_|$)|(^|_)version$",
    re.I,
)
CURRENT_VERSION_RE = re.compile(r"^current[_-]?(version|revision|rev)$", re.I)
ENTITY_TYPE_RE = re.compile(r"(entity_type|target_type)$", re.I)
ENTITY_ID_RE = re.compile(r"(entity_id|target_id)$", re.I)
XOR_NOTE_RE = re.compile(
    r"(?:either\s+)?(\w+)\s+or\s+(\w+)(?:\s+must|\s+cannot|\s+should)?",
    re.I,
)
USER_GROUP_XOR = ("user_id", "group_id")

GEOGRAPHY_PROFILES = (
    {
        "country": "United States",
        "regions": (
            ("California", "Los Angeles", "90001"),
            ("New York", "New York", "10001"),
            ("Texas", "Houston", "77001"),
            ("Florida", "Miami", "33101"),
            ("Illinois", "Chicago", "60601"),
            ("Washington", "Seattle", "98101"),
            ("Georgia", "Atlanta", "30301"),
            ("Colorado", "Denver", "80202"),
        ),
    },
)

# Earlier event -> later event, by column-name prefix.
TIMESTAMP_ORDER = (
    "created",
    "granted",
    "requested",
    "installed",
    "opened",
    "initiated",
    "started",
    "detected",
    "effective",
    "watched",
    "occurred",
    "edited",
    "changed",
    "updated",
    "last_seen",
    "completed",
    "closed",
    "resolved",
    "failed",
    "revoked",
    "deleted",
    "trashed",
    "archived",
    "cancelled",
    "canceled",
    "cancel",
    "expires",
    "expire",
    "expiry",
    "expiration",
    "due",
)


# ======================================================
# UTILITIES
# ======================================================


def reset_state():
    USED_UNIQUE_VALUES.clear()
    USED_COMPOSITE_UNIQUES.clear()
    VERSION_COUNTERS.clear()
    PARENT_USAGE.clear()


def unique_key(table: str, column: str) -> str:
    return f"{table}.{column}"


def composite_key(table: str, cols: Tuple[str, ...]) -> str:
    return f"{table}.({','.join(cols)})"


def merge_config(raw) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not raw:
        return cfg
    if "Num_of_entries" in raw and raw["Num_of_entries"]:
        cfg["Num_of_entries"] = {
            **cfg["Num_of_entries"],
            **raw["Num_of_entries"],
        }
    for key in (
        "seed",
        "null_probability",
        "default_probability",
        "self_fk_root_probability",
        "xor_groups",
    ):
        if key in raw and raw[key] is not None:
            cfg[key] = raw[key]
    return cfg


def build_fk_map(refs):
    fk_map = {}

    for ref in refs:
        if ref.type == "<>":
            continue

        if ref.type in [">", "-"]:
            src_cols = ref.col1
            tgt_cols = ref.col2
        else:
            src_cols = ref.col2
            tgt_cols = ref.col1

        for src_col, tgt_col in zip(src_cols, tgt_cols):
            fk_map.setdefault(src_col.table.name, {})[src_col.name] = {
                "target_table": tgt_col.table.name,
                "target_column": tgt_col.name,
            }

    return fk_map


def build_fk_map_from_schema(schema):
    fk_map = {}
    for relationship in schema.relationships:
        if relationship.cardinality == "many-to-many":
            continue
        for source_column, target_column in zip(
            relationship.source_columns, relationship.target_columns
        ):
            fk_map.setdefault(relationship.source_table, {})[source_column] = {
                "target_table": relationship.target_table,
                "target_column": target_column,
            }
    return fk_map


def topo_sort_tables(tables: List[Table], fk_map: Dict[str, Dict]) -> List[Table]:
    graph = {t.name: set() for t in tables}

    for src, cols in fk_map.items():
        for meta in cols.values():
            tgt = meta["target_table"]
            if src != tgt:
                graph[src].add(tgt)

    visited, temp, result = set(), set(), []

    def visit(node):
        if node in temp:
            raise RuntimeError(f"Circular dependency detected at {node}")
        if node not in visited:
            temp.add(node)
            for dep in graph[node]:
                visit(dep)
            temp.remove(node)
            visited.add(node)
            result.append(node)

    for t in graph:
        visit(t)

    table_map = {t.name: t for t in tables}
    return [table_map[name] for name in result]


def save_json(name, data, output_dir="data"):
    os.makedirs(output_dir, exist_ok=True)
    with open(f"{output_dir}/{name}.json", "w") as f:
        json.dump(data, f, indent=4)


def validate_generated_native_types(schema, generated_data):
    """Reject output that cannot represent declared native JSON-compatible types."""
    for table in schema.tables:
        columns = {column.name: column for column in table.columns}
        for row in generated_data.get(table.name, {}).values():
            for name, column in columns.items():
                if name not in row or row[name] is None:
                    continue
                value = row[name]
                base = column_base_type(column)
                if base == "uuid":
                    try:
                        UUID(str(value))
                    except (ValueError, TypeError, AttributeError) as exc:
                        raise ValueError(f"{table.name}.{name} is not a valid UUID") from exc
                if column.data_type.is_array and not isinstance(value, list):
                    raise ValueError(f"{table.name}.{name} must be emitted as an array")
                if base in {"json", "jsonb"} and not isinstance(value, (dict, list)):
                    raise ValueError(f"{table.name}.{name} must be emitted as JSON")


def index_columns(index) -> List[str]:
    return [s.name for s in getattr(index, "subjects", []) if hasattr(s, "name")]


def is_nullable(column) -> bool:
    return not (column.pk or column.not_null)


def is_sequence_version(name: str) -> bool:
    if CURRENT_VERSION_RE.match(name):
        return False
    return bool(VERSION_NAME_RE.search(name))


def timestamp_prefix(col_name: str) -> Optional[str]:
    n = col_name.lower()
    if not (n.endswith("_at") or n.endswith("_on") or n.endswith("_date")):
        return None
    stem = re.sub(r"(_at|_on|_date)$", "", n)
    for prefix in TIMESTAMP_ORDER:
        if stem == prefix or stem.startswith(prefix):
            return prefix
    return None


def coerce_default(value, col_type: str, is_enum: bool):
    if value is None:
        return None
    if is_enum:
        return str(value).strip("'\"")
    if isinstance(value, (bool, int, float)):
        return value
    text = str(value).strip("'\"")
    col_type, _ = parse_sql_type(col_type)
    if col_type in {"bool", "boolean"}:
        return text.lower() in {"true", "1", "t", "yes"}
    if col_type in INTEGER_TYPES:
        try:
            return int(text)
        except ValueError:
            return value
    return text


def evaluate_default(value, column):
    """Convert supported schema defaults into values suitable for JSON output."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, list, dict)):
        return value

    text = str(value).strip()
    normalized = text.strip("'\"").strip().lower()
    base = column_base_type(column)
    if normalized in {"now()", "current_timestamp", "current_timestamp()", "localtimestamp"}:
        return generate_timestamp()
    if normalized in {"gen_random_uuid()", "uuid_generate_v4()", "uuid()", "cuid()"}:
        return generate_value(column.name, "uuid")
    if base in {"json", "jsonb"}:
        json_text = re.sub(r"^cast\((.*)\s+as\s+jsonb\)$", r"\1", normalized, flags=re.I)
        json_text = json_text.replace("::jsonb", "").strip().strip("'")
        try:
            return json.loads(json_text)
        except (TypeError, json.JSONDecodeError):
            return {}
    if getattr(column.data_type, "is_array", False):
        array_text = normalized.replace("::text[]", "").replace("::varchar[]", "").strip()
        if array_text in {"{}", "array[]", "array[]::text[]"}:
            return []
        try:
            return json.loads(array_text)
        except (TypeError, json.JSONDecodeError):
            return []
    return coerce_default(value, str(column.type), column.data_type.enum_name is not None)


def parse_xor_from_note(note) -> List[Tuple[str, str]]:
    if not note:
        return []
    text = str(note)
    pairs = []
    for match in XOR_NOTE_RE.finditer(text):
        a, b = match.group(1), match.group(2)
        if a.lower() in {"either", "both"} or b.lower() in {"both", "must"}:
            continue
        pairs.append((a, b))
    return pairs


def xor_groups_for_table(
    table: Table, config: dict
) -> Tuple[List[List[str]], set[str]]:
    col_names = {c.name for c in table.columns}
    groups: List[List[str]] = []

    configured = (config.get("xor_groups") or {}).get(table.name, [])
    for group in configured:
        present = [c for c in group if c in col_names]
        if len(present) >= 2:
            groups.append(present)

    required_from_notes = set()
    for a, b in parse_xor_from_note(getattr(table, "note", None)):
        present = [c for c in (a, b) if c in col_names]
        if len(present) >= 2:
            groups.append(present)
        elif len(present) == 1:
            # Note requires one of the pair; the only mapped column should be filled.
            required_from_notes.add(present[0])

    if all(c in col_names for c in USER_GROUP_XOR):
        groups.append(list(USER_GROUP_XOR))

    # Deduplicate while keeping order.
    seen = set()
    unique = []
    for group in groups:
        key = tuple(sorted(group))
        if key not in seen:
            seen.add(key)
            unique.append(group)
    return unique, required_from_notes


def entity_pairs(columns) -> List[Tuple[str, str]]:
    type_cols = [c.name for c in columns if ENTITY_TYPE_RE.search(c.name)]
    id_cols = [c.name for c in columns if ENTITY_ID_RE.search(c.name)]
    pairs = []
    for tcol in type_cols:
        prefix = ENTITY_TYPE_RE.sub("", tcol)
        match = next(
            (icol for icol in id_cols if ENTITY_ID_RE.sub("", icol) == prefix),
            None,
        )
        if match:
            pairs.append((tcol, match))
    return pairs


def column_base_type(column) -> str:
    raw = column.type if isinstance(column.type, str) else str(column.type)
    base, _ = parse_sql_type(str(raw))
    return base


def is_date_column(column) -> bool:
    return column_base_type(column) == "date"


def emit_later(earlier, column=None, stored=None):
    """Advance a temporal value, keeping date columns as YYYY-MM-DD."""
    as_date = (column is not None and is_date_column(column)) or is_date_only(stored)
    name = column.name if column is not None else ""
    if FUTURE_NAME_RE.search(name):
        return in_the_future(after=earlier, as_date=as_date)
    return later_than(earlier, as_date=as_date)


def table_pk_column(table: Table) -> Optional[str]:
    for col in table.columns:
        if col.pk:
            return col.name
    return None


def parent_values(parent_rows: dict, target_column: str) -> List:
    return [
        row[target_column]
        for row in parent_rows.values()
        if row.get(target_column) is not None
    ]


def pick_parent_value(src_table: str, col_name: str, values: List, used: Optional[set]):
    pool = list(values)
    if used is not None:
        pool = [v for v in pool if v not in used]
    if not pool:
        return None
    pool.sort(key=lambda v: PARENT_USAGE[(src_table, col_name, v)])
    lowest = PARENT_USAGE[(src_table, col_name, pool[0])]
    candidates = [v for v in pool if PARENT_USAGE[(src_table, col_name, v)] == lowest]
    return random.choice(candidates)


def next_version(table: str, col: str, parent_key) -> int:
    key = (table, col, parent_key)
    VERSION_COUNTERS[key] = VERSION_COUNTERS.get(key, 0) + 1
    return VERSION_COUNTERS[key]


_ACTION_VERBS = (
    "create",
    "update",
    "delete",
    "manage",
    "grant",
    "configure",
    "export",
    "import",
    "restore",
    "clone",
    "publish",
    "unpublish",
    "archive",
    "watch",
    "unwatch",
    "rename",
    "move",
)


def infer_table_from_action(action, table_names: List[str]) -> Optional[str]:
    if not action:
        return None
    token = str(action).lower().replace("-", "_")
    remainder = token
    for verb in _ACTION_VERBS:
        remainder = remainder.replace(verb, " ")
    remainder_tokens = [t for t in re.split(r"[_\s]+", remainder) if t]

    scored = []
    for name in table_names:
        stem = name.lower().rstrip("s")
        if stem and stem in token:
            scored.append((2, len(stem), name))
        elif name.lower() in token:
            scored.append((2, len(name), name))
        elif any(
            stem.startswith(t.rstrip("s")) or t.rstrip("s") in stem
            for t in remainder_tokens
            if len(t) > 3
        ):
            scored.append((1, len(stem), name))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


# ======================================================
# ROW CONSISTENCY (NAME-PATTERN BASED, SCHEMA-AGNOSTIC)
# ======================================================


def _ensure_one_optional_fk(entry, table, fk_map, fill, xor_groups=None):
    """If every nullable FK is empty, fill one so the row still has a target."""
    fks = fk_map.get(table.name, {})
    locked_out = set()
    for group in xor_groups or []:
        if any(entry.get(c) is not None for c in group):
            locked_out.update(c for c in group if entry.get(c) is None)
    optional = [
        c.name
        for c in table.columns
        if c.name in fks and is_nullable(c) and c.name not in locked_out
    ]
    if len(optional) < 2:
        return
    if any(entry.get(c) is not None for c in optional):
        return
    chosen = random.choice(optional)
    entry[chosen] = fill(chosen)


def apply_xor(entry: dict, groups: List[List[str]], fill):
    for group in groups:
        present = [c for c in group if c in entry]
        if len(present) < 2:
            continue
        chosen = random.choice(present)
        for col in present:
            if col == chosen:
                if entry[col] is None:
                    entry[col] = fill(col)
            else:
                entry[col] = None


def apply_flag_timestamps(entry: dict, columns):
    names = {c.name for c in columns}
    for col in columns:
        n = col.name.lower()
        if not n.startswith("is_") and not n.startswith("has_"):
            continue
        flag = n.split("_", 1)[1]
        ts_name = f"{flag}_at" if f"{flag}_at" in names else None
        if col.name == "is_deleted" and "deleted_at" in names:
            ts_name = "deleted_at"
        if ts_name is None:
            continue
        if entry.get(col.name):
            if entry.get(ts_name) is None:
                earlier = entry.get("created_at") or entry.get("granted_at")
                ts_col = next((c for c in columns if c.name == ts_name), None)
                if earlier:
                    entry[ts_name] = emit_later(earlier, ts_col, entry.get(ts_name))
                else:
                    entry[ts_name] = generate_value(
                        ts_name, str(ts_col.type) if ts_col else "datetime"
                    )
        else:
            entry[ts_name] = None

    # is_active (and similar) invert revoked_* columns.
    if "is_active" in names:
        active = bool(entry.get("is_active"))
        for col in columns:
            if not col.name.lower().startswith("revoked"):
                continue
            if active:
                entry[col.name] = None
            elif entry.get(col.name) is None:
                col_type = str(col.type).lower()
                if col_type in {"datetime", "timestamp", "date"}:
                    earlier = entry.get("granted_at") or entry.get("created_at")
                    if earlier:
                        entry[col.name] = emit_later(earlier, col, entry.get(col.name))
                    else:
                        entry[col.name] = generate_value(col.name, col_type)


def apply_verb_alignment(entry: dict, columns, fk_map, table_name, generated_data, table_data):
    """If updated_at is null, updated_by* is null; same for revoked/deleted."""
    names = [c.name for c in columns]
    for col in columns:
        prefix = timestamp_prefix(col.name)
        if not prefix:
            continue
        ts_val = entry.get(col.name)
        for other in names:
            if other == col.name:
                continue
            if other.lower().startswith(f"{prefix}_by"):
                if ts_val is None:
                    entry[other] = None
                elif entry.get(other) is None:
                    entry[other] = _fill_related(
                        other, columns, fk_map, table_name, generated_data, table_data
                    )


def apply_timestamp_order(entry: dict, columns):
    dated = []
    col_by_name = {c.name: c for c in columns}
    for col in columns:
        prefix = timestamp_prefix(col.name)
        if prefix is None or prefix not in TIMESTAMP_ORDER or entry.get(col.name) is None:
            continue
        dated.append((TIMESTAMP_ORDER.index(prefix), col.name))
    dated.sort()
    previous = None
    for _, name in dated:
        current = as_datetime(entry[name])
        if previous is not None and (current is None or current < previous):
            entry[name] = emit_later(previous, col_by_name.get(name), entry.get(name))
            current = as_datetime(entry[name])
        if current is not None:
            previous = current


INHERIT_COLUMNS = {"currency", "country"}
PREFERRED_INHERIT_FKS = (
    "account_id",
    "order_id",
    "policy_id",
    "invoice_id",
    "shipment_id",
    "reservation_id",
    "customer_id",
)


def apply_inherited_context(entry, table, fk_map, generated_data, table_data):
    """Copy currency/country from a parent row when both sides have the column."""
    fks = fk_map.get(table.name, {})
    if not fks:
        return
    inherit = [
        c.name
        for c in table.columns
        if c.name.lower() in INHERIT_COLUMNS
        or c.name.lower().endswith("_currency")
    ]
    if not inherit:
        return
    fk_order = [c for c in PREFERRED_INHERIT_FKS if c in fks] + [
        c for c in fks if c not in PREFERRED_INHERIT_FKS
    ]
    for col_name in inherit:
        found = False
        for fk_col in fk_order:
            meta = fks[fk_col]
            parent_id = entry.get(fk_col)
            if parent_id is None:
                continue
            parents = _parent_rows(table.name, meta, generated_data, table_data)
            target = meta["target_column"]
            for row in parents.values():
                if str(row.get(target)) != str(parent_id):
                    continue
                if row.get(col_name) is not None:
                    entry[col_name] = row[col_name]
                    found = True
                    break
            if found:
                break


def apply_geographic_consistency(entry, columns):
    """Keep country, region, city, and postal fields from one location profile."""
    names = {column.name.lower(): column.name for column in columns}
    country_name = next(
        (name for normalized, name in names.items() if normalized in {"country", "country_name"}),
        None,
    )
    state_name = next(
        (name for normalized, name in names.items() if normalized in {"state", "state_name", "province", "region"}),
        None,
    )
    city_name = next(
        (name for normalized, name in names.items() if normalized in {"city", "city_name", "town"}),
        None,
    )
    postal_name = next(
        (name for normalized, name in names.items() if normalized in {"postal_code", "postcode", "postalcode", "zip", "zipcode", "zip_code"}),
        None,
    )
    if not any((country_name, state_name, city_name, postal_name)):
        return

    existing_country = str(entry.get(country_name, "")).strip().lower() if country_name else ""
    profile = next(
        (profile for profile in GEOGRAPHY_PROFILES if profile["country"].lower() == existing_country),
        None,
    ) or random.choice(GEOGRAPHY_PROFILES)
    region, city, postal_code = random.choice(profile["regions"])

    if country_name and entry.get(country_name) is not None:
        entry[country_name] = profile["country"]
    if state_name and entry.get(state_name) is not None:
        entry[state_name] = region
    if city_name and entry.get(city_name) is not None:
        entry[city_name] = city
    if postal_name and entry.get(postal_name) is not None:
        entry[postal_name] = postal_code


def apply_identity(entry: dict, table_name: str, pending_uniques: list):
    """If a row has a person name and an email, make the email match the name."""
    name = None
    email_col = None
    first = last = None
    for key, val in entry.items():
        n = key.lower()
        if val and any(tok in n for tok in ("full_name", "fullname", "display_name")):
            name = val
        if n == "name" and " " in str(val or ""):
            name = val
        if n in {"first_name", "firstname"} and val:
            first = val
        if n in {"last_name", "lastname"} and val:
            last = val
        if n.endswith("email") or n == "email_address":
            if "notification" in n or "verified" in n:
                continue
            email_col = key
    if first and last:
        name = f"{first} {last}"
    if not (name and email_col and entry.get(email_col)):
        return
    parts = str(name).split()
    local = ".".join(p.lower() for p in parts if p.isalpha()) or "user"
    ukey = unique_key(table_name, email_col)
    used = USED_UNIQUE_VALUES.setdefault(ukey, set())
    pending = {u for k, u in pending_uniques if k == ukey}
    for _ in range(50):
        email = f"{local}.{random.randint(1, 99999)}@example.com"
        if email not in used and email not in pending:
            entry[email_col] = email
            pending_uniques[:] = [
                (k, v) for k, v in pending_uniques if k != ukey
            ]
            pending_uniques.append((ukey, email))
            return
    entry[email_col] = f"{local}.{row_suffix()}@example.com"


def row_suffix():
    return f"{random.randint(100000, 999999)}"


def apply_entity_refs(entry: dict, columns, generated_data, action_col=None, current_table=None):
    tables = [
        n for n, rows in generated_data.items() if rows and n != current_table
    ] or [n for n, rows in generated_data.items() if rows]
    if not tables:
        return
    action = entry.get(action_col) if action_col else None
    preferred = infer_table_from_action(action, tables)
    for type_col, id_col in entity_pairs(columns):
        target = preferred if preferred in generated_data else random.choice(tables)
        rows = generated_data.get(target) or {}
        if not rows:
            continue
        row = random.choice(list(rows.values()))
        pk = next(
            (row[k] for k in row if k.endswith("_id") or k == "id"),
            next(iter(row.values())),
        )
        # Prefer an obvious PK column when present.
        for key in (f"{str(target).rstrip('s')}_id", f"{target}_id", "id"):
            if key in row:
                pk = row[key]
                break
        entry[type_col] = target
        entry[id_col] = pk


def _fill_related(col_name, columns, fk_map, table_name, generated_data, table_data):
    col = next((c for c in columns if c.name == col_name), None)
    if col is None:
        return None
    if col_name in fk_map.get(table_name, {}):
        meta = fk_map[table_name][col_name]
        parents = generated_data.get(meta["target_table"], {})
        if meta["target_table"] == table_name:
            parents = table_data or parents
        values = parent_values(parents, meta["target_column"])
        return random.choice(values) if values else None
    return generate_value(col_name, str(col.type).lower())


# ======================================================
# CORE SEEDER
# ======================================================


def _parent_rows(table_name, meta, generated_data, table_data):
    target = meta["target_table"]
    if target == table_name:
        return table_data
    return generated_data.get(target, {})


def generate_column_value(
    column,
    table,
    enums,
    fk_map,
    generated_data,
    table_data,
    row_id,
    config,
    pending_uniques: list,
    force: bool = False,
):
    col_name = column.name
    col_type = str(column.type).lower()
    table_name = table.name
    fks = fk_map.get(table_name, {})

    if column.pk and col_name not in fks:
        if column_base_type(column) in INTEGER_TYPES:
            return int(row_id)
        if column_base_type(column) == "uuid":
            return generate_value(col_name, "uuid")
        return row_id

    # Schema default, used most of the time for non-unique columns.
    if (
        column.default is not None
        and not column.unique
        and random.random() < config["default_probability"]
    ):
        return evaluate_default(column.default, column)

    if (
        not force
        and is_nullable(column)
        and random.random() < config["null_probability"]
    ):
        return None

    if col_name in fks:
        meta = fks[col_name]
        parents = _parent_rows(table_name, meta, generated_data, table_data)
        values = parent_values(parents, meta["target_column"])

        if not values:
            if column.not_null and meta["target_table"] == table_name:
                if column_base_type(column) in INTEGER_TYPES:
                    return int(row_id)
                return row_id
            return None

        if meta["target_table"] == table_name and is_nullable(column):
            if random.random() < config["self_fk_root_probability"]:
                return None

        used = None
        if column.unique:
            ukey = unique_key(table_name, col_name)
            USED_UNIQUE_VALUES.setdefault(ukey, set())
            used = USED_UNIQUE_VALUES[ukey]

        value = pick_parent_value(table_name, col_name, values, used)
        if value is None:
            return None
        if column.unique:
            pending_uniques.append((unique_key(table_name, col_name), value))
        return value

    if column.data_type.enum_name is not None:
        enum_vals = enums[column.data_type.enum_name].values
        if column.data_type.is_array:
            return [random.choice(enum_vals) for _ in range(random.randint(1, 3))]
        return random.choice(enum_vals)

    if column.data_type.enum_values:
        return random.choice(column.data_type.enum_values)

    for _ in range(80):
        val = generate_value(col_name, column.data_type)
        if not column.unique:
            return val
        ukey = unique_key(table_name, col_name)
        USED_UNIQUE_VALUES.setdefault(ukey, set())
        if val not in USED_UNIQUE_VALUES[ukey]:
            pending_uniques.append((ukey, val))
            return val
    return generate_value(col_name, col_type)


def _version_parent_fk(table: Table, fk_map) -> Optional[str]:
    fks = fk_map.get(table.name, {})
    if not fks:
        return None
    required = [
        c.name
        for c in table.columns
        if c.name in fks and c.not_null and fks[c.name]["target_table"] != table.name
    ]
    if required:
        return required[0]
    optional = [
        c.name
        for c in table.columns
        if c.name in fks and fks[c.name]["target_table"] != table.name
    ]
    return optional[0] if optional else None


def _fill_sequence_versions(entry, table, fk_map):
    parent_fk = _version_parent_fk(table, fk_map)
    for column in table.columns:
        if not is_sequence_version(column.name):
            continue
        if entry.get(column.name) is not None:
            continue
        parent_key = entry.get(parent_fk) if parent_fk else None
        entry[column.name] = next_version(table.name, column.name, parent_key)


def _prefer_unused_enum_fk_pairs(entry, table, fk_map, enums, pending_pairs: list):
    """Avoid repeating (parent, enum) combinations when the schema did not declare them unique."""
    fks = fk_map.get(table.name, {})
    enum_cols = [c for c in table.columns if c.data_type.enum_name is not None]
    fk_cols = [
        c.name
        for c in table.columns
        if c.name in fks and entry.get(c.name) is not None
    ]
    if not enum_cols or not fk_cols:
        return
    for ecol in enum_cols:
        if entry.get(ecol.name) is None:
            continue
        for fk_col in fk_cols:
            combo_key = ("enum_fk", table.name, fk_col, ecol.name)
            used = USED_COMPOSITE_UNIQUES.setdefault(combo_key, set())
            pair = (entry[fk_col], entry[ecol.name])
            if pair in used:
                enum_vals = enums[ecol.data_type.enum_name].values
                random.shuffle(enum_vals)
                for candidate in enum_vals:
                    alt = (entry[fk_col], candidate)
                    if alt not in used:
                        entry[ecol.name] = candidate
                        pair = alt
                        break
            pending_pairs.append((combo_key, pair))


def seed_table(table: Table, enums, fk_map, generated_data, entries, config):
    table_data = {}
    composite_uniques = []
    for idx in table.indexes:
        cols = index_columns(idx)
        if idx.unique and len(cols) > 1:
            composite_uniques.append(tuple(cols))

    xor_groups, required_from_notes = xor_groups_for_table(table, config)
    col_by_name = {c.name: c for c in table.columns}

    for i in range(1, entries + 1):
        row_id = str(i)
        success = False

        for _attempt in range(100):
            entry = {}
            pending_uniques = []
            aborted = False

            for column in table.columns:
                if is_sequence_version(column.name) or (column.generated and not column.pk):
                    continue
                value = generate_column_value(
                    column,
                    table,
                    enums,
                    fk_map,
                    generated_data,
                    table_data,
                    row_id,
                    config,
                    pending_uniques,
                )
                if (
                    column.unique
                    and column.name in fk_map.get(table.name, {})
                    and value is None
                    and not is_nullable(column)
                ):
                    aborted = True
                    break
                entry[column.name] = value

            if aborted:
                continue

            _fill_sequence_versions(entry, table, fk_map)

            def refill(col_name):
                col = col_by_name[col_name]
                return generate_column_value(
                    col,
                    table,
                    enums,
                    fk_map,
                    generated_data,
                    table_data,
                    row_id,
                    config,
                    pending_uniques,
                    force=True,
                )

            pending_pairs = []
            apply_xor(entry, xor_groups, refill)
            for col_name in required_from_notes:
                if entry.get(col_name) is None:
                    entry[col_name] = refill(col_name)
            _ensure_one_optional_fk(entry, table, fk_map, refill, xor_groups)
            apply_flag_timestamps(entry, table.columns)
            apply_verb_alignment(
                entry, table.columns, fk_map, table.name, generated_data, table_data
            )
            apply_inherited_context(
                entry, table, fk_map, generated_data, table_data
            )
            apply_geographic_consistency(entry, table.columns)
            apply_timestamp_order(entry, table.columns)
            apply_identity(entry, table.name, pending_uniques)
            _prefer_unused_enum_fk_pairs(
                entry, table, fk_map, enums, pending_pairs
            )

            # Restore NOT NULL columns that consistency may have cleared.
            for column in table.columns:
                if (
                    entry.get(column.name) is None
                    and (column.not_null or column.pk)
                ):
                    entry[column.name] = generate_column_value(
                        column,
                        table,
                        enums,
                        fk_map,
                        generated_data,
                        table_data,
                        row_id,
                        config,
                        pending_uniques,
                    )

            is_valid_composite = True
            current_combos = []
            for cols in composite_uniques:
                ckey = composite_key(table.name, cols)
                USED_COMPOSITE_UNIQUES.setdefault(ckey, set())
                c_val = tuple(entry.get(c) for c in cols)
                if c_val in USED_COMPOSITE_UNIQUES[ckey]:
                    is_valid_composite = False
                    break
                current_combos.append((ckey, c_val))

            if not is_valid_composite:
                continue

            for ckey, c_val in current_combos:
                USED_COMPOSITE_UNIQUES[ckey].add(c_val)
            for ckey, pair in pending_pairs:
                USED_COMPOSITE_UNIQUES.setdefault(ckey, set()).add(pair)
            for ukey, val in pending_uniques:
                USED_UNIQUE_VALUES.setdefault(ukey, set()).add(val)
            for col_name, meta in fk_map.get(table.name, {}).items():
                val = entry.get(col_name)
                if val is not None:
                    PARENT_USAGE[(table.name, col_name, val)] += 1

            table_data[row_id] = entry
            success = True
            break

        if not success:
            logger.warning(
                "Failed to generate unique row for table %s after 100 attempts",
                table.name,
            )

    generated_data[table.name] = table_data


def fill_entity_refs(tables: List[Table], generated_data):
    """Resolve generic entity_type/entity_id pairs after every table exists."""
    for table in tables:
        if not entity_pairs(table.columns):
            continue
        action_col = next(
            (c.name for c in table.columns if "action" in c.name.lower()),
            None,
        )
        for row in generated_data.get(table.name, {}).values():
            apply_entity_refs(
                row, table.columns, generated_data, action_col, table.name
            )


def sync_current_versions(tables: List[Table], fk_map, generated_data):
    """Point current_version-style columns at the max child sequence when one exists."""
    for table in tables:
        current_cols = [
            c.name for c in table.columns if CURRENT_VERSION_RE.match(c.name)
        ]
        if not current_cols:
            continue
        pk = table_pk_column(table)
        if not pk:
            continue

        for child in tables:
            child_fks = fk_map.get(child.name, {})
            fk_to_parent = next(
                (
                    col
                    for col, meta in child_fks.items()
                    if meta["target_table"] == table.name
                    and meta["target_column"] == pk
                ),
                None,
            )
            if fk_to_parent is None:
                continue
            seq_cols = [
                c.name for c in child.columns if is_sequence_version(c.name)
            ]
            if not seq_cols:
                continue
            seq_col = seq_cols[0]
            maxima = defaultdict(int)
            for row in generated_data.get(child.name, {}).values():
                parent_id = row.get(fk_to_parent)
                ver = row.get(seq_col)
                if parent_id is None or not isinstance(ver, int):
                    continue
                if ver > maxima[str(parent_id)]:
                    maxima[str(parent_id)] = ver

            for row in generated_data.get(table.name, {}).values():
                parent_id = str(row.get(pk))
                if parent_id in maxima:
                    for current_col in current_cols:
                        row[current_col] = maxima[parent_id]
                else:
                    for current_col in current_cols:
                        if row.get(current_col) in (None, 0):
                            row[current_col] = coerce_default(
                                next(
                                    c.default
                                    for c in table.columns
                                    if c.name == current_col
                                ),
                                "int",
                                False,
                            ) or 1
            break


# ======================================================
# MAIN
# ======================================================


def seed(
    schema_path,
    config_path,
    output_dir=None,
    schema_format="auto",
    dialect="sqlite",
    persist_json=True,
):
    configure_logging()
    logger.info(
        "Starting seed: schema=%s config=%s output=%s format=%s dialect=%s",
        schema_path,
        config_path,
        output_dir,
        schema_format,
        dialect,
    )
    reset_state()

    schema = load_schema_file(schema_path, schema_format, dialect)
    with open(config_path) as f:
        config = merge_config(yaml.safe_load(f))

    configure_generators(int(config.get("seed", 42)))
    Faker.seed(int(config.get("seed", 42)))

    enums = schema.enum_map
    fk_map = build_fk_map_from_schema(schema)
    tables = topo_sort_tables(list(schema.tables), fk_map)
    logger.info("Loaded schema with %d tables and %d relationships", len(tables), len(schema.relationships))
    generated_data = {}

    for table in tables:
        entries = config["Num_of_entries"].get(
            table.name, config["Num_of_entries"].get("default", 100)
        )
        seed_table(
            table=table,
            enums=enums,
            fk_map=fk_map,
            generated_data=generated_data,
            entries=entries,
            config=config,
        )
        logger.info("Generated %d rows for table %s", len(generated_data[table.name]), table.name)

    fill_entity_refs(tables, generated_data)
    sync_current_versions(tables, fk_map, generated_data)
    validate_generated_native_types(schema, generated_data)

    if persist_json:
        if output_dir is None:
            raise ValueError("output_dir is required when persist_json is enabled")
        for name, rows in generated_data.items():
            save_json(name, rows, output_dir)

    logger.info("Seed completed: %d tables generated", len(generated_data))
    return generated_data


def main():
    configure_logging()
    parser = ArgumentParser()
    parser.add_argument("--schema", type=str, default="schema.dbml")
    parser.add_argument("--config", type=str, default="config.yaml")
    parser.add_argument("--output", type=str, default="data")
    parser.add_argument("--format", choices=("auto", "dbml", "sql", "prisma"), default="auto")
    parser.add_argument("--dialect", choices=("sqlite", "postgres", "mysql"), default="sqlite")
    args = parser.parse_args()
    try:
        seed(args.schema, args.config, args.output, args.format, args.dialect)
    except Exception:
        logger.exception("Seed failed")
        raise


if __name__ == "__main__":
    main()
