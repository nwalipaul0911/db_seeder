import json
import random
import re
from faker import Faker

from datetime_generator import (
    generate_birth_date,
    generate_date,
    generate_timestamp,
    in_the_future,
)

fake = Faker("en_US")

DATE_TYPES = {
    "date": generate_date,
    "datetime": generate_timestamp,
    "timestamp": generate_timestamp,
    "timestamptz": generate_timestamp,
    "time": lambda: fake.time(pattern="%H:%M:%S", end_datetime=None),
}

INTEGER_TYPES = {
    "int",
    "integer",
    "serial",
    "bigserial",
    "smallserial",
    "bigint",
    "smallint",
}

NUMERICAL_TYPES = INTEGER_TYPES | {
    "float",
    "double",
    "decimal",
    "numeric",
    "money",
    "real",
}

TEXT_TYPES = {
    "varchar",
    "char",
    "character",
    "string",
    "text",
    "citext",
    "clob",
}

BOOL_TYPES = {"bool", "boolean"}

TYPE_PARAM_RE = re.compile(
    r"^([a-zA-Z][a-zA-Z0-9_ ]+?)(?:\s*\(([^)]*)\))?$"
)

ISO_CURRENCIES = (
    "USD",
    "EUR",
    "GBP",
    "NGN",
    "CAD",
    "AUD",
    "JPY",
    "CHF",
    "INR",
    "ZAR",
    "KES",
    "GHS",
)

GENERIC_STATUS = (
    "pending",
    "active",
    "inactive",
    "completed",
    "cancelled",
    "draft",
    "open",
    "closed",
    "failed",
)

_NAME_GENERATORS = None

# Tokens that must be the whole column or a suffix, never a leading fragment.
# "key" matches space_key, not key_cards_issued.
_SUFFIX_ONLY = {"key"}


def parse_sql_type(col_type: str):
    """Split `decimal(18,2)` / `varchar(50)` into (`decimal`, (18, 2))."""
    raw = (col_type or "").strip().lower()
    if not raw:
        return "", ()
    match = TYPE_PARAM_RE.match(raw)
    if not match:
        return raw.split()[0], ()
    base = match.group(1).strip().replace(" ", "")
    if base in {"doubleprecision"}:
        base = "double"
    if base in {"charactervarying"}:
        base = "varchar"
    args = []
    if match.group(2):
        for part in match.group(2).split(","):
            part = part.strip()
            if part.isdigit():
                args.append(int(part))
    return base, tuple(args)


def configure_generators(seed=42):
    """Seed every generator used by a run. Call once from main()."""
    global fake, _NAME_GENERATORS
    random.seed(seed)
    Faker.seed(seed)
    fake = Faker("en_US")
    fake.unique.clear()
    _NAME_GENERATORS = None


def _slug():
    try:
        return fake.unique.slug()
    except Exception:
        return f"{fake.slug()}-{fake.hexify(text='^^^^^^^^')}"


def _json_object():
    return {
        fake.word(): random.choice(
            [fake.word(), random.randint(1, 100), True, False]
        )
        for _ in range(random.randint(2, 5))
    }


def _code():
    return fake.bothify(text="??-######").upper()


def _number_code():
    return fake.numerify(text="##########")


