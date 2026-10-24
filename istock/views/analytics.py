"""Analytics, forecast accuracy, reports and administration."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from ..auth import role_required
from ..engine import forecasting as fc
from ..engine.analytics import (dead_stock, expiring_batches,
                                inventory_overview, portfolio_daily_sales)
from ..engine.replenishment import (analyse, engine_settings, on_order_bulk,
                                    recalculate_all, stock_status)
from ..extensions import db
from ..models import ROLE_LABELS, Item, User
from ..services import reports
from ..settings import DEFAULTS, all_settings, set_setting

bp = Blueprint("analytics", __name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@bp.route("/analytics")
@role_required("manager")
def index():
    cfg = engine_settings()
    rows = inventory_overview(cfg["history_days"])
    on_order = on_order_bulk()
    accuracy = {"SMA": 0, "LR": 0, "sma_mae": [], "lr_mae": [], "sma_mape": [], "lr_mape": []}
    for r in rows:
        res = analyse(r["item"], r["series"], on_order.get(r["item"].id, 0), cfg)
        r["analysis"] = res
        bt = res["backtest"]
        if bt["sma"]:
            accuracy[bt["best"]] += 1
            accuracy["sma_mae"].append(bt["sma"]["mae"])
            accuracy["lr_mae"].append(bt["lr"]["mae"])
            if bt["sma"]["mape"] is not None and bt["lr"]["mape"] is not None:
                accuracy["sma_mape"].append(bt["sma"]["mape"])
                accuracy["lr_mape"].append(bt["lr"]["mape"])
    for k in ("sma_mae", "lr_mae", "sma_mape", "lr_mape"):
        accuracy[k] = fc.mean(accuracy[k]) if accuracy[k] else None
    # Error of the automatically selected model, per item
    sel = []
    for r in rows:
        bt = r["analysis"]["backtest"]
        if bt["sma"]:
            sel.append(min(bt["sma"]["mae"], bt["lr"]["mae"]))
    accuracy["selected_mae"] = fc.mean(sel) if sel else None

    abc = {k: {"count": 0, "value": 0.0} for k in "ABC"}
    for r in rows:
        abc[r["abc"]]["count"] += 1
        abc[r["abc"]]["value"] += r["annual_value"]
    pareto = sorted(rows, key=lambda r: r["annual_value"], reverse=True)
    labels, units, revenue = portfolio_daily_sales(cfg["history_days"])
    turnover_vals = [r["turnover"] for r in rows if r["turnover"]]
    return render_template("analytics/index.html", rows=rows, pareto=pareto, abc=abc, accuracy=accuracy,
                           dead=dead_stock(), expiring=expiring_batches(), cfg=cfg,
                           avg_turnover=fc.mean(turnover_vals) if turnover_vals else None,
                           trend={"labels": labels, "units": units, "revenue": revenue})


@bp.route("/reports")
@login_required
def reports_index():
    return render_template("analytics/reports.html")


@bp.route("/reports/inventory.xlsx")
@login_required
def report_inventory():
    items = Item.query.filter_by(is_active=True).order_by(Item.sku).all()
    return send_file(reports.inventory_xlsx(items, with_financials=current_user.can_view_financials),
                     as_attachment=True, download_name="inventory.xlsx", mimetype=XLSX)


@bp.route("/reports/reorder.xlsx")
@role_required("manager")
def report_reorder():
    items = [i for i in Item.query.filter_by(is_active=True).order_by(Item.sku).all()
             if stock_status(i) in ("OUT", "CRITICAL", "REORDER") or i.recommended_qty > 0]
    return send_file(reports.reorder_xlsx(items), as_attachment=True,
                     download_name="reorder-recommendations.xlsx", mimetype=XLSX)


# --------------------------------------------------------------------------- #
# Administration
# --------------------------------------------------------------------------- #

@bp.route("/admin/users")
@role_required("admin")
def users():
    return render_template("admin/users.html", users=User.query.order_by(User.username).all(), roles=ROLE_LABELS)


@bp.route("/admin/users/new", methods=["GET", "POST"])
@bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def user_form(user_id=None):
    user = db.session.get(User, user_id) if user_id else User(role="staff", is_active_user=True)
    if user is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        username = (f.get("username") or "").strip()
        password = f.get("password") or ""
        role = f.get("role")
        errors = []
        if not username or not (f.get("full_name") or "").strip():
            errors.append("Username and full name are required.")
        if role not in ROLE_LABELS:
            errors.append("Invalid role.")
        if user.id is None and len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if user.id is not None and password and len(password) < 8:
            errors.append("New password must be at least 8 characters.")
        if user.id == current_user.id and (role != "admin" or not f.get("is_active")):
            errors.append("You cannot remove your own administrator access.")
        if not errors:
            user.username = username
            user.full_name = f.get("full_name").strip()
            user.email = (f.get("email") or "").strip() or None
            user.role = role
            user.is_active_user = bool(f.get("is_active")) if user.id else True
            if password:
                user.set_password(password)
            try:
                db.session.add(user)
                db.session.commit()
                flash("User saved.", "success")
                return redirect(url_for("analytics.users"))
            except IntegrityError:
                db.session.rollback()
                errors.append("Username already exists.")
        for e in errors:
            flash(e, "danger")
    return render_template("admin/user_form.html", user=user, roles=ROLE_LABELS)


@bp.route("/admin/settings", methods=["GET", "POST"])
@role_required("admin")
def settings():
    if request.method == "POST":
        try:
            for key in DEFAULTS:
                if key in request.form:
                    set_setting(key, request.form.get(key).strip())
            sl = float(request.form.get("default_service_level", 0.95))
            if not 0.5 <= sl <= 0.999:
                raise ValueError("Service level must be between 0.50 and 0.999.")
            db.session.commit()
            n = recalculate_all()
            flash("Settings saved; %d items recalculated." % n, "success")
            return redirect(url_for("analytics.settings"))
        except ValueError as exc:
            db.session.rollback()
            flash("Invalid value: %s" % exc, "danger")
    return render_template("admin/settings.html", settings=all_settings())


@bp.route("/admin/recalculate", methods=["POST"])
@role_required("manager")
def recalc():
    n = recalculate_all()
    flash("Safety stock, reorder points and forecasts recalculated for %d items." % n, "success")
    return redirect(request.referrer or url_for("main.dashboard"))
