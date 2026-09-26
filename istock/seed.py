"""Realistic demo data: simulates N days of operations for a small FMCG
distributor (trend + weekly seasonality + noise, replenishment cycles,
batches with expiry dates) so the forecasting engine has history to learn from.
"""
import math
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .engine.replenishment import recalculate_all
from .extensions import db
from .models import (Batch, Category, Client, Item, Location, PurchaseOrder,
                     PurchaseOrderLine, SalesOrder, SalesOrderLine,
                     StockTransaction, Supplier, User)
from .settings import set_setting

SUPPLIERS = [
    # name, contact, phone, lead time (days), lead-time jitter (days)
    ("Lanka Agro Foods (Pvt) Ltd", "S. Perera", "011 234 5678", 5, 1),
    ("Ceylon Beverage Traders", "N. Fernando", "011 276 1122", 7, 2),
    ("Hill Country Tea Exporters", "R. Bandara", "052 222 3344", 10, 2),
    ("Island Home Care Products", "K. Silva", "011 255 9090", 3, 1),
    ("Southern Dairy Co-op", "A. Wickramasinghe", "041 223 4455", 4, 1),
    ("Colombo Snacks Manufacturing", "M. Jayasuriya", "011 287 6543", 14, 3),
]

CATEGORIES = [("Rice & Grains", "Rice, dhal, flour"), ("Beverages", "Soft drinks, juices, water"),
              ("Tea & Coffee", "Packaged tea and coffee"), ("Home Care", "Soap, detergent, cleaners"),
              ("Dairy", "Milk powder, yoghurt, butter"), ("Snacks", "Biscuits, chips, noodles"),
              ("Cooking Essentials", "Oil, sugar, salt, spices")]

LOCATIONS = [("A-01-01", "Dry goods"), ("A-01-02", "Dry goods"), ("A-02-01", "Dry goods"), ("A-02-02", "Dry goods"),
             ("B-01-01", "Beverages"), ("B-01-02", "Beverages"), ("B-02-01", "Beverages"),
             ("C-01-01", "Chilled"), ("C-01-02", "Chilled"), ("D-01-01", "Home care"), ("D-01-02", "Home care"),
             ("E-01-01", "Snacks"), ("E-01-02", "Snacks"), ("E-02-01", "Snacks")]

