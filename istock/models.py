"""Relational data model for i-STOCK (normalised, MySQL-compatible)."""
from datetime import date, datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def now():
    return datetime.now()


# --------------------------------------------------------------------------- #
# Users and RBAC
# --------------------------------------------------------------------------- #
ROLE_LEVELS = {"staff": 1, "manager": 2, "admin": 3}
ROLE_LABELS = {"staff": "Warehouse Staff", "manager": "Office Manager", "admin": "Administrator"}


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")
    is_active_user = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    last_login_at = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):  # used by Flask-Login
        return self.is_active_user

    def has_role(self, minimum):
        return ROLE_LEVELS.get(self.role, 0) >= ROLE_LEVELS[minimum]

    @property
    def can_view_financials(self):
        return self.has_role("manager")

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)


# --------------------------------------------------------------------------- #
# Master data
# --------------------------------------------------------------------------- #
class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))


class Location(db.Model):
    """A warehouse bin / shelf location, e.g. A-01-03."""
    __tablename__ = "locations"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    zone = db.Column(db.String(50))
    description = db.Column(db.String(255))


class Supplier(db.Model):
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact_person = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    # Average lead time (days). Updated automatically from actual PO receipts.
    lead_time_days = db.Column(db.Float, nullable=False, default=7)
    # Standard deviation of observed lead times (days); 0 until enough history.
    lead_time_std = db.Column(db.Float, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    items = db.relationship("Item", back_populates="supplier")


class Client(db.Model):
    """Retailers / customers that receive outbound shipments."""
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact_person = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    address = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=now)


# --------------------------------------------------------------------------- #
# Items, batches and stock movements
# --------------------------------------------------------------------------- #
class Item(db.Model):
    __tablename__ = "items"
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=False, index=True)
    barcode = db.Column(db.String(64), unique=True, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(255))
    unit = db.Column(db.String(20), nullable=False, default="pcs")
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"))
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    # Cached stock level (sum of batch quantities). The item row is locked
    # (SELECT ... FOR UPDATE) during every stock movement.
    quantity_on_hand = db.Column(db.Integer, nullable=False, default=0)

    # Replenishment parameters
    min_stock = db.Column(db.Integer, nullable=False, default=0)          # manual floor for safety stock
    service_level = db.Column(db.Float)                                    # e.g. 0.95; None => global default
    lead_time_override = db.Column(db.Float)                               # days; None => supplier lead time
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    # Values produced by the decision engine (recalculated automatically)
    avg_daily_sales = db.Column(db.Float, nullable=False, default=0)
    demand_std = db.Column(db.Float, nullable=False, default=0)
    safety_stock = db.Column(db.Integer, nullable=False, default=0)
    reorder_point = db.Column(db.Integer, nullable=False, default=0)
    forecast_30d = db.Column(db.Float, nullable=False, default=0)
    forecast_model = db.Column(db.String(20))
    recommended_qty = db.Column(db.Integer, nullable=False, default=0)
    metrics_updated_at = db.Column(db.DateTime)

    category = db.relationship("Category")
    supplier = db.relationship("Supplier", back_populates="items")
    location = db.relationship("Location")
    batches = db.relationship("Batch", back_populates="item", order_by="Batch.expiry_date",
                              cascade="all, delete-orphan")

    @property
    def stock_value(self):
        return float(self.unit_cost or 0) * (self.quantity_on_hand or 0)

    @property
    def scan_code(self):
        return self.barcode or self.sku


class Batch(db.Model):
    __tablename__ = "batches"
    __table_args__ = (db.UniqueConstraint("item_id", "batch_no", name="uq_batch_item_no"),)
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False, index=True)
    batch_no = db.Column(db.String(50), nullable=False)
    expiry_date = db.Column(db.Date)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    received_at = db.Column(db.DateTime, nullable=False, default=now)

    item = db.relationship("Item", back_populates="batches")

    @property
    def days_to_expiry(self):
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days


TXN_TYPES = ("IN", "OUT", "ADJUST")


class StockTransaction(db.Model):
    """Immutable ledger of every inbound, outbound and adjustment movement."""
    __tablename__ = "stock_transactions"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False, index=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("batches.id"))
    txn_type = db.Column(db.String(10), nullable=False, index=True)
    # Signed quantity: +ve for IN, -ve for OUT, either for ADJUST.
    quantity = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    unit_value = db.Column(db.Numeric(12, 2))  # cost for IN, selling price for OUT
    reference = db.Column(db.String(60))
    note = db.Column(db.String(255))
    source = db.Column(db.String(20), default="manual")  # manual | scan | po | order
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"))
    sales_order_id = db.Column(db.Integer, db.ForeignKey("sales_orders.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now, index=True)

    item = db.relationship("Item")
    batch = db.relationship("Batch")
    client = db.relationship("Client")
    user = db.relationship("User")


# --------------------------------------------------------------------------- #
# Purchasing
# --------------------------------------------------------------------------- #
PO_STATUSES = ("DRAFT", "SENT", "PARTIAL", "RECEIVED", "CANCELLED")
PO_OPEN_STATUSES = ("DRAFT", "SENT", "PARTIAL")


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(30), unique=True, nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="DRAFT", index=True)
    auto_generated = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(255))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    sent_at = db.Column(db.DateTime)
    expected_date = db.Column(db.Date)
    received_at = db.Column(db.DateTime)

    supplier = db.relationship("Supplier")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    lines = db.relationship("PurchaseOrderLine", back_populates="po", cascade="all, delete-orphan")

    @property
    def total(self):
        return sum(float(l.unit_cost or 0) * l.quantity_ordered for l in self.lines)


class PurchaseOrderLine(db.Model):
    __tablename__ = "purchase_order_lines"
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    quantity_ordered = db.Column(db.Integer, nullable=False)
    quantity_received = db.Column(db.Integer, nullable=False, default=0)
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    po = db.relationship("PurchaseOrder", back_populates="lines")
    item = db.relationship("Item")

    @property
    def outstanding(self):
        return max(0, self.quantity_ordered - self.quantity_received)


# --------------------------------------------------------------------------- #
# Outbound client orders (picking lists, invoices)
# --------------------------------------------------------------------------- #
SO_STATUSES = ("PENDING", "DISPATCHED", "CANCELLED")


class SalesOrder(db.Model):
    __tablename__ = "sales_orders"
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(30), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="PENDING", index=True)
    notes = db.Column(db.String(255))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    dispatched_at = db.Column(db.DateTime)

    client = db.relationship("Client")
    created_by = db.relationship("User")
    lines = db.relationship("SalesOrderLine", back_populates="order", cascade="all, delete-orphan")

    @property
    def total(self):
        return sum(float(l.unit_price or 0) * l.quantity for l in self.lines)


class SalesOrderLine(db.Model):
    __tablename__ = "sales_order_lines"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("sales_orders.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    order = db.relationship("SalesOrder", back_populates="lines")
    item = db.relationship("Item")


# --------------------------------------------------------------------------- #
# System settings (key/value)
# --------------------------------------------------------------------------- #
class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255), nullable=False)
