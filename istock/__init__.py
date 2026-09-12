"""i-STOCK: an automated, predictive and cost-effective inventory management
and demand forecasting system for small-scale distributors."""
import os

import click
from dotenv import load_dotenv
from flask import Flask, render_template

from .config import BASE_DIR, Config
from .extensions import csrf, db, login_manager

load_dotenv(os.path.join(BASE_DIR, ".env"))


def create_app(config_class=Config):
    app = Flask(__name__, instance_path=os.path.join(BASE_DIR, "instance"))
    app.config.from_object(config_class)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401  (register tables)
    from .views import register_blueprints
    register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)
    _register_cli(app)

    from . import devreload
    devreload.init_app(app)

    @app.route("/favicon.ico")
    def favicon():
        return "", 204

    return app


@login_manager.user_loader
def load_user(user_id):
    from .models import User
    return db.session.get(User, int(user_id))


def _register_template_helpers(app):
    from .engine.replenishment import STATUS_META, stock_status
    from .settings import get_setting

    @app.template_filter("money")
    def money(value):
        return "{:,.2f}".format(float(value or 0))

    @app.template_filter("num")
    def num(value, digits=0):
        if value is None:
            return "-"
        return ("{:,.%df}" % digits).format(float(value))

    @app.template_filter("tex")
    def tex(value, digits=2):
        """Format a number for use inside a KaTeX expression (thousands separators kept tight)."""
        if value is None:
            return "0"
        return ("{:,.%df}" % digits).format(float(value)).replace(",", "{,}")

    @app.template_filter("dt")
    def dt(value, fmt="%Y-%m-%d %H:%M"):
        return value.strftime(fmt) if value else "-"

    @app.context_processor
    def inject_helpers():
        return {
            "currency": get_setting("currency"),
            "company_name": get_setting("company_name"),
            "stock_status": stock_status,
            "STATUS_META": STATUS_META,
        }


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/error.html", code=403, message="You do not have permission to view this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/error.html", code=404, message="The page you requested was not found."), 404


def _register_cli(app):
    @app.cli.command("init-db")
    @click.option("--drop", is_flag=True, help="Drop all tables first.")
    def init_db(drop):
        """Create database tables."""
        if drop:
            db.drop_all()
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("seed")
    @click.option("--days", default=180, help="Days of simulated sales history.")
    def seed(days):
        """Drop, recreate and load realistic demo data."""
        from .seed import seed_demo
        seed_demo(days=days)
        click.echo("Demo data loaded. Logins: admin/admin123, manager/manager123, staff/staff123")

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin(username, password):
        """Create an administrator account."""
        from .models import User
        db.create_all()
        user = User(username=username, full_name=username, role="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo("Administrator %s created." % username)

    @app.cli.command("recalc")
    def recalc():
        """Recalculate safety stock, ROP and forecasts for every item."""
        from .engine.replenishment import recalculate_all
        click.echo("Recalculated %d items." % recalculate_all())

    @app.cli.command("export-schema")
    @click.option("--out", default=os.path.join(BASE_DIR, "docs", "schema_mysql.sql"))
    def export_schema(out):
        """Write MySQL DDL for all tables (for documentation / manual setup)."""
        from sqlalchemy.dialects import mysql
        from sqlalchemy.schema import CreateTable
        with open(out, "w") as fh:
            fh.write("-- i-STOCK MySQL schema (generated)\n")
            fh.write("CREATE DATABASE IF NOT EXISTS istock CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\nUSE istock;\n\n")
            for table in db.metadata.sorted_tables:
                ddl = str(CreateTable(table).compile(dialect=mysql.dialect())).strip()
                fh.write(ddl + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n\n")
        click.echo("Schema written to %s" % out)
