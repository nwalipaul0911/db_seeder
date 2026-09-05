from seeder_engine.dbml_adapter import DBMLAdapter


DBML = """
Enum status {
  active
  inactive
}

Table users {
  id int [pk, increment]
  status status [not null, default: 'active']
  email varchar [unique]
}

Table posts {
  id int [pk]
  user_id int
}

Ref: posts.user_id > users.id
"""


def test_dbml_adapter_builds_canonical_schema():
    schema = DBMLAdapter().parse(DBML)

    assert schema.metadata["source_format"] == "dbml"
    assert schema.enum_map["status"].values == ("active", "inactive")
    assert schema.table_map["users"].primary_key == ("id",)
    assert schema.table_map["users"].columns[1].data_type.enum_name == "status"
    assert schema.table_map["users"].columns[2].unique is True
    assert schema.relationships[0].source_table == "posts"
    assert schema.relationships[0].target_table == "users"
    assert schema.table_map["posts"].columns[1].references.target_columns == ("id",)


def test_dbml_adapter_exposes_capabilities():
    capabilities = DBMLAdapter().capabilities()

    assert capabilities["foreign_keys"] == "supported"
    assert capabilities["check_constraints"] == "partial"
