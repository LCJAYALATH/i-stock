"""Typed access to system-wide settings stored in the `settings` table."""
from .extensions import db
from .models import Setting

DEFAULTS = {
    "company_name": ("i-STOCK Demo Distributors", str, "Company name printed on documents"),
    "company_address": ("No. 12, Main Street, Ratnapura", str, "Company address printed on documents"),
    "currency": ("Rs.", str, "Currency symbol"),
    "history_days": (90, int, "Days of sales history used for averages and forecasting"),
    "forecast_horizon": (30, int, "Forecast horizon (days)"),
    "sma_window": (7, int, "Moving-average window (days)"),
    "backtest_days": (14, int, "Hold-out days used to compare forecasting models"),
    "default_service_level": (0.95, float, "Target cycle service level (0.50 - 0.99)"),
    "default_lead_time": (7, float, "Lead time used when a supplier has none (days)"),
    "review_period": (7, float, "Review period added to lead time when sizing orders (days)"),
    "ordering_cost": (1500, float, "Cost of placing one order (for EOQ)"),
    "holding_rate": (0.20, float, "Annual holding cost as a fraction of unit cost (for EOQ)"),
    "overstock_days": (90, int, "Days of cover above which an item is flagged as overstock"),
    "dead_stock_days": (60, int, "Days without a sale before an item is flagged as dead stock"),
    "expiry_warning_days": (30, int, "Warn when a batch expires within this many days"),
    "auto_draft_po": (1, int, "Automatically draft a PO when stock falls to the ROP (1 = yes)"),
}


def get_setting(key):
    default, cast, _ = DEFAULTS[key]
    row = db.session.get(Setting, key)
    if row is None:
        return default
    try:
        return cast(row.value)
    except (TypeError, ValueError):
        return default


def all_settings():
    rows = {s.key: s.value for s in Setting.query.all()}
    out = {}
    for key, (default, cast, label) in DEFAULTS.items():
        try:
            value = cast(rows[key]) if key in rows else default
        except (TypeError, ValueError):
            value = default
        out[key] = {"value": value, "label": label, "type": cast.__name__}
    return out


def set_setting(key, value):
    _, cast, _ = DEFAULTS[key]
    cast(value)  # validate
    row = db.session.get(Setting, key)
    if row is None:
        db.session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
