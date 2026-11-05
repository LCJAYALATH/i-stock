"""Smoke tests for every page, the scanning API and role-based access control."""
import pytest

from istock.models import PurchaseOrder, PurchaseOrderLine
from tests.conftest import login


def test_login_required(app, users):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_bad_login(app, users):
    r = login(app.test_client(), "staff", "wrong")
    assert b"Invalid username or password" in r.data


def test_open_redirect_blocked(app, users):
    c = app.test_client()
    r = c.post("/login?next=https://evil.example.com", data={"username": "staff", "password": "staffpass1"})
    assert r.headers["Location"] == "/"


@pytest.fixture()
def seeded(app):
    from istock.seed import seed_demo
    seed_demo(days=60)
    return app


ALL_PAGES = ["/", "/scan", "/items/", "/items/1", "/transactions", "/suppliers", "/clients", "/clients/new",
             "/purchase-orders", "/purchase-orders/1", "/orders", "/orders/new", "/orders/1", "/reports",
             "/transactions/export", "/reports/inventory.xlsx", "/items/labels", "/items/count-sheet",
             "/orders/1/picking-list", "/purchase-orders/1/pdf"]
MANAGER_PAGES = ["/analytics", "/items/new", "/items/1/edit", "/items/masters", "/suppliers/new",
                 "/purchase-orders/new", "/reports/reorder.xlsx"]
ADMIN_PAGES = ["/admin/users", "/admin/users/new", "/admin/settings"]


@pytest.mark.parametrize("role,password", [("admin", "admin123"), ("manager", "manager123"), ("staff", "staff123")])
def test_pages_by_role(seeded, role, password):
    c = seeded.test_client()
    login(c, role, password)
    for url in ALL_PAGES:
        assert c.get(url).status_code == 200, url
    for url in MANAGER_PAGES:
        expected = 403 if role == "staff" else 200
        assert c.get(url).status_code == expected, url
    for url in ADMIN_PAGES:
        expected = 200 if role == "admin" else 403
        assert c.get(url).status_code == expected, url


def test_staff_cannot_see_costs(seeded):
    c = seeded.test_client()
    login(c, "staff", "staff123")
    assert b"Stock value" not in c.get("/").data
    c = seeded.test_client()
    login(c, "manager", "manager123")
    assert b"Stock value" in c.get("/").data


def test_staff_cannot_approve_po(seeded):
    c = seeded.test_client()
    login(c, "staff", "staff123")
    po = PurchaseOrder(po_number="PO-TEST-1", supplier_id=1, status="DRAFT")
    po.lines.append(PurchaseOrderLine(item_id=1, quantity_ordered=5, unit_cost=1))
    from istock.extensions import db
    db.session.add(po)
    db.session.commit()
    assert c.post("/purchase-orders/%d/send" % po.id).status_code == 403
    assert db.session.get(PurchaseOrder, po.id).status == "DRAFT"


def test_scan_api_flow(seeded):
    c = seeded.test_client()
    login(c, "staff", "staff123")
    r = c.get("/api/items/lookup?code=RG-RICE-5").get_json()
    assert r["ok"] and r["item"]["sku"] == "RG-RICE-5"
    item_id, before = r["item"]["id"], r["item"]["on_hand"]
    r = c.get("/api/items/lookup?code=" + r["item"]["barcode"]).get_json()
    assert r["item"]["id"] == item_id
    r = c.post("/api/scan", json={"mode": "IN", "item_id": item_id, "qty": 10, "batch_no": "SCAN1"}).get_json()
    assert r["ok"] and r["item"]["on_hand"] == before + 10
    r = c.post("/api/scan", json={"mode": "OUT", "item_id": item_id, "qty": 4}).get_json()
    assert r["ok"] and r["item"]["on_hand"] == before + 6
    r = c.post("/api/scan", json={"mode": "OUT", "item_id": item_id, "qty": 10 ** 6})
    assert r.status_code == 409 and not r.get_json()["ok"]
    assert c.get("/api/items/lookup?code=NOPE").status_code == 404


def test_generate_reorder_pos_and_dispatch(seeded):
    c = seeded.test_client()
    login(c, "manager", "manager123")
    r = c.post("/purchase-orders/generate", follow_redirects=True)
    assert r.status_code == 200
    assert PurchaseOrder.query.filter_by(status="DRAFT", auto_generated=True).count() >= 1
    r = c.post("/orders/1/dispatch", follow_redirects=True)
    assert r.status_code == 200


def test_create_order_via_form(seeded):
    c = seeded.test_client()
    login(c, "staff", "staff123")
    r = c.post("/orders/new", data={"client_id": 1, "item_id": ["1", "2"], "qty": ["2", "3"],
                                    "unit_price": ["1", "1"]}, follow_redirects=True)
    assert r.status_code == 200 and b"created" in r.data
    from istock.models import SalesOrder
    so = SalesOrder.query.order_by(SalesOrder.id.desc()).first()
    assert len(so.lines) == 2
    assert float(so.lines[0].unit_price) != 1.0  # staff cannot override prices