def _name_generators():
    global _NAME_GENERATORS
    if _NAME_GENERATORS is not None:
        return _NAME_GENERATORS
    _NAME_GENERATORS = {
        "email": lambda: fake.unique.email(),
        "email_address": lambda: fake.unique.email(),
        "phone": lambda: fake.unique.phone_number(),
        "phone_number": lambda: fake.unique.phone_number(),
        "first_name": lambda: fake.first_name(),
        "firstname": lambda: fake.first_name(),
        "last_name": lambda: fake.last_name(),
        "lastname": lambda: fake.last_name(),
        "full_name": lambda: fake.name(),
        "fullname": lambda: fake.name(),
        "display_name": lambda: fake.name(),
        "username": lambda: fake.user_name(),
        "user_name": lambda: fake.user_name(),
        "name": lambda: fake.catch_phrase(),
        "title": lambda: fake.sentence(nb_words=random.randint(4, 8)).rstrip("."),
        "job_title": lambda: fake.job(),
        "job": lambda: fake.job(),
        "gender": lambda: random.choice(
            ["female", "male", "non_binary", "unspecified"]
        ),
        "subject": lambda: fake.sentence(nb_words=5).rstrip("."),
        "key": _code,
        "slug": _slug,
        "purpose": lambda: fake.sentence(),
        "description": lambda: fake.paragraph(nb_sentences=2),
        "details": lambda: fake.sentence(),
        "note": lambda: fake.sentence(),
        "comment": lambda: fake.sentence(),
        "snapshot": lambda: fake.paragraph(nb_sentences=3),
        "content": lambda: fake.paragraph(nb_sentences=3),
        "body": lambda: fake.paragraph(nb_sentences=3),
        "config": lambda: json.dumps(_json_object()),
        "settings": lambda: json.dumps(_json_object()),
        "payload": lambda: json.dumps(_json_object()),
        "metadata": lambda: json.dumps(_json_object()),
        "json": lambda: _json_object(),
        "url": lambda: fake.url(),
        "uri": lambda: fake.url(),
        "destination": lambda: fake.url(),
        "path": lambda: fake.uri_path(),
        "address": lambda: fake.address(),
        "city": lambda: fake.city(),
        "state": lambda: fake.state(),
        "country": lambda: fake.country(),
        "postcode": lambda: fake.postcode(),
        "zipcode": lambda: fake.postcode(),
        "ipaddress": lambda: fake.ipv4(),
        "ip_address": lambda: fake.ipv4(),
        "macaddress": lambda: fake.mac_address(),
        "mac_address": lambda: fake.mac_address(),
        "creditcard": lambda: fake.credit_card_number(),
        "credit_card": lambda: fake.credit_card_number(),
        "useragent": lambda: fake.user_agent(),
        "user_agent": lambda: fake.user_agent(),
        "account": lambda: fake.bothify(text="acc-########"),
        "iban": lambda: fake.iban(),
        "sku": lambda: fake.bothify(text="SKU-#####").upper(),
        "vin": lambda: fake.bothify(text="????????#########").upper(),
        "currency": lambda: random.choice(ISO_CURRENCIES),
        "uuid": lambda: str(fake.uuid4()),
        "license": _code,
        "number": _number_code,
        "code": _code,
        "reference": _code,
        "handle": lambda: fake.user_name(),
    }
    return _NAME_GENERATORS


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _has_token_sequence(tokens, key_tokens):
    if not key_tokens or len(key_tokens) > len(tokens):
        return False
    n = len(key_tokens)
    for i in range(len(tokens) - n + 1):
        if tokens[i : i + n] == key_tokens:
            return True
    return False


def match_name_generator(col_name: str):
    """Return a generator for a column name using token-boundary matches only."""
    n = _normalize(col_name)
    gens = _name_generators()
    if n in gens:
        return gens[n]

    tokens = [t for t in n.split("_") if t]
    candidates = []
    for key, gen in gens.items():
        key_tokens = key.split("_")
        if key in _SUFFIX_ONLY:
            if n == key or n.endswith(f"_{key}"):
                candidates.append((len(key), gen))
            continue
        if _has_token_sequence(tokens, key_tokens):
            candidates.append((len(key), gen))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


def _int_bounds(col_name: str):
    tokens = _normalize(col_name).split("_")
    n = _normalize(col_name)
    if any(tok in tokens for tok in ("priority", "rank", "weight")):
        return 0, 10
    if any(tok in tokens for tok in ("size", "bytes", "kb", "mb")):
        return 10, 50_000
    if any(tok in tokens for tok in ("count", "quantity", "qty", "cards", "beds")):
        return 1, 200
    if any(tok in tokens for tok in ("capacity",)):
        return 1, 500
    if any(tok in tokens for tok in ("position", "order", "sort", "index")):
        return 0, 50
    if "version" in n or n in {"revision", "rev"}:
        return 1, 20
    return 1, 100


