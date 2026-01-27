import os
import random
import json
import yaml
from typing import List, Dict, Tuple
from faker import Faker
from pydbml import PyDBML
from pydbml.classes import Enum, Table

from field_types import ALL_SIMPLE_TYPES, ALL_NUMERICAL_TYPES, DATE_TYPES

Faker.seed(42)
fake = Faker()

# ======================================================
# GLOBAL STATE (PER RUN)
# ======================================================

USED_UNIQUE_VALUES = {}  # "table.column" -> set(values)
USED_COMPOSITE_UNIQUES = {}  # "table.(col1,col2)" -> set(tuple)

# ======================================================
# UTILITIES
# ======================================================


def unique_key(table: str, column: str) -> str:
    return f"{table}.{column}"


def composite_key(table: str, cols: Tuple[str]) -> str:
    return f"{table}.({','.join(cols)})"


def build_fk_map(refs):
    fk_map = {}

    for ref in refs:
        if ref.type == "<>":
            continue  # many-to-many skipped for now

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


def save_json(name, data):
    os.makedirs("data", exist_ok=True)
    with open(f"data/{name}.json", "w") as f:
        json.dump(data, f, indent=4)


# ======================================================
# CORE SEEDER
# ======================================================


def seed_table(table: Table, enums, fk_map, generated_data, entries):
    table_data = {}

    # Corrected subject extraction for pydbml Index objects
    composite_uniques = []
    for idx in table.indexes:
        # Use .subjects as discovered in previous debug steps
        cols = [s.name for s in getattr(idx, "subjects", []) if hasattr(s, "name")]
        if idx.unique and len(cols) > 1:
            composite_uniques.append(tuple(cols))

    for i in range(1, entries + 1):
        row_id = str(i)

        # --- RETRY LOOP FOR COMPOSITE INTEGRITY ---
        success = False
        for attempt in range(100):  # Give it 100 tries to find a unique combo
            entry = {}
            for column in table.columns:
                col_name = column.name
                col_type = str(column.type).lower()
                lname = col_name.lower()

                # 1. Primary Key
                if column.pk:
                    entry[col_name] = row_id
                    continue

                # 2. Foreign Key Logic
                if col_name in fk_map.get(table.name, {}):
                    meta = fk_map[table.name][col_name]
                    parent_rows = generated_data.get(meta["target_table"], {})

                    if not parent_rows:
                        entry[col_name] = None
                        continue

                    if column.unique:
                        ukey = unique_key(table.name, col_name)
                        USED_UNIQUE_VALUES.setdefault(ukey, set())
                        available = [
                            r[meta["target_column"]]
                            for r in parent_rows.values()
                            if r[meta["target_column"]] not in USED_UNIQUE_VALUES[ukey]
                        ]
                        if not available:
                            break  # Fail attempt
                        value = random.choice(available)
                        entry[col_name] = value
                    else:
                        parent = random.choice(list(parent_rows.values()))
                        entry[col_name] = parent[meta["target_column"]]
                    continue

                # 3. Enum Logic
                if isinstance(column.type, Enum):
                    enum_vals = [v.name for v in enums[column.type.name].items]
                    entry[col_name] = random.choice(enum_vals)
                    continue

                # 4. Data Generation
                # We use a 'while' loop for single-column uniques
                while True:
                    if lname in ALL_SIMPLE_TYPES:
                        val = ALL_SIMPLE_TYPES[lname]()
                    elif col_type in DATE_TYPES:
                        val = DATE_TYPES[col_type]()
                    elif col_type in ALL_NUMERICAL_TYPES:
                        val = ALL_NUMERICAL_TYPES[col_type](1, 1000)
                    elif col_type in ALL_SIMPLE_TYPES:
                        val = ALL_SIMPLE_TYPES[col_type]()
                    else:
                        val = fake.unique.slug()

                    if not column.unique:
                        entry[col_name] = val
                        break

                    ukey = unique_key(table.name, col_name)
                    USED_UNIQUE_VALUES.setdefault(ukey, set())
                    if val not in USED_UNIQUE_VALUES[ukey]:
                        USED_UNIQUE_VALUES[ukey].add(val)
                        entry[col_name] = val
                        break

            # --- COMPOSITE VALIDATION ---
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

            if is_valid_composite:
                # Commit composite values to global state
                for ckey, c_val in current_combos:
                    USED_COMPOSITE_UNIQUES[ckey].add(c_val)
                table_data[row_id] = entry
                success = True
                break

        if not success:
            print(
                f"Warning: Failed to generate unique row for {table.name} after 100 attempts."
            )

    generated_data[table.name] = table_data
    save_json(table.name, table_data)


# ======================================================
# MAIN
# ======================================================


def main():
    with open("schema.dbml") as f:
        dbml = PyDBML.parse_file(f)

    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    enums = {e.name: e for e in dbml.enums}
    fk_map = build_fk_map(dbml.refs)
    tables = topo_sort_tables(dbml.tables, fk_map)

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
        )


if __name__ == "__main__":
    main()
