"""Authentication and role-based access control."""
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .models import User, now

bp = Blueprint("auth", __name__)


def role_required(minimum):
    """Allow access only to users whose role is at least `minimum`
    (staff < manager < admin)."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(minimum):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=bool(request.form.get("remember")))
            user.last_login_at = now()
            db.session.commit()
            nxt = request.args.get("next")
            if not nxt or urlparse(nxt).netloc or not nxt.startswith("/"):
                nxt = url_for("main.dashboard")
            return redirect(nxt)
        flash("Invalid username or password.", "danger")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
