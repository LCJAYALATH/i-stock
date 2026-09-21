"""Purchase-order and client-order workflows."""
from datetime import date, timedelta

from ..engine.replenishment import (recalculate_items, recalculate_supplier_items,
                                    update_supplier_lead_time)
from ..extensions import db
from ..models import Batch, now
from .stock import StockError, issue_stock, receive_stock


# --------------------------------------------------------------------------- #
# Purchase orders
# --------------------------------------------------------------------------- #

def send_po(po, user):
    if po.status != "DRAFT":
        raise StockError("Only draft purchase orders can be approved and sent.")
    if not po.lines:
        raise StockError("Add at least one line before sending.")
    po.status = "SENT"
    po.approved_by_id = user.id
    po.sent_at = now()
    po.expected_date = date.today() + timedelta(days=int(round(po.supplier.lead_time_days or 7)))
    db.session.flush()
    recalculate_items([l.item for l in po.lines], auto_po=False)  # on-order quantity changed
    db.session.commit()


def cancel_po(po):
    if po.status not in ("DRAFT", "SENT"):
        raise StockError("Only draft or sent purchase orders can be cancelled.")
    po.status = "CANCELLED"
    db.session.flush()
    recalculate_items([l.item for l in po.lines], auto_po=False)
    db.session.commit()


def receive_po(po, user, receipts):
    """receipts: list of dicts {line, qty, batch_no, expiry_date}."""
    if po.status not in ("SENT", "PARTIAL"):
        raise StockError("Only sent purchase orders can be received.")
    received_any = False
    try:
        for r in sorted(receipts, key=lambda r: r["line"].item_id):
            qty = int(r["qty"] or 0)
            if qty <= 0:
                continue
            line = r["line"]
            if qty > line.outstanding:
                raise StockError("Cannot receive %d of %s; only %d outstanding."
                                 % (qty, line.item.sku, line.outstanding))
            receive_stock(line.item_id, qty, user, batch_no=r.get("batch_no"), expiry_date=r.get("expiry_date"),
                          unit_cost=line.unit_cost, reference=po.po_number, source="po",
                          purchase_order_id=po.id, commit=False)
            line.quantity_received += qty
            received_any = True
        if not received_any:
            raise StockError("Enter a quantity for at least one line.")
        if all(l.outstanding == 0 for l in po.lines):
            po.status = "RECEIVED"
            po.received_at = now()
            db.session.flush()
            if update_supplier_lead_time(po.supplier):
                recalculate_supplier_items(po.supplier)
        else:
            po.status = "PARTIAL"
        db.session.flush()
        recalculate_items([l.item for l in po.lines])
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# --------------------------------------------------------------------------- #
# Client (sales) orders
# --------------------------------------------------------------------------- #

def picking_plan(order):
    """FEFO batch allocation for each line, sorted by bin location (walk order)."""
    plan = []
    for line in order.lines:
        remaining = line.quantity
        batches = (Batch.query.filter(Batch.item_id == line.item_id, Batch.quantity > 0)
                   .order_by(Batch.expiry_date.is_(None), Batch.expiry_date, Batch.received_at).all())
        allocations = []
        for b in batches:
            if remaining <= 0:
                break
            take = min(b.quantity, remaining)
            allocations.append({"batch": b, "qty": take})
            remaining -= take
        plan.append({"line": line, "allocations": allocations, "short": remaining,
                     "location": line.item.location.code if line.item.location else "ZZZ"})
    plan.sort(key=lambda p: p["location"])
    return plan


def dispatch_order(order, user):
    if order.status != "PENDING":
        raise StockError("Only pending orders can be dispatched.")
    if not order.lines:
        raise StockError("The order has no lines.")
    try:
        for line in sorted(order.lines, key=lambda l: l.item_id):  # consistent lock order
            issue_stock(line.item_id, line.quantity, user, client_id=order.client_id,
                        unit_price=line.unit_price, reference=order.order_number, source="order",
                        sales_order_id=order.id, commit=False)
        order.status = "DISPATCHED"
        order.dispatched_at = now()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