# sku, name, unit, category, supplier idx, bin, cost, price, base demand/day, trend/day, shelf life (days), scenario
ITEMS = [
    ("RG-RICE-5", "Samba Rice 5kg", "bag", 0, 0, "A-01-01", 1150, 1390, 14, 0.03, 365, "normal"),
    ("RG-RICE-10", "Nadu Rice 10kg", "bag", 0, 0, "A-01-01", 2050, 2390, 6, 0.00, 365, "normal"),
    ("RG-DHAL-1", "Red Dhal 1kg", "pack", 0, 0, "A-01-02", 290, 360, 22, 0.05, 270, "low"),
    ("RG-FLOUR-1", "Wheat Flour 1kg", "pack", 0, 0, "A-01-02", 210, 255, 18, -0.02, 180, "normal"),
    ("BV-COLA-15", "Cola Soft Drink 1.5L", "bottle", 1, 1, "B-01-01", 260, 330, 30, 0.08, 270, "normal"),
    ("BV-ORNG-1", "Orange Nectar 1L", "pack", 1, 1, "B-01-02", 340, 420, 12, 0.02, 120, "expiring"),
    ("BV-WATER-15", "Drinking Water 1.5L", "bottle", 1, 1, "B-02-01", 75, 110, 45, 0.10, 365, "out"),
    ("BV-GINGER-4", "Ginger Beer 400ml", "bottle", 1, 1, "B-02-01", 120, 160, 15, -0.03, 270, "normal"),
    ("TC-TEA-200", "Ceylon Black Tea 200g", "pack", 2, 2, "A-02-01", 520, 650, 16, 0.01, 540, "normal"),
    ("TC-TEA-100B", "Tea Bags 100s", "box", 2, 2, "A-02-01", 610, 760, 7, 0.02, 540, "low"),
    ("TC-COFFEE-50", "Instant Coffee 50g", "jar", 2, 2, "A-02-02", 690, 850, 4, 0.00, 540, "overstock"),
    ("HC-SOAP-100", "Herbal Bath Soap 100g", "bar", 3, 3, "D-01-01", 85, 120, 40, 0.04, 720, "normal"),
    ("HC-DETR-1", "Laundry Detergent 1kg", "pack", 3, 3, "D-01-01", 540, 680, 11, 0.02, 720, "normal"),
    ("HC-DISH-500", "Dishwash Liquid 500ml", "bottle", 3, 3, "D-01-02", 260, 340, 13, 0.00, 720, "normal"),
    ("HC-FLOOR-1", "Floor Cleaner 1L", "bottle", 3, 3, "D-01-02", 410, 520, 3, -0.01, 720, "dead"),
    ("DY-MILK-400", "Full Cream Milk Powder 400g", "pack", 4, 4, "A-02-02", 980, 1150, 25, 0.06, 365, "low"),
    ("DY-YOG-80", "Set Yoghurt 80g", "cup", 4, 4, "C-01-01", 55, 75, 60, 0.05, 21, "expiring"),
    ("DY-BUTTER-200", "Table Butter 200g", "pack", 4, 4, "C-01-02", 690, 820, 5, 0.01, 90, "normal"),
    ("SN-BISC-200", "Cream Crackers 200g", "pack", 5, 5, "E-01-01", 180, 230, 35, 0.07, 240, "normal"),
    ("SN-CHOC-100", "Chocolate Biscuits 100g", "pack", 5, 5, "E-01-02", 140, 185, 20, 0.02, 240, "normal"),
    ("SN-NOOD-75", "Instant Noodles 75g", "pack", 5, 5, "E-02-01", 95, 130, 48, 0.12, 300, "low"),
    ("SN-CHIPS-50", "Potato Chips 50g", "pack", 5, 5, "E-02-01", 110, 150, 18, -0.04, 180, "normal"),
    ("CE-OIL-1", "Coconut Oil 1L", "bottle", 6, 0, "A-01-02", 720, 880, 17, 0.03, 365, "normal"),
    ("CE-SUGAR-1", "White Sugar 1kg", "pack", 6, 0, "A-01-01", 255, 310, 28, 0.02, 720, "normal"),
    ("CE-SALT-1", "Iodised Salt 1kg", "pack", 6, 0, "A-01-02", 95, 130, 9, 0.00, 720, "normal"),
    ("CE-CHILLI-100", "Chilli Powder 100g", "pack", 6, 0, "A-02-01", 180, 240, 0, 0.00, 365, "new"),
]

CLIENTS = [("Sunrise Supermarket", "D. Kumara", "045 222 1100", "Main Street, Ratnapura"),
           ("Pelmadulla Grocery Stores", "P. Senanayake", "045 227 3344", "Colombo Road, Pelmadulla"),
           ("Balangoda Mini Mart", "H. Rathnayake", "045 228 7788", "Bus Stand Road, Balangoda"),
           ("Embilipitiya Wholesale", "T. Gunasekara", "047 223 9911", "New Town, Embilipitiya"),
           ("Belihuloya Rest House Shop", "W. Dissanayake", "045 228 0055", "Belihuloya"),
           ("Kahawatta Co-op City", "L. Herath", "045 227 6600", "Kahawatta")]

WEEKLY = [1.05, 1.0, 0.95, 1.0, 1.15, 1.25, 0.6]  # Mon..Sun


def _ts(day, rng):
    return datetime.combine(day, time(hour=rng.randint(8, 17), minute=rng.randint(0, 59)))


