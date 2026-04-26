"""
Smoke tests and bug-regression tests for the budget tracker Flask app.
"""
from datetime import date


def test_root_redirects_to_dashboard(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers["Location"]


def test_dashboard_loads(client):
    resp = client.get("/dashboard", follow_redirects=True)
    assert resp.status_code == 200


def test_api_summary_returns_expected_keys(client):
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    assert "application/json" in resp.content_type
    data = resp.get_json()
    for key in (
        "current_balance",
        "net_this_month",
        "total_income_this_month",
        "total_spent_this_month",
        "today",
        "by_category",
        "trend",
    ):
        assert key in data, f"missing key in /api/summary response: {key!r}"


def test_api_transactions_returns_list(client):
    resp = client.get("/api/transactions?limit=5")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "transactions" in data
    assert isinstance(data["transactions"], list)


# ---------------------------------------------------------------------------
# Regression: bug #4
# /capone_csv_summary returned a raw Python list.  Flask 3.x auto-jsonifies
# lists, so this is not a runtime error on Flask 3.1+, but we keep the test
# to pin the contract: the endpoint must return HTTP 200 with JSON content.
# ---------------------------------------------------------------------------
def test_capone_csv_summary_is_json(client):
    resp = client.get("/capone_csv_summary")
    assert resp.status_code == 200
    assert "application/json" in resp.content_type
    assert isinstance(resp.get_json(), list)


# ---------------------------------------------------------------------------
# Regression: bug #3
# update_transaction_json (PUT /api/transactions/<id>) had a NameError in its
# except block: it referenced undefined 'txn' instead of 'tx'.  When category
# learning raised, a secondary NameError masked the real error and the endpoint
# returned 500 instead of logging a warning and continuing.
# ---------------------------------------------------------------------------
def test_update_transaction_category_learning_error_is_handled(client, app, monkeypatch):
    from models import db, Transaction
    import app as app_module

    # Seed one transaction so we have a valid ID to update.
    with app.app_context():
        tx = Transaction(
            date=date(2025, 3, 10),
            amount=-25.00,
            merchant="Bug3 Merchant",
            account_name="Test Account",
            source_system="Manual",
        )
        db.session.add(tx)
        db.session.commit()
        tx_id = tx.id

    # Force the category-learning helper to raise so the except branch runs.
    def _raise(*a, **k):
        raise RuntimeError("simulated learning failure")

    monkeypatch.setattr(app_module, "learn_category_from_transaction", _raise)

    resp = client.put(
        f"/api/transactions/{tx_id}",
        json={"category": "Dining"},
        content_type="application/json",
    )

    # Before the fix: the except block raised NameError(txn) -> 500.
    # After the fix: warning is logged, execution continues -> 200.
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. Body: {resp.data!r}"
    )
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["transaction"]["category"] == "Dining"
