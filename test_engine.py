"""Unit tests for seeder generators — do not require schema.dbml or data/."""

from datetime import datetime

from seeder_engine.datetime_generator import generate_birth_date, later_than
from seeder_engine.field_types import (
    configure_generators,
    generate_value,
    match_name_generator,
    parse_sql_type,
)


def setup_function():
    configure_generators(1)


def test_parse_sql_type_strips_parameters():
    assert parse_sql_type("decimal(18,2)") == ("decimal", (18, 2))
    assert parse_sql_type("varchar(50)") == ("varchar", (50,))
    assert parse_sql_type("DECIMAL(12,2)") == ("decimal", (12, 2))
    assert parse_sql_type("int") == ("int", ())
    assert parse_sql_type("bigint") == ("bigint", ())


def test_capacity_does_not_match_city():
    assert match_name_generator("bed_capacity") is None
    assert match_name_generator("capacity") is None
    assert match_name_generator("city") is not None


def test_key_does_not_match_key_cards():
    assert match_name_generator("key_cards_issued") is None
    assert match_name_generator("space_key") is not None
    assert match_name_generator("key") is not None


def test_decimal_with_precision_is_numeric():
    val = generate_value("current_balance", "decimal(18,2)")
    assert isinstance(val, (int, float))
    assert float(val) >= 0


def test_int_capacity_is_int():
    val = generate_value("bed_capacity", "int")
    assert isinstance(val, int)
    val = generate_value("key_cards_issued", "int")
    assert isinstance(val, int)


def test_identifier_columns_are_not_slugs():
    number = generate_value("employee_number", "varchar(50)")
    assert isinstance(number, str)
    assert number.replace("-", "").isalnum()
    assert " " not in number
    iban = generate_value("iban", "varchar(34)")
    assert isinstance(iban, str) and len(iban) >= 8
    currency = generate_value("currency", "varchar(10)")
    assert currency.isalpha() and currency.isupper() and len(currency) == 3


def test_date_of_birth_is_an_adult_date():
    raw = generate_value("date_of_birth", "date")
    born = datetime.fromisoformat(raw)
    age = (datetime.now() - born).days / 365
    assert 18 <= age <= 81
    assert "T" not in raw


def test_date_expiry_stays_a_date():
    raw = generate_value("license_expiry", "date")
    assert len(raw) == 10
    assert "T" not in raw


def test_geographic_fields_are_consistent():
    from seeder_engine.main import GEOGRAPHY_PROFILES, apply_geographic_consistency
    from seeder_engine.schema_ir import Column, DataType

    names = ("country", "state", "city", "postal_code")
    columns = tuple(Column(name=name, data_type=DataType(name="string")) for name in names)
    entry = {name: "unrelated" for name in names}

    apply_geographic_consistency(entry, columns)

    locations = {
        (profile["country"], state, city, postal)
        for profile in GEOGRAPHY_PROFILES
        for state, city, postal in profile["regions"]
    }
    assert (
        entry["country"],
        entry["state"],
        entry["city"],
        entry["postal_code"],
    ) in locations


def test_temporal_names_do_not_generate_sentence_text():
    raw = generate_value("created_at", "varchar(40)")

    assert "T" in raw
    assert datetime.fromisoformat(raw)


def test_later_than_can_emit_a_date():
    later = later_than("2024-01-01", as_date=True)
    assert len(later) == 10
    assert later >= "2024-01-01"


def test_birth_date_helper():
    raw = generate_birth_date(min_age=18, max_age=80)
    assert len(raw) == 10
