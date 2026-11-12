# i-STOCK

**An automated, predictive and cost-effective inventory management and demand forecasting system for small-scale distributors.**

MIT259 Capstone Project. J.R.L.C. Jayalath (2512091045), Master of Information Technology, Faculty of Graduate Studies, Sabaragamuwa University of Sri Lanka. Supervisor: Dr. U.A.P. Ishanka.

---

## Features (mapped to the proposal)

| Proposal requirement | Implementation |
|---|---|
| Smart inventory cataloguing (SKU, batch, expiry, cost, bin) | `Item`, `Batch`, `Location`, `Category` models; item and master-data screens |
| Camera-based QR/barcode scanning | `/scan` page using **html5-qrcode** (QR, Code128, EAN-13/8, UPC-A, Code39). USB/Bluetooth scanners and manual entry also work |
| Dynamic low-stock alerts, `ROP = d × L + SS` | `engine/replenishment.py`. Recalculated automatically after **every** stock movement, item edit, and supplier lead-time change |
| Automated safety-stock calculator | `SS = Z × √(L·σd² + d²·σL²)`. Uses the service level (Z), demand variability (σd) and lead-time variability (σL) |
| Predictive demand forecasting | Simple Moving Average **and** Linear Regression (OLS). Each item is back-tested on held-out days and the model with the lower MAE is **selected automatically** |
| Data-driven reordering | Order-up-to quantity `Q = forecast(L+R) + SS − on hand − on order`, with EOQ shown for reference |
| Supplier & PO management (Sent → Received) | Draft → **manager approval** → Sent → Partial → Received. When stock reaches the ROP a draft PO is **created automatically** |
| Supplier lead times update | Actual lead time (sent → received) is learned from the last 10 received POs, and the ROPs are recalculated |
| Basic trend analytics | Analytics page: SMA/LR charts, trend direction, forecast accuracy (MAE/MAPE), ABC classification, inventory turnover, days of inventory, dead stock, expiring batches |
| RBAC | Warehouse Staff / Office Manager / Administrator. Staff cannot see costs, edit prices, adjust stock or approve POs |
| Transactional locks (no race conditions) | `SELECT … FOR UPDATE` on item and batch rows, with a consistent lock order. Verified by `tests/test_concurrency_mysql.py`: 20 concurrent pickers, 5 boxes, exactly 5 succeed |
| Reports: picking lists, invoices, count sheets | ReportLab PDFs: purchase order, FEFO picking list sorted by bin, invoice, blind count sheet with barcodes, QR/barcode label sheet. openpyxl Excel: inventory, ledger, reorder recommendations |
| Free / open-source deployment | Python, Flask, MySQL, Bootstrap, Chart.js. Dockerfile, `docker-compose.yml`, `render.yaml`, `Procfile` |
| Mobile-first UI | Responsive Bootstrap layout with an off-canvas menu on phones and a large-touch-target scan screen |

Other features: First-Expiry-First-Out batch picking, client orders with atomic dispatch, stock-count adjustments, an immutable stock ledger with Excel export, and CSRF protection on every form and API call.

## Tech stack

- **Backend:** Python 3.12, Flask 3, SQLAlchemy 2, Flask-Login, Flask-WTF (CSRF)
- **Database:** MySQL 8 (InnoDB) in production. SQLite is used automatically for zero-setup local demos
- **Frontend:** HTML5, Bootstrap 5, Bootstrap Icons, Chart.js, html5-qrcode
- **Documents:** ReportLab (PDF, QR and Code128), openpyxl (Excel)
- **Testing:** pytest (38 tests: unit, integration, RBAC, API, and a MySQL concurrency test)

## Quick start (local, SQLite)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
flask seed            # creates tables + 180 days of realistic demo history
flask run             # http://127.0.0.1:5000  (debug + hot reload, via .flaskenv)
```

Demo logins: `admin / admin123`, `manager / manager123`, `staff / staff123`.

**Hot reload.** `.flaskenv` sets `FLASK_APP=wsgi.py` and `FLASK_DEBUG=1`, so `flask run` starts in debug mode:
saving a Python file restarts the server, and saving a template, CSS or JS file reloads every open browser tab
automatically (`istock/devreload.py`, enabled only in debug mode). Production (gunicorn) never loads `.flaskenv`.

> The browser only allows camera access on **HTTPS or localhost**. To test on a phone over Wi-Fi, use an HTTPS tunnel (for example `cloudflared tunnel --url http://localhost:5000`) or deploy to Render.

