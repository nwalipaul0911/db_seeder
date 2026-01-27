from datetime import timedelta, datetime
from faker import Faker
import random

fake = Faker()


def generate_timestamp(start_date=None, end_date=None):
    """Generates a random timestamp (ISO) between two datetimes."""
    if not start_date or not end_date:
        return datetime.now().isoformat()
    if isinstance(start_date, datetime) and isinstance(end_date, datetime):
        time_diff = end_date - start_date
        random_seconds = random.randint(0, int(time_diff.total_seconds()))
        return (start_date + timedelta(seconds=random_seconds)).isoformat()
    # If user passed date strings, attempt to parse
    try:
        sd = (
            start_date
            if isinstance(start_date, datetime)
            else datetime.fromisoformat(start_date)
        )
        ed = (
            end_date
            if isinstance(end_date, datetime)
            else datetime.fromisoformat(end_date)
        )
        time_diff = ed - sd
        random_seconds = random.randint(0, int(time_diff.total_seconds()))
        return (sd + timedelta(seconds=random_seconds)).isoformat()
    except Exception:
        return datetime.now().isoformat()


def generate_date(start_date=None, end_date=None):
    if not start_date:
        start_date = datetime.now()
    if not end_date:
        end_date = datetime.now()
    return fake.date_between(start_date=start_date, end_date=end_date).isoformat()