def seed_demo(days=180, rng_seed=42):
    rng = random.Random(rng_seed)
    db.drop_all()
    db.create_all()

    users = {}
    for username, name, role, pw in [("admin", "System Administrator", "admin", "admin123"),
                                     ("manager", "Office Manager", "manager", "manager123"),
                                     ("staff", "Warehouse Staff", "staff", "staff123")]:
        u = User(username=username, full_name=name, role=role)
        u.set_password(pw)
        db.session.add(u)
        users[role] = u
    set_setting("company_name", "i-STOCK Demo Distributors")

    cats = [Category(name=n, description=d) for n, d in CATEGORIES]
    sups = [Supplier(name=n, contact_person=c, phone=p, lead_time_days=lt, email=n.split()[0].lower() + "@example.com",
                     address="Sri Lanka") for n, c, p, lt, _ in SUPPLIERS]
    locs = {code: Location(code=code, zone=zone) for code, zone in LOCATIONS}
    clients = [Client(name=n, contact_person=c, phone=p, address=a) for n, c, p, a in CLIENTS]
    db.session.add_all(cats + sups + list(locs.values()) + clients)
    db.session.flush()

    today = date.today()
    start = today - timedelta(days=days - 1)
    po_seq = [0]

    def new_po_number(day):
        po_seq[0] += 1
        return "PO-%s-%03d" % (day.strftime("%Y%m%d"), po_seq[0])

    for idx, (sku, name, unit, ci, si, bin_code, cost, price, base, trend, shelf, scenario) in enumerate(ITEMS):
        sup_lt, sup_jitter = SUPPLIERS[si][3], SUPPLIERS[si][4]
        item = Item(sku=sku, barcode="479%010d" % (1000000 + idx * 7919), name=name, unit=unit,
                    category_id=cats[ci].id, supplier_id=sups[si].id, location_id=locs[bin_code].id,
                    unit_cost=Decimal(cost), unit_price=Decimal(price), min_stock=0, is_active=True)
        db.session.add(item)
        db.session.flush()
        if scenario == "new":
            continue

        batches = []          # [Batch]
        pending = []          # (arrival_day, qty, po)
        on_hand = int(base * 20) + 10
        first_batch = Batch(item_id=item.id, batch_no="OPEN-%02d" % (idx + 1), quantity=on_hand,
                            expiry_date=start + timedelta(days=min(shelf, 60) + rng.randint(0, 20)),
                            received_at=datetime.combine(start, time(8)))
        db.session.add(first_batch)
        db.session.flush()
        batches.append(first_batch)
        db.session.add(StockTransaction(item_id=item.id, batch_id=first_batch.id, txn_type="IN", quantity=on_hand,
                                        balance_after=on_hand, unit_value=item.unit_cost, reference="OPENING",
                                        source="manual", user_id=users["manager"].id,
                                        created_at=datetime.combine(start, time(8))))
        stop_replenish_day = None
        if scenario == "out":
            stop_replenish_day = days - 25
        elif scenario == "low":
            stop_replenish_day = days - rng.randint(9, 14)

        for d in range(days):
            day = start + timedelta(days=d)
            # Goods arriving today
            for arr in [p for p in pending if p[0] == day]:
                _, qty, po = arr
                pending.remove(arr)
                b = Batch(item_id=item.id, batch_no="B%s-%02d" % (day.strftime("%y%m%d"), idx + 1), quantity=qty,
                          expiry_date=day + timedelta(days=shelf), received_at=_ts(day, rng))
                db.session.add(b)
                db.session.flush()
                batches.append(b)
                on_hand += qty
                db.session.add(StockTransaction(item_id=item.id, batch_id=b.id, txn_type="IN", quantity=qty,
                                                balance_after=on_hand, unit_value=item.unit_cost,
                                                reference=po.po_number, source="po", purchase_order_id=po.id,
                                                user_id=users["staff"].id, created_at=b.received_at))
                po.lines[0].quantity_received = qty
                po.status = "RECEIVED"
                po.received_at = b.received_at

            # Demand for the day
            level = max(0.0, base + trend * d)
            if scenario == "dead" and d > days - 95:
                level = 0
            demand = level * WEEKLY[day.weekday()] * rng.gauss(1.0, 0.25)
            demand = max(0, int(round(demand)))
            orders = rng.randint(1, 4) if demand else 0
            parts = []
            remaining_demand = demand
            for o in range(orders):
                q = remaining_demand if o == orders - 1 else rng.randint(0, remaining_demand)
                remaining_demand -= q
                if q:
                    parts.append(q)
            for q in parts:
                q = min(q, on_hand)
                if q <= 0:
                    break  # lost sale (stockout)
                client = rng.choice(clients)
                ts = _ts(day, rng)
                need = q
                for b in sorted([b for b in batches if b.quantity > 0], key=lambda b: (b.expiry_date, b.id)):
                    if need == 0:
                        break
                    take = min(b.quantity, need)
                    b.quantity -= take
                    need -= take
                    on_hand -= take
                    db.session.add(StockTransaction(item_id=item.id, batch_id=b.id, txn_type="OUT", quantity=-take,
                                                    balance_after=on_hand, unit_value=item.unit_price,
                                                    reference="INV-%s-%04d" % (day.strftime("%y%m%d"), rng.randint(1, 9999)),
                                                    source=rng.choice(["scan", "scan", "order"]), client_id=client.id,
                                                    user_id=users["staff"].id, created_at=ts))

            # Simple (s, Q) replenishment policy used to generate realistic history
            if stop_replenish_day is not None and d >= stop_replenish_day:
                continue
            in_transit = sum(p[1] for p in pending)
            trigger = level * (sup_lt + 4) + 1.2 * math.sqrt(sup_lt) * level * 0.3
            if on_hand + in_transit <= trigger and d < days - 1:
                qty = int(max(level * 21, 10))
                if scenario == "overstock" and d > days - 30:
                    qty = int(level * 150)
                lt = max(1, sup_lt + rng.randint(-sup_jitter, sup_jitter))
                sent = _ts(day, rng)
                po = PurchaseOrder(po_number=new_po_number(day), supplier_id=sups[si].id, status="SENT",
                                   created_by_id=users["manager"].id, approved_by_id=users["manager"].id,
                                   created_at=sent, sent_at=sent, expected_date=day + timedelta(days=sup_lt))
                po.lines.append(PurchaseOrderLine(item_id=item.id, quantity_ordered=qty, unit_cost=item.unit_cost))
                db.session.add(po)
                db.session.flush()
                pending.append((day + timedelta(days=lt), qty, po))

        # Overstock scenario: a large recent delivery
        if scenario == "overstock":
            b = Batch(item_id=item.id, batch_no="BULK-%02d" % (idx + 1), quantity=int(base * 140),
                      expiry_date=today + timedelta(days=400), received_at=datetime.combine(today - timedelta(days=6), time(10)))
            db.session.add(b)
            db.session.flush()
            batches.append(b)
            on_hand += b.quantity
            db.session.add(StockTransaction(item_id=item.id, batch_id=b.id, txn_type="IN", quantity=b.quantity,
                                            balance_after=on_hand, unit_value=item.unit_cost, reference="BULK-DEAL",
                                            source="manual", user_id=users["manager"].id, created_at=b.received_at))
        # Expiring scenario: a batch that expires soon
        if scenario == "expiring":
            live = [b for b in batches if b.quantity > 0]
            if live:
                live[0].expiry_date = today + timedelta(days=rng.randint(4, 12))
        item.quantity_on_hand = on_hand

    db.session.flush()
    # Purchase orders that were still in transit at the end of the simulation stay SENT.
    # Two pending client orders ready to be picked
    items = {i.sku: i for i in Item.query.all()}
    for n, (client, lines) in enumerate([(clients[0], [("RG-RICE-5", 10), ("CE-SUGAR-1", 20), ("SN-BISC-200", 24)]),
                                         (clients[2], [("HC-SOAP-100", 48), ("BV-COLA-15", 12), ("DY-YOG-80", 30)])], 1):
        so = SalesOrder(order_number="SO-%s-%03d" % (today.strftime("%Y%m%d"), n), client_id=client.id,
                        created_by_id=users["staff"].id)
        for sku, q in lines:
            so.lines.append(SalesOrderLine(item_id=items[sku].id, quantity=q, unit_price=items[sku].unit_price))
        db.session.add(so)
    db.session.commit()

    # Learn supplier lead times from the simulated PO history, then run the engine.
    from .engine.replenishment import update_supplier_lead_time
    for s in Supplier.query.all():
        update_supplier_lead_time(s)
    db.session.commit()
    recalculate_all(auto_po=False)
