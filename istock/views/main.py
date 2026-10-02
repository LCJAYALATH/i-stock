"""Dashboard, camera scanning workflow and the stock ledger."""
from datetime import datetime, time, timedelta

from flask import (Blueprint, jsonify, render_template, request, send_file,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from ..engine.analytics import expiring_batches, portfolio_daily_sales
from ..engine.replenishment import STATUS_META, open_po_line_for, stock_status
from ..extensions import db
from ..models import (PO_OPEN_STATUSES, Client, Item, PurchaseOrder,
                      SalesOrder, StockTransaction)
from ..services import reports
from ..services.stock import StockError, issue_stock, receive_stock
from ..settings import get_setting
from ..utils import parse_date, parse_int

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def dashboard():
    items = Item.query.filter_by(is_active=True).all()
    overstock_days = get_setting("overstock_days")
    statuses = {i.id: stock_status(i, overstock_days) for i in items}
    alerts = [i for i in items if statuses[i.id] in ("OUT", "CRITICAL", "REORDER")]
    order = {"OUT": 0, "CRITICAL": 1, "REORDER": 2}
    alerts.sort(key=lambda i: (order[statuses[i.id]], i.quantity_on_hand - i.reorder_point))
    counts = {k: 0 for k in STATUS_META}
    for s in statuses.values():
        counts[s] += 1
    labels, units, revenue = portfolio_daily_sales(30)
    kpi = {
        "skus": len(items),
        "stock_value": sum(i.stock_value for i in items),
        "units_on_hand": sum(i.quantity_on_hand for i in items),
        "alerts": len(alerts),
        "out": counts["OUT"],
        "open_pos": PurchaseOrder.query.filter(PurchaseOrder.status.in_(PO_OPEN_STATUSES)).count(),
        "draft_pos": PurchaseOrder.query.filter_by(status="DRAFT").count(),
        "pending_orders": SalesOrder.query.filter_by(status="PENDING").count(),
        "units_30d": sum(units),
        "revenue_30d": sum(revenue),
    }
    on_po = {i.id: bool(open_po_line_for(i.id)) for i in alerts}
    recent = StockTransaction.query.order_by(StockTransaction.created_at.desc()).limit(8).all()
    top = sorted(items, key=lambda i: i.avg_daily_sales, reverse=True)[:6]
    return render_template("dashboard.html", kpi=kpi, alerts=alerts, statuses=statuses, counts=counts,
                           on_po=on_po, chart={"labels": labels, "units": units, "revenue": revenue},
                           expiring=expiring_batches()[:8], recent=recent, top=top)


# --------------------------------------------------------------------------- #
# Camera scanning
# --------------------------------------------------------------------------- #

@bp.route("/scan")
@login_required
def scan():
    clients = Client.query.order_by(Client.name).all()
    return render_template("scan.html", clients=clients, mode=request.args.get("mode", "OUT"))


def _item_json(item):
    status = stock_status(item)
    return {
        "id": item.id, "sku": item.sku, "barcode": item.barcode, "name": item.name, "unit": item.unit,
        "on_hand": item.quantity_on_hand, "reorder_point": item.reorder_point,
        "safety_stock": item.safety_stock, "location": item.location.code if item.location else None,
        "status": status, "status_label": STATUS_META[status][0], "status_color": STATUS_META[status][1],
        "url": url_for("inventory.item_detail", item_id=item.id),
        "batches": [{"id": b.id, "batch_no": b.batch_no, "qty": b.quantity,
                     "expiry": b.expiry_date.isoformat() if b.expiry_date else None}
                    for b in item.batches if b.quantity > 0],
    }


@bp.route("/api/items/lookup")
@login_required
def api_lookup():
    code = (request.args.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "No code supplied."}), 400
    item = Item.query.filter(or_(Item.barcode == code, Item.sku == code)).first()
    if item is None:
        return jsonify({"ok": False, "error": "No item matches code '%s'." % code}), 404
    return jsonify({"ok": True, "item": _item_json(item)})


@bp.route("/api/items/search")
@login_required
def api_search():
    q = (request.args.get("q") or "").strip()
    query = Item.query.filter_by(is_active=True)
    if q:
        like = "%" + q + "%"
        query = query.filter(or_(Item.sku.ilike(like), Item.name.ilike(like), Item.barcode.ilike(like)))
    return jsonify([{"id": i.id, "sku": i.sku, "name": i.name, "on_hand": i.quantity_on_hand,
                     "unit_price": float(i.unit_price), "unit_cost": float(i.unit_cost) if current_user.can_view_financials else None}
                    for i in query.order_by(Item.name).limit(20)])


@bp.route("/api/scan", methods=["POST"])
@login_required
def api_scan():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    item_id = parse_int(data.get("item_id"))
    try:
        if mode == "IN":
            receive_stock(item_id, data.get("qty"), current_user, batch_no=data.get("batch_no"),
                          expiry_date=parse_date(data.get("expiry_date")),
                          reference=(data.get("reference") or "").strip() or None, source="scan")
            verb = "Received"
        elif mode == "OUT":
            issue_stock(item_id, data.get("qty"), current_user, batch_id=parse_int(data.get("batch_id")),
                        client_id=parse_int(data.get("client_id")),
                        reference=(data.get("reference") or "").strip() or None, source="scan")
            verb = "Issued"
        else:
            return jsonify({"ok": False, "error": "Unknown mode."}), 400
    except StockError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    item = db.session.get(Item, item_id)
    msg = "%s %s x %s. New balance: %d." % (verb, data.get("qty"), item.sku, item.quantity_on_hand)
    if item.quantity_on_hand <= item.reorder_point:
        msg += " Stock is at or below the reorder point (%d)." % item.reorder_point
    return jsonify({"ok": True, "message": msg, "item": _item_json(item)})


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #

def _filtered_transactions():
    q = StockTransaction.query.join(Item)
    t = request.args.get("type")
    if t in ("IN", "OUT", "ADJUST"):
        q = q.filter(StockTransaction.txn_type == t)
    s = (request.args.get("q") or "").strip()
    if s:
        like = "%" + s + "%"
        q = q.filter(or_(Item.sku.ilike(like), Item.name.ilike(like), StockTransaction.reference.ilike(like)))
    d1, d2 = parse_date(request.args.get("from")), parse_date(request.args.get("to"))
    if d1:
        q = q.filter(StockTransaction.created_at >= datetime.combine(d1, time.min))
    if d2:
        q = q.filter(StockTransaction.created_at < datetime.combine(d2 + timedelta(days=1), time.min))
    item_id = parse_int(request.args.get("item_id"))
    if item_id:
        q = q.filter(StockTransaction.item_id == item_id)
    return q.order_by(StockTransaction.created_at.desc(), StockTransaction.id.desc())


@bp.route("/transactions")
@login_required
def transactions():
    page = _filtered_transactions().paginate(page=parse_int(request.args.get("page"), 1), per_page=50)
    return render_template("transactions.html", page=page)


@bp.route("/transactions/export")
@login_required
def transactions_export():
    buf = reports.transactions_xlsx(_filtered_transactions().limit(20000).all())
    return send_file(buf, as_attachment=True, download_name="transactions.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
