"""Purchase orders (inbound) and client orders (outbound)."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func

from ..auth import role_required
from ..engine.replenishment import generate_reorder_pos, next_number
from ..extensions import db
from ..models import (PO_STATUSES, SO_STATUSES, Client, Item, PurchaseOrder,
                      PurchaseOrderLine, SalesOrder, SalesOrderLine, Supplier)
from ..services import reports
from ..services.orders import (cancel_po, dispatch_order, picking_plan,
                               receive_po, send_po)
from ..services.stock import StockError
from ..utils import none_if_blank, parse_date, parse_int, parse_money

bp = Blueprint("orders", __name__)


# --------------------------------------------------------------------------- #
# Purchase orders
# --------------------------------------------------------------------------- #

@bp.route("/purchase-orders")
@login_required
def po_list():
    q = PurchaseOrder.query
    status = request.args.get("status")
    if status in PO_STATUSES:
        q = q.filter_by(status=status)
    page = q.order_by(PurchaseOrder.created_at.desc()).paginate(
        page=parse_int(request.args.get("page"), 1), per_page=25, error_out=False)
    counts = dict(db.session.query(PurchaseOrder.status, func.count(PurchaseOrder.id))
                  .group_by(PurchaseOrder.status).all())
    return render_template("orders/po_list.html", page=page, pos=page.items,
                           statuses=PO_STATUSES, counts=counts)


@bp.route("/purchase-orders/generate", methods=["POST"])
@role_required("manager")
def po_generate():
    added, new_pos = generate_reorder_pos()
    if added:
        flash("Replenishment engine added %d line(s); %d new draft PO(s) created. Review and approve them below."
              % (added, len(new_pos)), "success")
    else:
        flash("No new purchase orders needed: every item is above its reorder point or already on order.", "info")
    return redirect(url_for("orders.po_list", status="DRAFT"))


@bp.route("/purchase-orders/new", methods=["GET", "POST"])
@role_required("manager")
def po_new():
    if request.method == "POST":
        supplier = db.session.get(Supplier, parse_int(request.form.get("supplier_id")) or 0)
        if supplier is None:
            flash("Choose a supplier.", "danger")
        else:
            po = PurchaseOrder(po_number=next_number("PO", PurchaseOrder, PurchaseOrder.po_number),
                               supplier_id=supplier.id, status="DRAFT", created_by_id=current_user.id,
                               notes=none_if_blank(request.form.get("notes")))
            db.session.add(po)
            db.session.commit()
            return redirect(url_for("orders.po_detail", po_id=po.id))
    return render_template("orders/po_new.html",
                           suppliers=Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all())


@bp.route("/purchase-orders/<int:po_id>")
@login_required
def po_detail(po_id):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    items = Item.query.filter_by(is_active=True, supplier_id=po.supplier_id).order_by(Item.name).all()
    return render_template("orders/po_detail.html", po=po, supplier_items=items)


@bp.route("/purchase-orders/<int:po_id>/lines", methods=["POST"])
@role_required("manager")
def po_lines(po_id):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    if po.status != "DRAFT":
        flash("Only draft purchase orders can be edited.", "warning")
        return redirect(url_for("orders.po_detail", po_id=po.id))
    action = request.form.get("action")
    if action == "add":
        item = db.session.get(Item, parse_int(request.form.get("item_id")) or 0)
        qty = parse_int(request.form.get("qty"), 0)
        if item is None or qty <= 0:
            flash("Choose an item and a positive quantity.", "danger")
        else:
            existing = next((l for l in po.lines if l.item_id == item.id), None)
            if existing:
                existing.quantity_ordered += qty
            else:
                po.lines.append(PurchaseOrderLine(item_id=item.id, quantity_ordered=qty,
                                                  unit_cost=parse_money(request.form.get("unit_cost"), item.unit_cost)))
    elif action == "update":
        for line in list(po.lines):
            qty = parse_int(request.form.get("qty_%d" % line.id))
            if qty is not None:
                if qty <= 0:
                    po.lines.remove(line)
                else:
                    line.quantity_ordered = qty
            cost = request.form.get("cost_%d" % line.id)
            if cost not in (None, ""):
                line.unit_cost = parse_money(cost, line.unit_cost)
    db.session.commit()
    return redirect(url_for("orders.po_detail", po_id=po.id))


@bp.route("/purchase-orders/<int:po_id>/send", methods=["POST"])
@role_required("manager")
def po_send(po_id):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    try:
        send_po(po, current_user)
        flash("Purchase order approved and marked as sent to %s." % po.supplier.name, "success")
    except StockError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("orders.po_detail", po_id=po.id))


@bp.route("/purchase-orders/<int:po_id>/cancel", methods=["POST"])
@role_required("manager")
def po_cancel(po_id):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    try:
        cancel_po(po)
        flash("Purchase order cancelled.", "info")
    except StockError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("orders.po_detail", po_id=po.id))


@bp.route("/purchase-orders/<int:po_id>/receive", methods=["POST"])
@login_required
def po_receive(po_id):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    receipts = [{"line": l,
                 "qty": parse_int(request.form.get("qty_%d" % l.id), 0),
                 "batch_no": none_if_blank(request.form.get("batch_%d" % l.id)),
                 "expiry_date": parse_date(request.form.get("expiry_%d" % l.id))}
                for l in po.lines]
    try:
        receive_po(po, current_user, receipts)
        flash("Goods received into stock. PO status: %s." % po.status, "success")
    except StockError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("orders.po_detail", po_id=po_id))


@bp.route("/purchase-orders/<int:po_id>/pdf")
@login_required
def po_pdf(po_id):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    return send_file(reports.purchase_order_pdf(po), mimetype="application/pdf",
                     download_name="%s.pdf" % po.po_number)


# --------------------------------------------------------------------------- #
# Client orders
# --------------------------------------------------------------------------- #

@bp.route("/orders")
@login_required
def so_list():
    q = SalesOrder.query
    status = request.args.get("status")
    if status in SO_STATUSES:
        q = q.filter_by(status=status)
    page = q.order_by(SalesOrder.created_at.desc()).paginate(
        page=parse_int(request.args.get("page"), 1), per_page=25, error_out=False)
    counts = dict(db.session.query(SalesOrder.status, func.count(SalesOrder.id))
                  .group_by(SalesOrder.status).all())
    return render_template("orders/so_list.html", page=page, orders=page.items,
                           statuses=SO_STATUSES, counts=counts)


@bp.route("/orders/new", methods=["GET", "POST"])
@login_required
def so_new():
    clients = Client.query.order_by(Client.name).all()
    if request.method == "POST":
        client = db.session.get(Client, parse_int(request.form.get("client_id")) or 0)
        item_ids = request.form.getlist("item_id")
        qtys = request.form.getlist("qty")
        prices = request.form.getlist("unit_price")
        lines = {}
        for idx, raw_id in enumerate(item_ids):
            item = db.session.get(Item, parse_int(raw_id) or 0)
            qty = parse_int(qtys[idx] if idx < len(qtys) else None, 0)
            if item is None or qty <= 0:
                continue
            price = item.unit_price
            if current_user.can_view_financials and idx < len(prices):
                price = parse_money(prices[idx], item.unit_price)
            if item.id in lines:
                lines[item.id].quantity += qty
            else:
                lines[item.id] = SalesOrderLine(item_id=item.id, quantity=qty, unit_price=price)
        if client is None or not lines:
            flash("Choose a client and add at least one item with a quantity.", "danger")
        else:
            order = SalesOrder(order_number=next_number("SO", SalesOrder, SalesOrder.order_number),
                               client_id=client.id, created_by_id=current_user.id,
                               notes=none_if_blank(request.form.get("notes")), lines=list(lines.values()))
            db.session.add(order)
            db.session.commit()
            flash("Order %s created. Print the picking list, pick, then dispatch." % order.order_number, "success")
            return redirect(url_for("orders.so_detail", order_id=order.id))
    return render_template("orders/so_new.html", clients=clients)


@bp.route("/orders/<int:order_id>")
@login_required
def so_detail(order_id):
    order = db.session.get(SalesOrder, order_id) or abort(404)
    plan = picking_plan(order) if order.status == "PENDING" else []
    return render_template("orders/so_detail.html", order=order, plan=plan,
                           shortage=any(p["short"] for p in plan))


@bp.route("/orders/<int:order_id>/dispatch", methods=["POST"])
@login_required
def so_dispatch(order_id):
    order = db.session.get(SalesOrder, order_id) or abort(404)
    try:
        dispatch_order(order, current_user)
        flash("Order dispatched and stock deducted (FEFO).", "success")
    except StockError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("orders.so_detail", order_id=order_id))


@bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
@login_required
def so_cancel(order_id):
    order = db.session.get(SalesOrder, order_id) or abort(404)
    if order.status == "PENDING":
        order.status = "CANCELLED"
        db.session.commit()
        flash("Order cancelled.", "info")
    return redirect(url_for("orders.so_detail", order_id=order_id))


@bp.route("/orders/<int:order_id>/picking-list")
@login_required
def so_picking_pdf(order_id):
    order = db.session.get(SalesOrder, order_id) or abort(404)
    return send_file(reports.picking_list_pdf(order, picking_plan(order)), mimetype="application/pdf",
                     download_name="picking-%s.pdf" % order.order_number)


@bp.route("/orders/<int:order_id>/invoice")
@login_required
def so_invoice_pdf(order_id):
    order = db.session.get(SalesOrder, order_id) or abort(404)
    return send_file(reports.invoice_pdf(order), mimetype="application/pdf",
                     download_name="invoice-%s.pdf" % order.order_number)