## Running with MySQL

```bash
docker compose up --build -d          # MySQL 8 on :3307 + app on :8000
docker compose exec web flask seed    # optional demo data
```

`docker-compose.override.yml` is merged automatically: it mounts the project folder into the container and runs
Flask's dev server in debug mode, so code, template and CSS changes appear without `docker compose build`.
Rebuild only when `requirements.txt` or the `Dockerfile` changes. For the production-like setup (gunicorn, no mount),
run `docker compose -f docker-compose.yml up --build`.

Or point at any MySQL server:

```bash
export DATABASE_URL="mysql+pymysql://user:password@host:3306/istock?charset=utf8mb4"
flask init-db        # or: flask seed
```

The full DDL is in [`docs/schema_mysql.sql`](docs/schema_mysql.sql). Regenerate it with `flask export-schema`.

## Deployment (zero cost)

1. Create a free MySQL database (for example on Railway) and copy its connection URL.
2. On Render, create a **Blueprint** from this repo (`render.yaml`), then set `DATABASE_URL`.
3. Render runs `flask init-db` and starts gunicorn. Create an admin with `flask create-admin <name>` from the Render shell, or run `flask seed` for demo data.

## CLI commands

| Command | Purpose |
|---|---|
| `flask init-db [--drop]` | Create tables |
| `flask seed [--days 180]` | Reset and load demo data |
| `flask create-admin USER` | Create an administrator |
| `flask recalc` | Recalculate SS / ROP / forecasts for all items (for example from a nightly cron) |
| `flask export-schema` | Write the MySQL DDL to `docs/schema_mysql.sql` |

## Tests

```bash
pytest -q                                   # SQLite
TEST_DATABASE_URL="mysql+pymysql://istock:istock_pass@127.0.0.1:3307/istock_test" pytest -q
MYSQL_TEST_URL="mysql+pymysql://istock:istock_pass@127.0.0.1:3307/istock_test" pytest tests/test_concurrency_mysql.py
```

## Project structure

```
istock/
  __init__.py           app factory, template helpers, CLI commands
  config.py             configuration (DATABASE_URL, SECRET_KEY)
  models.py             13 normalised tables
  auth.py               login/logout, role_required decorator (RBAC)
  settings.py           tunable engine parameters (service level, horizon, ...)
  engine/
    forecasting.py      pure statistics: SMA, OLS regression, back-test, SS, ROP, EOQ
    replenishment.py    decision pipeline, auto-recalculation, auto PO drafting, lead-time learning
    analytics.py        ABC, turnover, dead stock, expiry
  services/
    stock.py            locked receive / issue (FEFO) / adjust
    orders.py           PO approve/receive, client order picking & atomic dispatch
    reports.py          PDF and Excel generation
  views/                Flask blueprints (dashboard, scan API, inventory, partners, orders, analytics/admin)
  templates/ static/    Bootstrap UI, scanner JS
  seed.py               180-day distributor simulation (trend, weekly seasonality, noise, replenishment)
tests/                  pytest suite
docs/schema_mysql.sql   generated MySQL DDL
```

## Decision engine, step by step

```
Data Collection     daily units sold per item from the stock ledger (last 90 days)
Data Processing     mean demand d, std dev σd; supplier lead time L, σL (learned from POs)
Demand Forecasting  SMA(7) and OLS regression; back-test last 14 days; pick the lower MAE
Stock-Level Analysis SS = Z·√(L·σd² + d²·σL²);  ROP = d·L + SS
Reorder Decision    on hand ≤ ROP  →  alert, and a draft PO if none is open
Purchase Recommendation  Q = Σ forecast over (L + review period) + SS − on hand − on order
```
