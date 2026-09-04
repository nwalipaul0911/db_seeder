from datetime import datetime, timedelta
import random


def as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None


def is_date_only(value) -> bool:
    """True when the stored value is an ISO date without a time component."""
    if value is None or isinstance(value, datetime):
        return False
    text = str(value)
    return len(text) == 10 and text[4] == "-" and "T" not in text


def default_range():
    """A two-year window ending now — used when callers omit bounds."""
    end = datetime.now()
    return end - timedelta(days=730), end


def generate_timestamp(start_date=None, end_date=None):
    """Random ISO timestamp between two datetimes (defaults to the last two years)."""
    start = as_datetime(start_date)
    end = as_datetime(end_date)
    if start is None or end is None:
        start, end = default_range()
    if end < start:
        start, end = end, start
    span = int((end - start).total_seconds())
    offset = random.randint(0, max(span, 0))
    return (start + timedelta(seconds=offset)).isoformat()


def generate_date(start_date=None, end_date=None):
    """Random ISO date between two datetimes (defaults to the last two years)."""
    start = as_datetime(start_date)
    end = as_datetime(end_date)
    if start is None or end is None:
        start, end = default_range()
    if end < start:
        start, end = end, start
    span_days = max((end.date() - start.date()).days, 0)
    return (start.date() + timedelta(days=random.randint(0, span_days))).isoformat()


def generate_birth_date(min_age=18, max_age=80):
    """ISO date for an adult-to-elder date of birth."""
    today = datetime.now()
    return generate_date(
        start_date=today - timedelta(days=max_age * 365),
        end_date=today - timedelta(days=min_age * 365),
    )


def later_than(earlier, end_date=None, min_seconds=1, as_date=False):
    """A value at or after `earlier`. Returns a date string when `as_date` is set."""
    start = as_datetime(earlier)
    if start is None:
        return generate_date() if as_date else generate_timestamp(end_date=end_date)
    end = as_datetime(end_date) or datetime.now()
    start = start + timedelta(seconds=min_seconds)
    if as_date:
        if end.date() < start.date():
            return start.date().isoformat()
        return generate_date(start_date=start, end_date=end)
    if end < start:
        return start.isoformat()
    return generate_timestamp(start_date=start, end_date=end)


def in_the_future(after=None, max_days=365, as_date=False):
    """A value after `after` (or now), used for expiry / due dates."""
    start = as_datetime(after) or datetime.now()
    start = start + timedelta(seconds=1)
    end = start + timedelta(days=max_days)
    if as_date:
        return generate_date(start_date=start, end_date=end)
    return generate_timestamp(start_date=start, end_date=end)
