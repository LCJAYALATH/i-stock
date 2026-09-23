"""Automated & predictive replenishment engine (database-aware layer).

Data Collection -> Data Processing -> Demand Forecasting -> Stock-Level
Analysis -> Reorder Decision -> Purchase Recommendation

`recalculate_item` is called automatically after every stock movement and
whenever a supplier's lead time changes, so the safety stock and ROP always
reflect the latest sales velocity.
"""
import math
from datetime import date, datetime, time, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import (PO_OPEN_STATUSES, Item, PurchaseOrder, PurchaseOrderLine,
                      StockTransaction, Supplier, now)
from ..settings import get_setting
from . import forecasting as fc

ENGINE_KEYS = ("history_days", "forecast_horizon", "sma_window", "backtest_days",
               "default_service_level", "default_lead_time", "review_period",
               "ordering_cost", "holding_rate", "overstock_days", "dead_stock_days", "auto_draft_po")


def engine_settings():
    return {k: get_setting(k) for k in ENGINE_KEYS}


# --------------------------------------------------------------------------- #
# Data collection
# --------------------------------------------------------------------------- #

def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def daily_sales_bulk(item_ids, days, end=None):
    """Return {item_id: [units sold per day]} for the `days` days ending `end`."""
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    series = {i: [0] * days for i in item_ids}
    if not item_ids:
        return series
    day_col = func.date(StockTransaction.created_at)
    rows = (db.session.query(StockTransaction.item_id, day_col, func.sum(StockTransaction.quantity))
            .filter(StockTransaction.item_id.in_(list(item_ids)),
                    StockTransaction.txn_type == "OUT",
                    StockTransaction.created_at >= datetime.combine(start, time.min),
                    StockTransaction.created_at < datetime.combine(end + timedelta(days=1), time.min))
            .group_by(StockTransaction.item_id, day_col)
            .all())
    for item_id, day, qty in rows:
        idx = (_as_date(day) - start).days
        if 0 <= idx < days:
            series[item_id][idx] = -int(qty or 0)  # OUT quantities are stored negative
    return series


def daily_sales(item_id, days, end=None):
    return daily_sales_bulk([item_id], days, end)[item_id]


def on_order_bulk(item_ids=None):
    """Outstanding quantities on purchase orders already sent to suppliers."""
    q = (db.session.query(PurchaseOrderLine.item_id,
                          func.sum(PurchaseOrderLine.quantity_ordered - PurchaseOrderLine.quantity_received))
         .join(PurchaseOrder)
         .filter(PurchaseOrder.status.in_(("SENT", "PARTIAL"))))
    if item_ids is not None:
        q = q.filter(PurchaseOrderLine.item_id.in_(list(item_ids)))
    return {item_id: int(qty or 0) for item_id, qty in q.group_by(PurchaseOrderLine.item_id).all()}


# --------------------------------------------------------------------------- #
# Processing and decision
# --------------------------------------------------------------------------- #

def lead_time_for(item, cfg):
    if item.lead_time_override:
        return float(item.lead_time_override), 0.0
    if item.supplier and item.supplier.lead_time_days:
        return float(item.supplier.lead_time_days), float(item.supplier.lead_time_std or 0)
    return float(cfg["default_lead_time"]), 0.0


