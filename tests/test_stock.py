"""Integration tests for stock movements and the automated replenishment loop."""
from datetime import date, datetime, timedelta

import pytest

from istock.engine.replenishment import (recalculate_item, stock_status,
                                         update_supplier_lead_time)
from istock.models import (Batch, PurchaseOrder, PurchaseOrderLine,
                           SalesOrder, SalesOrderLine, StockTransaction)
from istock.services.orders import dispatch_order, receive_po, send_po
from istock.services.stock import (StockError, adjust_stock, issue_stock,
                                   receive_stock)


def _history(db, item, days=60, per_day=10, user=None):
    """Create sales history directly in the ledger."""
    receive_stock(item.id, days * per_day + 1000, user, batch_no="HIST")
    now = datetime.now()
    for d in range(days):
        db.session.add(StockTransaction(item_id=item.id, txn_type="OUT", quantity=-per_day, balance_after=0,
                                        created_at=now - timedelta(days=d)))
    item.quantity_on_hand -= days * per_day
    db.session.query(Batch).filter_by(item_id=item.id).first().quantity -= days * per_day
    db.session.commit()


def test_receive_creates_batch_and_ledger(db, basic, users):
    item = basic["item"]
    receive_stock(item.id, 50, users["staff"], batch_no="B1", expiry_date=date.today() + timedelta(days=90))
    assert item.quantity_on_hand == 50
    b = Batch.query.filter_by(item_id=item.id, batch_no="B1").one()
    assert b.quantity == 50
    t = StockTransaction.query.one()
    assert (t.txn_type, t.quantity, t.balance_after) == ("IN", 50, 50)


def test_receive_rejects_bad_quantity(db, basic):
    with pytest.raises(StockError):
        receive_stock(basic["item"].id, 0)
    with pytest.raises(StockError):
        receive_stock(basic["item"].id, "abc")


def test_issue_uses_fefo_across_batches(db, basic, users):
    item = basic["item"]
    receive_stock(item.id, 10, batch_no="LATE", expiry_date=date.today() + timedelta(days=100))
    receive_stock(item.id, 5, batch_no="EARLY", expiry_date=date.today() + timedelta(days=10))
    txns = issue_stock(item.id, 8, users["staff"])
    assert [(t.batch.batch_no, t.quantity) for t in txns] == [("EARLY", -5), ("LATE", -3)]
    assert item.quantity_on_hand == 7


def test_issue_rejects_insufficient_stock(db, basic):
    item = basic["item"]
    receive_stock(item.id, 3)
    with pytest.raises(StockError, match="Insufficient"):
        issue_stock(item.id, 4)
    db.session.rollback()
    assert item.quantity_on_hand == 3


def test_adjustment(db, basic, users):
    item = basic["item"]
    receive_stock(item.id, 20, batch_no="B1")
    batch = Batch.query.filter_by(batch_no="B1").one()
    adjust_stock(item.id, batch.id, 17, users["manager"], reason="damaged")
    assert item.quantity_on_hand == 17
    assert StockTransaction.query.filter_by(txn_type="ADJUST").one().quantity == -3


def test_rop_recalculates_automatically_after_sales(db, basic, users):
    item = basic["item"]
    _history(db, item, days=60, per_day=10)
    recalculate_item(item)
    db.session.commit()
    assert item.avg_daily_sales == pytest.approx(10, rel=0.05)
    # Constant demand -> sigma 0 -> SS 0 (no floor) -> ROP = 10 * 5
    assert item.reorder_point == pytest.approx(50, abs=3)
    item.min_stock = 25
    recalculate_item(item)
    assert item.safety_stock == 25
    assert item.reorder_point == pytest.approx(75, abs=3)


def test_lead_time_change_changes_rop(db, basic):
    item = basic["item"]
    _history(db, item)
    recalculate_item(item)
    rop_before = item.reorder_point
    basic["supplier"].lead_time_days = 10
    recalculate_item(item)
    assert item.reorder_point > rop_before


