"""Suppliers and clients."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import login_required
from sqlalchemy import func

from ..auth import role_required
from ..engine.replenishment import recalculate_supplier_items
from ..extensions import db
from ..models import Client, Item, PurchaseOrder, SalesOrder, Supplier
from ..utils import none_if_blank, parse_float

bp = Blueprint("partners", __name__)


@bp.route("/suppliers")
@login_required
def suppliers():
    rows = Supplier.query.order_by(Supplier.name).all()
    counts = dict(db.session.query(Item.supplier_id, func.count(Item.id))
                  .filter(Item.is_active.is_(True)).group_by(Item.supplier_id).all())
    received = dict(db.session.query(PurchaseOrder.supplier_id, func.count(PurchaseOrder.id))
                    .filter(PurchaseOrder.status == "RECEIVED").group_by(PurchaseOrder.supplier_id).all())
    return render_template("partners/suppliers.html", suppliers=rows, counts=counts, received=received)


@bp.route("/suppliers/new", methods=["GET", "POST"])
@bp.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@role_required("manager")
def supplier_form(supplier_id=None):
    supplier = db.session.get(Supplier, supplier_id) if supplier_id else Supplier(lead_time_days=7, is_active=True)
    if supplier is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        lead = parse_float(f.get("lead_time_days"))
        if not name or lead is None or lead <= 0:
            flash("Name and a positive lead time are required.", "danger")
        else:
            lead_changed = supplier.id is not None and abs((supplier.lead_time_days or 0) - lead) > 1e-9
            supplier.name = name
            supplier.contact_person = none_if_blank(f.get("contact_person"))
            supplier.phone = none_if_blank(f.get("phone"))
            supplier.email = none_if_blank(f.get("email"))
            supplier.address = none_if_blank(f.get("address"))
            supplier.lead_time_days = lead
            supplier.is_active = bool(f.get("is_active")) if supplier.id else True
            db.session.add(supplier)
            db.session.flush()
            if lead_changed:
                recalculate_supplier_items(supplier)  # ROP depends on lead time
            db.session.commit()
            flash("Supplier saved." + (" Reorder points recalculated for the new lead time." if lead_changed else ""),
                  "success")
            return redirect(url_for("partners.suppliers"))
    return render_template("partners/supplier_form.html", supplier=supplier)


@bp.route("/clients")
@login_required
def clients():
    rows = Client.query.order_by(Client.name).all()
    orders = dict(db.session.query(SalesOrder.client_id, func.count(SalesOrder.id))
                  .group_by(SalesOrder.client_id).all())
    return render_template("partners/clients.html", clients=rows, orders=orders)


@bp.route("/clients/new", methods=["GET", "POST"])
@bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def client_form(client_id=None):
    client = db.session.get(Client, client_id) if client_id else Client()
    if client is None:
        abort(404)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Client name is required.", "danger")
        else:
            client.name = name
            client.contact_person = none_if_blank(request.form.get("contact_person"))
            client.phone = none_if_blank(request.form.get("phone"))
            client.address = none_if_blank(request.form.get("address"))
            db.session.add(client)
            db.session.commit()
            flash("Client saved.", "success")
            return redirect(url_for("partners.clients"))
    return render_template("partners/client_form.html", client=client)
