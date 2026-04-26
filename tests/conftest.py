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