def _is_money_name(col_name: str) -> bool:
    tokens = set(_normalize(col_name).split("_"))
    return bool(
        tokens
        & {
            "amount",
            "balance",
            "price",
            "cost",
            "fee",
            "premium",
            "salary",
            "revenue",
            "deductible",
            "payment",
            "charge",
            "total",
            "subtotal",
            "tax",
            "discount",
            "coverage",
            "limit",
        }
    )


def _is_rate_name(col_name: str) -> bool:
    tokens = set(_normalize(col_name).split("_"))
    return bool(tokens & {"rate", "percent", "apr", "interest"})


def _numeric_value(col_type: str, col_name: str, scale=None):
    lo, hi = _int_bounds(col_name)
    if col_type in INTEGER_TYPES:
        return random.randint(lo, hi)
    if _is_rate_name(col_name):
        digits = 4 if scale is None else scale
        return round(random.uniform(0.01, 25.0), digits)
    if _is_money_name(col_name) or col_type == "money":
        digits = 2 if scale is None else scale
        return round(random.uniform(1, 10_000), digits)
    digits = 4 if scale is None else scale
    if col_type == "double":
        digits = 8 if scale is None else scale
    if col_type in {"decimal", "numeric", "money"}:
        digits = 2 if scale is None else scale
    return round(random.uniform(lo, hi), digits)


def _identifier_value(col_name: str):
    n = _normalize(col_name)
    tokens = n.split("_")
    if "iban" in tokens:
        return fake.iban()
    if "vin" in tokens:
        return fake.bothify(text="????????#########").upper()
    if "sku" in tokens:
        return fake.bothify(text="SKU-#####").upper()
    if any(tok in tokens for tok in ("license", "licence")):
        return _code()
    if n.endswith("_number") or n == "number":
        return _number_code()
    if n.endswith("_code") or n == "code":
        return _code()
    if n.endswith("_reference") or tokens[-1:] == ["reference"]:
        return _code()
    return None


def generate_value(col_name: str, col_type: str):
    """Produce a value from column name and SQL type. Unique-ness is handled by the seeder."""
    n = _normalize(col_name)
    base, params = parse_sql_type(col_type)
    scale = params[1] if len(params) >= 2 else (params[0] if base in {"decimal", "numeric"} and len(params) == 1 else None)

    if "birth" in n.split("_") and base in DATE_TYPES:
        return generate_birth_date()

    if base in DATE_TYPES:
        as_date = base == "date"
        if any(tok in n for tok in ("expire", "expiry", "due", "valid_until")):
            return in_the_future(as_date=as_date)
        return DATE_TYPES[base]()

    if base in BOOL_TYPES:
        return random.choice([True, False])

    if base in NUMERICAL_TYPES:
        return _numeric_value(base, col_name, scale=scale)

    if base in {"uuid"}:
        return str(fake.uuid4())

    if base in {"json", "jsonb"}:
        named = match_name_generator(col_name)
        if named:
            val = named()
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except json.JSONDecodeError:
                    return _json_object()
            return val
        return _json_object()

    # Text and unknown types: name matchers are allowed here only.
    named = match_name_generator(col_name)
    if named:
        return named()

    ident = _identifier_value(col_name)
    if ident is not None:
        return ident

    tokens = n.split("_")
    if "status" in tokens:
        return random.choice(GENERIC_STATUS)
    if tokens[-1:] == ["type"] or n == "type":
        return fake.word()

    if base in {"text", "blob"}:
        return fake.sentence()

    if base in {"binary", "bytea"}:
        return fake.sha1(raw_output=False)[:20]

    if n.endswith("_id"):
        return fake.bothify(text="id-########")

    return _slug()


# Backwards-compatible aliases used by older call sites / docs.
ALL_SIMPLE_TYPES = {}
ALL_NUMERICAL_TYPES = {t: (lambda a, b: random.randint(a, b)) for t in NUMERICAL_TYPES}