def test_auto_draft_po_when_stock_hits_rop(db, basic, users):
    item = basic["item"]
    _history(db, item, days=60, per_day=10)
    recalculate_item(item, auto_po=False)
    db.session.commit()
    on_hand = item.quantity_on_hand
    # Sell down to below the ROP
    issue_stock(item.id, on_hand - item.reorder_point + 1, users["staff"])
    po = PurchaseOrder.query.filter_by(status="DRAFT", auto_generated=True).one()
    assert po.lines[0].item_id == item.id
    assert po.lines[0].quantity_ordered > 0
    # Selling more does not create a duplicate line
    issue_stock(item.id, 1, users["staff"])
    assert PurchaseOrderLine.query.count() == 1


def test_po_lifecycle_and_lead_time_learning(db, basic, users):
    item, sup = basic["item"], basic["supplier"]
    for n, lt in enumerate([4, 6, 5]):
        po = PurchaseOrder(po_number="PO-T-%d" % n, supplier_id=sup.id, status="DRAFT")
        po.lines.append(PurchaseOrderLine(item_id=item.id, quantity_ordered=10, unit_cost=100))
        db.session.add(po)
        db.session.commit()
        send_po(po, users["manager"])
        assert po.status == "SENT"
        po.sent_at = datetime.now() - timedelta(days=lt)
        db.session.commit()
        receive_po(po, users["staff"], [{"line": po.lines[0], "qty": 4, "batch_no": "P%d" % n}])
        assert po.status == "PARTIAL"
        receive_po(po, users["staff"], [{"line": po.lines[0], "qty": 6, "batch_no": "P%d" % n}])
        assert po.status == "RECEIVED"
    assert item.quantity_on_hand == 30
    assert sup.lead_time_days == pytest.approx(5, abs=0.05)
    assert sup.lead_time_std == pytest.approx(1, abs=0.05)


def test_receive_more_than_outstanding_rejected(db, basic, users):
    po = PurchaseOrder(po_number="PO-X", supplier_id=basic["supplier"].id, status="DRAFT")
    po.lines.append(PurchaseOrderLine(item_id=basic["item"].id, quantity_ordered=5, unit_cost=1))
    db.session.add(po)
    db.session.commit()
    send_po(po, users["manager"])
    with pytest.raises(StockError):
        receive_po(po, users["staff"], [{"line": po.lines[0], "qty": 6}])
    assert basic["item"].quantity_on_hand == 0


def test_dispatch_order_is_atomic(db, basic, users):
    item = basic["item"]
    receive_stock(item.id, 5)
    from istock.models import Item
    other = Item(sku="TEST-2", name="Other", unit_cost=1, unit_price=2)
    db.session.add(other)
    db.session.commit()
    receive_stock(other.id, 100)
    so = SalesOrder(order_number="SO-T-1", client_id=basic["client"].id)
    so.lines = [SalesOrderLine(item_id=other.id, quantity=10, unit_price=2),
                SalesOrderLine(item_id=item.id, quantity=6, unit_price=150)]  # too many
    db.session.add(so)
    db.session.commit()
    with pytest.raises(StockError):
        dispatch_order(so, users["staff"])
    db.session.refresh(other)
    assert other.quantity_on_hand == 100  # first line rolled back too
    assert so.status == "PENDING"


def test_stock_status(db, basic):
    item = basic["item"]
    assert stock_status(item) == "OUT"
    item.quantity_on_hand, item.safety_stock, item.reorder_point, item.avg_daily_sales = 5, 10, 30, 2
    assert stock_status(item) == "CRITICAL"
    item.quantity_on_hand = 25
    assert stock_status(item) == "REORDER"
    item.quantity_on_hand = 100
    assert stock_status(item) == "OK"
    item.quantity_on_hand = 1000
    assert stock_status(item) == "OVERSTOCK"
