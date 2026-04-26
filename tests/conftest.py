import os

# Force in-memory SQLite BEFORE app.py is imported.
# app.py calls db.create_all() at module level; this env var ensures it
# targets the test DB rather than the real budget.db.
# load_dotenv() uses override=False by default so it won't overwrite this.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from app import app as flask_app
from models import db as _db


@pytest.fixture(scope="session")
def app():
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )
    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_transaction(app):
    """
    Factory fixture: call make_transaction(**overrides) to insert a Transaction
    and get back its integer ID.  Created rows are deleted in teardown (best-
    effort — tests that delete via the API will simply find None and skip).
    """
    from datetime import date as _date
    from models import db, Transaction

    created_ids = []

    def _factory(**kwargs):
        defaults = dict(
            date=_date(2025, 6, 1),
            amount=-10.00,
            merchant="Test Merchant",
            account_name="Test Account",
            source_system="Manual",
        )
        defaults.update(kwargs)
        tx = Transaction(**defaults)
        db.session.add(tx)
        db.session.commit()
        created_ids.append(tx.id)
        return tx.id

    yield _factory

    for tid in created_ids:
        tx = db.session.get(Transaction, tid)
        if tx is not None:
            db.session.delete(tx)
    db.session.commit()
