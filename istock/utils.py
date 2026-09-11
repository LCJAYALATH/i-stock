from datetime import datetime
from decimal import Decimal, InvalidOperation


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_float(value, default=None):
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def parse_int(value, default=None):
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def parse_money(value, default=Decimal("0")):
    try:
        d = Decimal(str(value).strip()) if value not in (None, "") else default
    except (InvalidOperation, TypeError):
        return default
    return d if d >= 0 else default


def none_if_blank(value):
    value = (value or "").strip()
    return value or None
