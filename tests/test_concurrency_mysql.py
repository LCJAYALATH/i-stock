"""Race-condition test for the 'last box' scenario (NFR: data reliability).

Runs only against MySQL/InnoDB, where SELECT ... FOR UPDATE row locks apply:
    MYSQL_TEST_URL=mysql+pymysql://istock:istock_pass@localhost:3307/istock_test pytest tests/test_concurrency_mysql.py
"""
import os
import threading

import pytest

URL = os.environ.get("MYSQL_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MYSQL_TEST_URL not set")


def test_concurrent_pickers_cannot_oversell():
    from istock import create_app
    from istock.config import TestConfig
    from istock.extensions import db
    from istock.models import Item, StockTransaction
    from istock.services.stock import StockError, issue_stock, receive_stock

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = URL
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_size": 20}

    app = create_app(Cfg)
    with app.app_context():
        db.drop_all()
        db.create_all()
        item = Item(sku="LAST-BOX", name="Last box", unit_cost=1, unit_price=1)
        db.session.add(item)
        db.session.commit()
        receive_stock(item.id, 5)
        item_id = item.id

    workers = 20
    barrier = threading.Barrier(workers)
    results = []

    def picker():
        with app.app_context():
            barrier.wait()
            try:
                issue_stock(item_id, 1)
                results.append("ok")
            except StockError:
                db.session.rollback()
                results.append("rejected")
            finally:
                db.session.remove()

    threads = [threading.Thread(target=picker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with app.app_context():
        item = db.session.get(Item, item_id)
        outs = StockTransaction.query.filter_by(item_id=item_id, txn_type="OUT").count()
        assert results.count("ok") == 5
        assert results.count("rejected") == workers - 5
        assert item.quantity_on_hand == 0
        assert outs == 5
        db.session.remove()  # release metadata locks before dropping tables
        db.drop_all()
