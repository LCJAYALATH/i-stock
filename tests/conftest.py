import pytest

from istock import create_app
from istock.config import TestConfig
from istock.extensions import db as _db
from istock.models import Category, Client, Item, Location, Supplier, User


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    return _db


@pytest.fixture()
def users(db):
    out = {}
    for role in ("staff", "manager", "admin"):
        u = User(username=role, full_name=role.title(), role=role)
        u.set_password(role + "pass1")
        db.session.add(u)
        out[role] = u
    db.session.commit()
    return out


@pytest.fixture()
def basic(db, users):
    sup = Supplier(name="Test Supplier", lead_time_days=5)
    cat = Category(name="General")
    loc = Location(code="A-01-01")
    client = Client(name="Test Shop")
    db.session.add_all([sup, cat, loc, client])
    db.session.flush()
    item = Item(sku="TEST-1", barcode="4790000000017", name="Test Item", unit="pcs", category_id=cat.id,
                supplier_id=sup.id, location_id=loc.id, unit_cost=100, unit_price=150)
    db.session.add(item)
    db.session.commit()
    return {"supplier": sup, "item": item, "client": client, "location": loc}


def login(client, username, password):
    # Tests share one app context, so drop Flask-Login's per-context user cache.
    from flask import g
    g.pop("_login_user", None)
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
