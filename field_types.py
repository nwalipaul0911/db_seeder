from faker import Faker
import random

# Assuming 'datetime_generator' has the functions:
# generate_date() -> returns a date string 'YYYY-MM-DD'
# generate_timestamp() -> returns a datetime string 'YYYY-MM-DD HH:MM:SS'
from datetime_generator import generate_date, generate_timestamp

# Initialize Faker with a locale for richer data
fake = Faker("en_US")

# --- 1. Primitive and Generic Types ---
# These handle standard text, boolean, and UUID types.
primitive_types = {
    "string": lambda: fake.word().capitalize(),
    "varchar": lambda: fake.unique.slug(),
    "text": lambda: fake.sentence(),
    "bool": lambda: random.choice([True, False]),
    "boolean": lambda: random.choice([True, False]),
    "uuid": lambda: str(fake.uuid4()),  # Standard UUID
    "id": lambda: random.randint(1000, 9999999),  # Generic ID / Primary Key
    "binary": lambda: fake.sha1(raw_output=False)[:20],  # Example of binary/hash data
    "blob": lambda: fake.text(max_nb_chars=50),
}

# --- 2. Numerical Types ---
# Requires min and max bounds for meaningful generation.
numerical_types = {
    "int": lambda a, b: random.randint(a, b),
    "integer": lambda a, b: random.randint(a, b),
    "serial": lambda a, b: random.randint(a, b),
    "float": lambda a, b: round(random.uniform(a, b), 4),
    "double": lambda a, b: round(random.uniform(a, b), 8),
    "decimal": lambda a, b: round(random.uniform(a, b), 2),
    "numeric": lambda a, b: round(random.uniform(a, b), 4),
    "money": lambda a, b: round(random.uniform(a, b), 2),
}

# --- 3. Date and Time Types ---
# Leveraging your datetime_generator
DATE_TYPES = {
    "date": generate_date,
    "datetime": generate_timestamp,
    "timestamp": generate_timestamp,  # Often interchangeable with datetime
    "time": lambda: fake.time(pattern="%H:%M:%S", end_datetime=None),
}

# --- 4. Specialized/Domain-Specific Types (Leveraging Faker Providers) ---
# These are often derived from the column NAME in a schema (e.g., 'email_address')
# or represent common data types not covered by primitives.
specialized_types = {
    "name": lambda: fake.name(),
    "firstname": lambda: fake.first_name(),
    "first name": lambda: fake.first_name(),
    "first_name": lambda: fake.first_name(),
    "lastname": lambda: fake.last_name(),
    "last name": lambda: fake.last_name(),
    "last_name": lambda: fake.last_name(),
    "username": lambda: fake.last_name(),
    "full name": lambda: fake.name(),
    "fullname": lambda: fake.name(),
    "full_name": lambda: fake.name(),
    "email": lambda: fake.unique.email(),
    "phone": lambda: fake.unique.phone_number(),
    "address": lambda: fake.address(),
    "city": lambda: fake.city(),
    "state": lambda: fake.state(),
    "country": lambda: fake.country(),
    "postcode": lambda: fake.postcode(),
    "url": lambda: fake.url(),
    "ipaddress": lambda: fake.ipv4(),
    "macaddress": lambda: fake.mac_address(),
    "creditcard": lambda: fake.credit_card_number(),
    "useragent": lambda: fake.user_agent(),
    "json": lambda: {
        "key": fake.word(),
        "value": random.randint(1, 100),
    },  # Basic JSON object
}

debate = 34

ALL_SIMPLE_TYPES = {
    **primitive_types,
    **specialized_types,
}

ALL_NUMERICAL_TYPES = numerical_types
