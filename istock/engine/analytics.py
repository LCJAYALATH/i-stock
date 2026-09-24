"""Inventory analytics: ABC classification, turnover, dead stock, expiry."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import Batch, Item, StockTransaction
from ..settings import get_setting
from .replenishment import _as_date, daily_sales_bulk


def net_movement_bulk(item_ids, days, end=None):
    """{item_id: [net signed movement per day]} for all transaction types."""
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    out = {i: [0] * days for i in item_ids}
    if not item_ids:
        return out
    day_col = func.date(StockTransaction.created_at)
    rows = (db.session.query(StockTransaction.item_id, day_col, func.sum(StockTransaction.quantity))
            .filter(StockTransaction.item_id.in_(list(item_ids)),
                    StockTransaction.created_at >= datetime.combine(start, time.min))
            .group_by(StockTransaction.item_id, day_col).all())
    for item_id, day, qty in rows:
        idx = (_as_date(day) - start).days
        if 0 <= idx < days:
            out[item_id][idx] = int(qty or 0)
    return out


def closing_balances(current_on_hand, net_per_day):
    """Rebuild daily closing stock by walking the ledger backwards from today."""
    balances = [0] * len(net_per_day)
    bal = current_on_hand
    for i in range(len(net_per_day) - 1, -1, -1):
        balances[i] = bal
        bal -= net_per_day[i]
    return balances


def inventory_overview(days=None):
    """Per-item analytics rows plus portfolio totals."""
    days = days or get_setting("history_days")
    items = Item.query.filter_by(is_active=True).all()
    ids = [i.id for i in items]
    sales = daily_sales_bulk(ids, days)
    net = net_movement_bulk(ids, days)
    rows = []
    for item in items:
        units = sum(sales[item.id])
        cost = float(item.unit_cost or 0)
        price = float(item.unit_price or 0)
        balances = closing_balances(item.quantity_on_hand or 0, net[item.id])
        avg_on_hand = sum(balances) / len(balances) if balances else 0
        turnover = (units / avg_on_hand) * (365.0 / days) if avg_on_hand > 0 else None
        rows.append({
            "item": item,
            "units_sold": units,
            "revenue": units * price,
            "cogs": units * cost,
            "annual_value": units * cost * 365.0 / days,
            "avg_on_hand": avg_on_hand,
            "turnover": turnover,
            "days_inventory": (365.0 / turnover) if turnover else None,
            "series": sales[item.id],
        })

    # ABC classification by annual consumption value (Pareto 80/15/5)
    total_value = sum(r["annual_value"] for r in rows) or 1
    cumulative = 0.0
    for r in sorted(rows, key=lambda r: r["annual_value"], reverse=True):
        cumulative += r["annual_value"]
        share = cumulative / total_value
        r["abc"] = "A" if share <= 0.80 or cumulative == r["annual_value"] else ("B" if share <= 0.95 else "C")
        r["value_share"] = r["annual_value"] / total_value * 100
    return rows


def portfolio_daily_sales(days=30):
    """Total units and revenue sold per day across all items."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    day_col = func.date(StockTransaction.created_at)
    rows = (db.session.query(day_col, func.sum(StockTransaction.quantity),
                             func.sum(StockTransaction.quantity * StockTransaction.unit_value))
            .filter(StockTransaction.txn_type == "OUT",
                    StockTransaction.created_at >= datetime.combine(start, time.min))
            .group_by(day_col).all())
    units = [0] * days
    revenue = [0.0] * days
    for day, qty, value in rows:
        idx = (_as_date(day) - start).days
        if 0 <= idx < days:
            units[idx] = -int(qty or 0)
            revenue[idx] = -float(value or 0)
    labels = [(start + timedelta(days=i)).strftime("%d %b") for i in range(days)]
    return labels, units, revenue


def dead_stock(days=None):
    days = days or get_setting("dead_stock_days")
    cutoff = datetime.now() - timedelta(days=days)
    last_sale = dict(db.session.query(StockTransaction.item_id, func.max(StockTransaction.created_at))
                     .filter(StockTransaction.txn_type == "OUT")
                     .group_by(StockTransaction.item_id).all())
    out = []
    for item in Item.query.filter(Item.is_active.is_(True), Item.quantity_on_hand > 0).all():
        last = last_sale.get(item.id)
        if last is not None and not isinstance(last, datetime):
            last = datetime.fromisoformat(str(last))
        if last is None or last < cutoff:
            out.append({"item": item, "last_sale": last})
    return out


def expiring_batches(days=None):
    days = days if days is not None else get_setting("expiry_warning_days")
    limit = date.today() + timedelta(days=days)
    return (Batch.query.join(Item)
            .filter(Batch.quantity > 0, Batch.expiry_date.isnot(None), Batch.expiry_date <= limit,
                    Item.is_active.is_(True))
            .order_by(Batch.expiry_date).all())