def analyse(item, series, on_order, cfg):
    """Run the full decision pipeline for one item and return all figures."""
    lead_time, lead_time_std = lead_time_for(item, cfg)
    service_level = item.service_level or cfg["default_service_level"]
    window = cfg["sma_window"]
    horizon = cfg["forecast_horizon"]

    # Ignore leading zeros before the item's first ever sale (new items).
    first_sale = next((i for i, v in enumerate(series) if v), None)
    active = series[first_sale:] if first_sale is not None else []

    avg = fc.mean(active)
    sigma = fc.stdev(active)
    bt = fc.backtest(active, holdout=cfg["backtest_days"], window=window)
    model = bt["best"]
    forecast_daily = fc.forecast(active, max(horizon, int(math.ceil(lead_time + cfg["review_period"])) + 1),
                                 model=model, window=window)

    if len(active) >= 14:
        ss = fc.safety_stock(sigma, lead_time, service_level, avg, lead_time_std)
        ss_method = "statistical"
    else:  # not enough history: fall back to half of lead-time demand
        ss = 0.5 * avg * lead_time
        ss_method = "fallback"
    ss_calc = ss
    ss = max(ss, item.min_stock or 0)
    rop = fc.reorder_point(avg, lead_time, ss)

    on_hand = item.quantity_on_hand or 0
    rec_qty = fc.recommended_order_qty(forecast_daily, lead_time, cfg["review_period"], ss, on_hand, on_order)
    unit_cost = float(item.unit_cost or 0)
    annual = avg * 365
    holding_cost = unit_cost * cfg["holding_rate"]
    eoq = fc.eoq(annual, cfg["ordering_cost"], holding_cost)
    cover_days = lead_time + cfg["review_period"]
    days_cover = (on_hand / avg) if avg > 0 else None
    intercept, slope, r2 = fc.linear_regression(active) if active else (0, 0, 0)

    return {
        "lead_time": lead_time,
        "lead_time_std": lead_time_std,
        "service_level": service_level,
        "z": fc.z_score(service_level),
        "history_points": len(active),
        "avg_daily_sales": avg,
        "demand_std": sigma,
        "safety_stock": int(math.ceil(ss)),
        "ss_method": ss_method,
        "ss_calc": ss_calc,
        "ss_value": ss,
        "min_floor_applied": bool(item.min_stock) and item.min_stock >= ss_calc,
        "cover_days": cover_days,
        "cover_demand": fc.cover_demand(forecast_daily, cover_days),
        "annual_demand": annual,
        "ordering_cost": cfg["ordering_cost"],
        "holding_cost": holding_cost,
        "reorder_point": int(math.ceil(rop)),
        "forecast_daily": forecast_daily[:horizon],
        "forecast_total": sum(forecast_daily[:horizon]),
        "model": model,
        "backtest": bt,
        "trend_slope": slope,
        "r2": r2,
        "on_hand": on_hand,
        "on_order": on_order,
        "recommended_qty": rec_qty,
        "eoq": int(round(eoq)),
        "days_cover": days_cover,
    }


def stock_status(item, overstock_days=None):
    """Classify an item using its cached engine values."""
    on_hand = item.quantity_on_hand or 0
    if on_hand <= 0:
        return "OUT"
    if item.safety_stock and on_hand <= item.safety_stock:
        return "CRITICAL"
    if item.reorder_point and on_hand <= item.reorder_point:
        return "REORDER"
    if overstock_days is None:
        overstock_days = get_setting("overstock_days")
    if item.avg_daily_sales > 0 and on_hand / item.avg_daily_sales > overstock_days:
        return "OVERSTOCK"
    if item.avg_daily_sales == 0 and on_hand > 0 and item.metrics_updated_at:
        return "NO_SALES"
    return "OK"


STATUS_META = {
    "OUT": ("Out of stock", "danger"),
    "CRITICAL": ("Below safety stock", "danger"),
    "REORDER": ("At reorder point", "warning"),
    "OVERSTOCK": ("Overstock", "info"),
    "NO_SALES": ("No recent sales", "secondary"),
    "OK": ("Healthy", "success"),
}


def recalculate_item(item, cfg=None, series=None, on_order=None, auto_po=True):
    cfg = cfg or engine_settings()
    if series is None:
        series = daily_sales(item.id, cfg["history_days"])
    if on_order is None:
        on_order = on_order_bulk([item.id]).get(item.id, 0)
    result = analyse(item, series, on_order, cfg)
    item.avg_daily_sales = round(result["avg_daily_sales"], 4)
    item.demand_std = round(result["demand_std"], 4)
    item.safety_stock = result["safety_stock"]
    item.reorder_point = result["reorder_point"]
    item.forecast_30d = round(result["forecast_total"], 2)
    item.forecast_model = result["model"]
    item.recommended_qty = result["recommended_qty"]
    item.metrics_updated_at = now()
    if auto_po and cfg["auto_draft_po"]:
        ensure_auto_po(item)
    return result


