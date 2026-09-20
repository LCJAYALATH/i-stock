"""Transactional stock movements.

Every movement locks the item row with SELECT ... FOR UPDATE (and the batch
rows it touches) before reading quantities. On MySQL/InnoDB this serialises
concurrent pickers: if two workers try to pick the last box at the same time,
the second transaction waits, re-reads the reduced quantity and is rejected
with an "insufficient stock" error instead of driving stock negative.

Locks are always taken in the same order (items by id, then batches by expiry)
to avoid deadlocks.
"""
from datetime import date

from sqlalchemy import select

from ..engine.replenishment import recalculate_item
from ..extensions import db
from ..models import Batch, Item, StockTransaction, now


class StockError(Exception):
    pass


def _lock_item(item_id):
    item = db.session.execute(
        select(Item).where(Item.id == item_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if item is None:
        raise StockError("Item not found.")
    return item


def _lock_batches(item_id):
    return db.session.execute(
        select(Batch).where(Batch.item_id == item_id, Batch.quantity > 0)
        .order_by(Batch.expiry_date.is_(None), Batch.expiry_date, Batch.received_at, Batch.id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalars().all()


def _positive_int(qty):
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise StockError("Quantity must be a whole number.")
    if qty <= 0:
        raise StockError("Quantity must be greater than zero.")
    return qty


def receive_stock(item_id, qty, user=None, batch_no=None, expiry_date=None, unit_cost=None,
                  reference=None, note=None, source="manual", purchase_order_id=None, commit=True):
    """Inbound receipt: adds to (or creates) a batch and logs an IN transaction."""
    qty = _positive_int(qty)
    item = _lock_item(item_id)
    if not item.is_active:
        raise StockError("Item %s is inactive." % item.sku)
    batch_no = (batch_no or "").strip() or "B" + date.today().strftime("%Y%m%d")
    batch = db.session.execute(
        select(Batch).where(Batch.item_id == item.id, Batch.batch_no == batch_no).with_for_update()
    ).scalar_one_or_none()
    if batch is None:
        batch = Batch(item_id=item.id, batch_no=batch_no, expiry_date=expiry_date, quantity=0, received_at=now())
        db.session.add(batch)
        db.session.flush()
    elif expiry_date and not batch.expiry_date:
        batch.expiry_date = expiry_date
    batch.quantity += qty
    item.quantity_on_hand = (item.quantity_on_hand or 0) + qty
    txn = StockTransaction(item_id=item.id, batch_id=batch.id, txn_type="IN", quantity=qty,
                           balance_after=item.quantity_on_hand,
                           unit_value=unit_cost if unit_cost is not None else item.unit_cost,
                           reference=reference, note=note, source=source,
                           purchase_order_id=purchase_order_id, user_id=getattr(user, "id", None))
    db.session.add(txn)
    db.session.flush()
    recalculate_item(item)
    if commit:
        db.session.commit()
    return [txn]


def issue_stock(item_id, qty, user=None, batch_id=None, client_id=None, unit_price=None,
                reference=None, note=None, source="manual", sales_order_id=None, commit=True):
    """Outbound fulfilment. Picks First-Expiry-First-Out unless a batch is given."""
    qty = _positive_int(qty)
    item = _lock_item(item_id)
    batches = _lock_batches(item.id)
    if batch_id:
        batches = [b for b in batches if b.id == int(batch_id)]
        if not batches:
            raise StockError("Selected batch has no stock.")
    available = sum(b.quantity for b in batches)
    if qty > available or qty > (item.quantity_on_hand or 0):
        raise StockError("Insufficient stock for %s: requested %d, available %d."
                         % (item.sku, qty, min(available, item.quantity_on_hand or 0)))
    remaining = qty
    txns = []
    price = unit_price if unit_price is not None else item.unit_price
    for batch in batches:
        if remaining == 0:
            break
        take = min(batch.quantity, remaining)
        batch.quantity -= take
        remaining -= take
        item.quantity_on_hand -= take
        txn = StockTransaction(item_id=item.id, batch_id=batch.id, txn_type="OUT", quantity=-take,
                               balance_after=item.quantity_on_hand, unit_value=price,
                               reference=reference, note=note, source=source, client_id=client_id,
                               sales_order_id=sales_order_id, user_id=getattr(user, "id", None))
        db.session.add(txn)
        txns.append(txn)
    db.session.flush()
    recalculate_item(item)
    if commit:
        db.session.commit()
    return txns


def adjust_stock(item_id, batch_id, counted_qty, user=None, reason=None, commit=True):
    """Physical count correction: set a batch to the counted quantity."""
    try:
        counted_qty = int(counted_qty)
    except (TypeError, ValueError):
        raise StockError("Counted quantity must be a whole number.")
    if counted_qty < 0:
        raise StockError("Counted quantity cannot be negative.")
    item = _lock_item(item_id)
    batch = db.session.execute(
        select(Batch).where(Batch.id == int(batch_id), Batch.item_id == item.id).with_for_update()
    ).scalar_one_or_none()
    if batch is None:
        raise StockError("Batch not found for this item.")
    diff = counted_qty - batch.quantity
    if diff == 0:
        raise StockError("Counted quantity matches the system quantity; nothing to adjust.")
    batch.quantity = counted_qty
    item.quantity_on_hand = (item.quantity_on_hand or 0) + diff
    txn = StockTransaction(item_id=item.id, batch_id=batch.id, txn_type="ADJUST", quantity=diff,
                           balance_after=item.quantity_on_hand, unit_value=item.unit_cost,
                           reference="COUNT", note=reason, source="manual", user_id=getattr(user, "id", None))
    db.session.add(txn)
    db.session.flush()
    recalculate_item(item)
    if commit:
        db.session.commit()
    return [txn]
