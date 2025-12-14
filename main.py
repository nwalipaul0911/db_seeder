from pydbml import PyDBML
from pydbml.classes import Enum, Table, Reference
from typing import List, Dict
import random
import json
from faker import Faker
fake = Faker()
from data import load_data
import yaml

yaml_config = None
with open("config.yaml", 'r') as yaml_file:
    yaml_config = yaml.safe_load(yaml_file)





def id_generator():
    for i in range(1, 10000):
        yield str(i)

column_type_values = {
    "int": lambda: random.randint(1, 1000),
    "float": lambda: round(random.uniform(1.0, 1000.0), 2),
    "decimal": lambda: round(random.uniform(1.0, 1000.0), 2),
    "string": lambda: fake.word(),
    "bool": lambda: random.choice([True, False]),
    "date": lambda: str(fake.date()),
    "datetime": lambda: str(fake.date_time()),
    "varchar": lambda: fake.word(),
    "text": lambda: fake.sentence(),
}
        


def seed_table(table: Table, enums: Dict, refs: List[Reference], entries):
    id_gen = id_generator()
    table_dict = {}
    table_name = table.name
    db = load_data()
    with open(f"data/{table_name}.json", "w") as file:
        for _ in range(entries):
            entry_id = next(id_gen)
            id_ref = f"{table_name[:-1] if table_name[-1] == "s" else table_name}_id"
            entry = {}
            
            
            for column in table.columns:
                sample_value = None
                if column.name.lower() == id_ref:
                    entry[id_ref] = entry_id
                    continue
                if isinstance(column.type, Enum):
                    enum_values = [i.name for i in enums[column.type.name].items]
                    sample_value = enum_values[random.randint(0, len(enum_values) - 1)] if enum_values else None
                    entry[column.name] = sample_value
                    continue
                elif column.name.lower().endswith("email"):
                    sample_value = fake.email()
                    entry[column.name] = sample_value
                    continue
                elif column.name.lower().endswith("name"):
                    sample_value = fake.name()
                    entry[column.name] = sample_value
                    continue
                elif column.name.lower().endswith("address"):
                    sample_value = fake.address()
                    entry[column.name] = sample_value
                    continue
                elif column.name.lower().endswith("id"):
                    sample_value = None
                    entry[column.name] = sample_value
                    continue
                elif column.name.lower().endswith("address"):
                    sample_value = fake.address()
                    entry[column.name] = sample_value
                    continue
                elif column.name.lower().endswith("phone"):
                    sample_value = fake.phone_number()
                    entry[column.name] = sample_value
                    continue
                elif column.type.lower() == "date":
                    sample_value = str(fake.date())
                    entry[column.name] = sample_value
                    continue
                elif "create" in column.name.lower() and column.type.lower() == "datetime":
                    sample_value = entry.get("updated_at", str(fake.date_time()))
                    entry[column.name] = sample_value
                    continue
                elif "updated" in column.name.lower() and column.type.lower() == "datetime":
                    sample_value = entry.get("created_at", str(fake.date_time()))
                    entry[column.name] = sample_value
                    continue
                elif "bool" in column.type.lower():
                    sample_value = random.choice([True, False])
                    entry[column.name] = sample_value
                    continue
                elif column.type.lower() in column_type_values:
                    sample_value = column_type_values[column.type.lower()]()
                    entry[column.name] = sample_value
                    continue
                else:
                    sample_value = fake.word()
                    entry[column.name] = sample_value
                
            table_dict[entry_id] = entry
        json.dump(table_dict, file, indent=4)


def main():  
    filename = "schema.dbml"
    with open(filename, 'r') as file:

        dbml = PyDBML.parse_file(file)
        tables = dbml.tables
        enums = dbml.enums
        refs = dbml.refs
        enums_dict = {enum.name: enum for enum in enums}
        for table in tables:
            default_count = yaml_config["Num_of_entries"].get("default", 100)
            entry_count = yaml_config["Num_of_entries"].get(table.name, default_count)
            print(entry_count)
            seed_table(table, enums_dict, refs, entries=entry_count)

if __name__ == "__main__":
    main()