def recalculate_items(items, auto_po=True):
    cfg = engine_settings()
    ids = [i.id for i in items]
    series = daily_sales_bulk(ids, cfg["history_days"])
    on_order = on_order_bulk(ids)
    for item in items:
        recalculate_item(item, cfg, series[item.id], on_order.get(item.id, 0), auto_po=auto_po)


def recalculate_all(auto_po=True):
    items = Item.query.filter_by(is_active=True).all()
    recalculate_items(items, auto_po=auto_po)
    db.session.commit()
    return len(items)


# --------------------------------------------------------------------------- #
# Purchase recommendation
# --------------------------------------------------------------------------- #

def next_number(prefix, model, column):
    stamp = date.today().strftime("%Y%m%d")
    base = "%s-%s-" % (prefix, stamp)
    last = (db.session.query(column).filter(column.like(base + "%"))
            .order_by(column.desc()).first())
    seq = int(last[0].rsplit("-", 1)[1]) + 1 if last else 1
    return "%s%03d" % (base, seq)


def open_po_line_for(item_id):
    return (PurchaseOrderLine.query.join(PurchaseOrder)
            .filter(PurchaseOrderLine.item_id == item_id,
                    PurchaseOrder.status.in_(PO_OPEN_STATUSES))
            .first())


def ensure_auto_po(item):
    """Draft (or extend) a supplier PO when an item reaches its reorder point."""
    if not (item.is_active and item.supplier_id and item.reorder_point > 0):
        return None
    if (item.quantity_on_hand or 0) > item.reorder_point:
        return None
    if open_po_line_for(item.id):
        return None  # already being replenished
    qty = max(item.recommended_qty, 1)
    po = (PurchaseOrder.query.filter_by(supplier_id=item.supplier_id, status="DRAFT", auto_generated=True)
          .order_by(PurchaseOrder.id.desc()).first())
    if po is None:
        po = PurchaseOrder(po_number=next_number("PO", PurchaseOrder, PurchaseOrder.po_number),
                           supplier_id=item.supplier_id, status="DRAFT", auto_generated=True,
                           notes="Auto-generated by the replenishment engine")
        db.session.add(po)
        db.session.flush()
    po.lines.append(PurchaseOrderLine(item_id=item.id, quantity_ordered=qty, unit_cost=item.unit_cost or 0))
    db.session.flush()
    return po


def generate_reorder_pos():
    """Recalculate everything and draft POs for every item at/below its ROP."""
    before = {po.id for po in PurchaseOrder.query.filter_by(status="DRAFT").all()}
    lines_before = PurchaseOrderLine.query.count()
    recalculate_all(auto_po=True)
    added = PurchaseOrderLine.query.count() - lines_before
    pos = PurchaseOrder.query.filter_by(status="DRAFT", auto_generated=True).all()
    new_pos = [po for po in pos if po.id not in before]
    return added, new_pos


def update_supplier_lead_time(supplier, min_samples=3, window=10):
    """Learn a supplier's real lead time from received purchase orders."""
    pos = (PurchaseOrder.query
           .filter(PurchaseOrder.supplier_id == supplier.id,
                   PurchaseOrder.sent_at.isnot(None),
                   PurchaseOrder.received_at.isnot(None))
           .order_by(PurchaseOrder.received_at.desc()).limit(window).all())
    samples = [(po.received_at - po.sent_at).total_seconds() / 86400.0 for po in pos]
    samples = [s for s in samples if s >= 0]
    if len(samples) < min_samples:
        return False
    supplier.lead_time_days = round(fc.mean(samples), 2)
    supplier.lead_time_std = round(fc.stdev(samples), 2)
    return True


def recalculate_supplier_items(supplier):
    items = Item.query.filter_by(supplier_id=supplier.id, is_active=True).all()
    recalculate_items(items)
