"""Item catalogue, batches, stock adjustments and master data."""
from datetime import date, timedelta

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..auth import role_required
from ..engine import forecasting as fc
from ..engine.replenishment import (analyse, daily_sales, engine_settings,
                                    on_order_bulk, recalculate_item,
                                    stock_status)
from ..extensions import db
from ..models import (Category, Item, Location, StockTransaction, Supplier)
from ..services import reports
from ..services.stock import StockError, adjust_stock
from ..utils import none_if_blank, parse_float, parse_int, parse_money

bp = Blueprint("inventory", __name__, url_prefix="/items")


@bp.route("/")
@login_required
def item_list():
    q = Item.query
    show = request.args.get("show", "active")
    if show == "active":
        q = q.filter(Item.is_active.is_(True))
    elif show == "inactive":
        q = q.filter(Item.is_active.is_(False))
    s = (request.args.get("q") or "").strip()
    if s:
        like = "%" + s + "%"
        q = q.filter(or_(Item.sku.ilike(like), Item.name.ilike(like), Item.barcode.ilike(like)))
    cat = parse_int(request.args.get("category"))
    if cat:
        q = q.filter(Item.category_id == cat)
    sup = parse_int(request.args.get("supplier"))
    if sup:
        q = q.filter(Item.supplier_id == sup)
    items = q.order_by(Item.name).all()
    status = request.args.get("status")
    if status:
        items = [i for i in items if stock_status(i) == status]
    return render_template("items/list.html", items=items,
                           categories=Category.query.order_by(Category.name).all(),
                           suppliers=Supplier.query.order_by(Supplier.name).all())


def _form_choices():
    return dict(categories=Category.query.order_by(Category.name).all(),
                suppliers=Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all(),
                locations=Location.query.order_by(Location.code).all())


def _apply_form(item, f):
    errors = []
    item.sku = (f.get("sku") or "").strip().upper()
    item.name = (f.get("name") or "").strip()
    if not item.sku:
        errors.append("SKU is required.")
    if not item.name:
        errors.append("Name is required.")
    item.barcode = none_if_blank(f.get("barcode"))
    item.description = none_if_blank(f.get("description"))
    item.unit = (f.get("unit") or "pcs").strip()
    item.category_id = parse_int(f.get("category_id"))
    item.supplier_id = parse_int(f.get("supplier_id"))
    item.location_id = parse_int(f.get("location_id"))
    item.unit_cost = parse_money(f.get("unit_cost"))
    item.unit_price = parse_money(f.get("unit_price"))
    item.min_stock = max(0, parse_int(f.get("min_stock"), 0))
    sl = parse_float(f.get("service_level"))
    if sl is not None and not (0.5 <= sl <= 0.999):
        errors.append("Service level must be between 0.50 and 0.999.")
    item.service_level = sl
    lt = parse_float(f.get("lead_time_override"))
    item.lead_time_override = lt if lt and lt > 0 else None
    item.is_active = bool(f.get("is_active", "1" if item.id is None else None))
    return errors


@bp.route("/new", methods=["GET", "POST"])
@role_required("manager")
def item_new():
    item = Item(is_active=True)
    if request.method == "POST":
        errors = _apply_form(item, request.form)
        if not errors:
            try:
                db.session.add(item)
                db.session.flush()
                recalculate_item(item, auto_po=False)
                db.session.commit()
                flash("Item %s created." % item.sku, "success")
                return redirect(url_for("inventory.item_detail", item_id=item.id))
            except IntegrityError:
                db.session.rollback()
                errors.append("SKU or barcode already exists.")
        for e in errors:
            flash(e, "danger")
    return render_template("items/form.html", item=item, **_form_choices())


@bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@role_required("manager")
def item_edit(item_id):
    item = db.session.get(Item, item_id) or abort(404)
    if request.method == "POST":
        errors = _apply_form(item, request.form)
        if not errors:
            try:
                recalculate_item(item)
                db.session.commit()
                flash("Item updated and replenishment figures recalculated.", "success")
                return redirect(url_for("inventory.item_detail", item_id=item.id))
            except IntegrityError:
                db.session.rollback()
                errors.append("SKU or barcode already exists.")
        for e in errors:
            flash(e, "danger")
    return render_template("items/form.html", item=item, **_form_choices())


@bp.route("/<int:item_id>")
@login_required
def item_detail(item_id):
    item = db.session.get(Item, item_id) or abort(404)
    cfg = engine_settings()
    series = daily_sales(item.id, cfg["history_days"])
    result = analyse(item, series, on_order_bulk([item.id]).get(item.id, 0), cfg)
    txns = (StockTransaction.query.filter_by(item_id=item.id)
            .order_by(StockTransaction.created_at.desc()).limit(25).all())
    start = date.today() - timedelta(days=len(series) - 1)
    labels = [(start + timedelta(days=i)).strftime("%d %b") for i in range(len(series))]
    f_labels = [(date.today() + timedelta(days=i + 1)).strftime("%d %b") for i in range(len(result["forecast_daily"]))]
    first = next((i for i, v in enumerate(series) if v), len(series))
    fitted = [None] * first + fc.lr_fitted(series[first:])
    chart = {
        "labels": labels + f_labels,
        "actual": series + [None] * len(f_labels),
        "sma": fc.sma_series(series, cfg["sma_window"]) + [None] * len(f_labels),
        "lr": fitted + [None] * len(f_labels),
        "forecast": [None] * (len(series) - 1) + [series[-1] if series else 0] + result["forecast_daily"],
    }
    return render_template("items/detail.html", item=item, r=result, txns=txns, chart=chart, cfg=cfg,
                           status=stock_status(item))


@bp.route("/<int:item_id>/adjust", methods=["POST"])
@role_required("manager")
def item_adjust(item_id):
    try:
        adjust_stock(item_id, request.form.get("batch_id"), request.form.get("counted_qty"),
                     current_user, reason=none_if_blank(request.form.get("reason")))
        flash("Stock adjusted.", "success")
    except StockError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("inventory.item_detail", item_id=item_id))


@bp.route("/<int:item_id>/recalculate", methods=["POST"])
@login_required
def item_recalculate(item_id):
    item = db.session.get(Item, item_id) or abort(404)
    recalculate_item(item)
    db.session.commit()
    flash("Replenishment figures recalculated.", "success")
    return redirect(url_for("inventory.item_detail", item_id=item_id))


@bp.route("/labels")
@login_required
def labels():
    ids = [parse_int(x) for x in request.args.getlist("id") if parse_int(x)]
    q = Item.query.filter_by(is_active=True)
    if ids:
        q = q.filter(Item.id.in_(ids))
    buf = reports.labels_pdf(q.order_by(Item.sku).all())
    return send_file(buf, mimetype="application/pdf", download_name="item-labels.pdf")


@bp.route("/count-sheet")
@login_required
def count_sheet():
    show = bool(request.args.get("system")) and current_user.can_view_financials
    buf = reports.count_sheet_pdf(Item.query.filter_by(is_active=True).all(), show_system_qty=show)
    return send_file(buf, mimetype="application/pdf", download_name="stock-count-sheet.pdf")


# --------------------------------------------------------------------------- #
# Categories and warehouse bins
# --------------------------------------------------------------------------- #

@bp.route("/masters", methods=["GET", "POST"])
@role_required("manager")
def masters():
    if request.method == "POST":
        kind = request.form.get("kind")
        try:
            if kind == "category":
                name = (request.form.get("name") or "").strip()
                if not name:
                    raise ValueError("Category name is required.")
                db.session.add(Category(name=name, description=none_if_blank(request.form.get("description"))))
            elif kind == "location":
                code = (request.form.get("code") or "").strip().upper()
                if not code:
                    raise ValueError("Bin code is required.")
                db.session.add(Location(code=code, zone=none_if_blank(request.form.get("zone")),
                                        description=none_if_blank(request.form.get("description"))))
            db.session.commit()
            flash("Saved.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("That name/code already exists.", "danger")
        return redirect(url_for("inventory.masters"))
    return render_template("items/masters.html",
                           categories=Category.query.order_by(Category.name).all(),
                           locations=Location.query.order_by(Location.code).all())
