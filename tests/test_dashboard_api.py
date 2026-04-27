"""
Tests for the Phase 1 dashboard rebuild:
  - Account.account_type field
  - /api/dashboard endpoint shape, calculations, and sort order
  - No regression on /api/summary

Freshness thresholds:
  - stale = days_since >= 30  (one cycle late)
  - red   = days_since >= 45  (multiple cycles late)
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def make_account(app):
    """Create Account rows in the test DB; clean up after the test."""
    from models import db, Account

    created = []

    def _factory(**kwargs):
        defaults = dict(institution="Test Bank", name="Test Account")
        defaults.update(kwargs)
        acct = Account(**defaults)
        db.session.add(acct)
        db.session.commit()
        created.append(acct.id)
        return acct

    yield _factory

    with app.app_context():
        for aid in created:
            a = db.session.get(Account, aid)
            if a is not None:
                db.session.delete(a)
        db.session.commit()


@pytest.fixture
def seeded_accounts(make_account):
    """10 accounts mirroring production: 2 checking, 1 savings, 2 wallet, 5 credit."""
    today = date.today()
    return [
        make_account(name="BoA Adv Plus",              institution="Bank of America",
                     account_type="checking", last4="0205",
                     last_statement_balance=Decimal("31.10"),
                     last_statement_date=today - timedelta(days=31)),   # stale
        make_account(name="Chase Checking",            institution="JPMorgan Chase",
                     account_type="checking", last4="9765",
                     last_statement_balance=Decimal("276.39"),
                     last_statement_date=today - timedelta(days=13)),
        make_account(name="Chase Savings",             institution="JPMorgan Chase",
                     account_type="savings",  last4="9383",
                     last_statement_balance=Decimal("0.01"),
                     last_statement_date=today - timedelta(days=13)),
        make_account(name="Venmo",                     institution="Venmo",
                     account_type="wallet",   last4=None,
                     last_statement_balance=Decimal("16.93"),
                     last_statement_date=today - timedelta(days=2)),
        make_account(name="PayPal Account",            institution="PayPal",
                     account_type="wallet",   last4=None,
                     last_statement_balance=Decimal("0.00"),
                     last_statement_date=today - timedelta(days=27)),
        make_account(name="CapOne Platinum 0728",      institution="Capital One",
                     account_type="credit",   last4="0728",
                     last_statement_balance=Decimal("601.89"),
                     last_statement_date=today - timedelta(days=19)),
        make_account(name="CapOne Quicksilver 7398",   institution="Capital One",
                     account_type="credit",   last4="7398",
                     last_statement_balance=Decimal("469.71"),
                     last_statement_date=today - timedelta(days=3)),
        make_account(name="Citi Costco Anywhere Visa", institution="Citibank",
                     account_type="credit",   last4="2557",
                     last_statement_balance=Decimal("596.45"),
                     last_statement_date=today - timedelta(days=5)),
        make_account(name="CareCredit Rewards MC",     institution="Synchrony Bank",
                     account_type="credit",   last4="7649",
                     last_statement_balance=Decimal("3740.45"),
                     last_statement_date=today - timedelta(days=10)),
        make_account(name="PayPal Cashback MC",        institution="Synchrony Bank",
                     account_type="credit",   last4="9868",
                     last_statement_balance=Decimal("189.51"),
                     last_statement_date=today - timedelta(days=8)),
    ]


# ---------------------------------------------------------------------------
# account_type field
# ---------------------------------------------------------------------------

def test_account_type_field_accepts_values(app):
    from models import db, Account

    with app.app_context():
        acct = Account(name="__type_test__", institution="Test", account_type="credit")
        db.session.add(acct)
        db.session.commit()
        fetched = db.session.get(Account, acct.id)
        assert fetched.account_type == "credit"
        db.session.delete(fetched)
        db.session.commit()


def test_account_type_field_is_nullable(app):
    from models import db, Account

    with app.app_context():
        acct = Account(name="__type_null__", institution="Test")
        db.session.add(acct)
        db.session.commit()
        fetched = db.session.get(Account, acct.id)
        assert fetched.account_type is None
        db.session.delete(fetched)
        db.session.commit()


# ---------------------------------------------------------------------------
# /api/dashboard — shape
# ---------------------------------------------------------------------------

def test_api_dashboard_returns_200(client, seeded_accounts):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200


def test_api_dashboard_content_type_is_json(client, seeded_accounts):
    resp = client.get("/api/dashboard")
    assert "application/json" in resp.content_type


def test_api_dashboard_has_expected_top_level_keys(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    for key in ("cash_on_hand", "total_debt", "avg_days_since_statement",
                "total_transactions", "interest_90d", "accounts"):
        assert key in data, f"missing key: {key!r}"


def test_api_dashboard_accounts_list_length(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    # seeded_accounts created 10 accounts in the test DB
    assert len(data["accounts"]) == len(seeded_accounts)


def test_api_dashboard_account_entry_has_required_fields(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    required = {"id", "name", "institution", "last4", "balance",
                "as_of", "days_since", "account_type", "stale"}
    for entry in data["accounts"]:
        assert required <= entry.keys(), f"missing fields in {entry}"


# ---------------------------------------------------------------------------
# /api/dashboard — calculations
# ---------------------------------------------------------------------------

def test_cash_on_hand_sums_only_cash_accounts(client, seeded_accounts):
    # checking: 31.10 + 276.39 = 307.49
    # savings:  0.01
    # wallet:   16.93 + 0.00 = 16.93
    # total:    324.43
    data = client.get("/api/dashboard").get_json()
    assert abs(data["cash_on_hand"] - 324.43) < 0.02


def test_total_debt_sums_only_credit_accounts(client, seeded_accounts):
    # 601.89 + 469.71 + 596.45 + 3740.45 + 189.51 = 5598.01
    data = client.get("/api/dashboard").get_json()
    assert abs(data["total_debt"] - 5598.01) < 0.02


def test_interest_90d_is_numeric_and_non_negative(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    assert isinstance(data["interest_90d"], (int, float))
    assert data["interest_90d"] >= 0


def test_total_transactions_is_integer(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    assert isinstance(data["total_transactions"], int)


def test_avg_days_since_statement_is_numeric(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    assert isinstance(data["avg_days_since_statement"], (int, float))


# ---------------------------------------------------------------------------
# /api/dashboard — sort order and stale flag
# ---------------------------------------------------------------------------

def test_cash_accounts_sort_before_credit(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    accounts = data["accounts"]
    cash_types = {"checking", "savings", "wallet"}
    types_in_order = [a["account_type"] for a in accounts]
    last_cash_idx = max(i for i, t in enumerate(types_in_order) if t in cash_types)
    first_credit_idx = min(i for i, t in enumerate(types_in_order) if t == "credit")
    assert last_cash_idx < first_credit_idx, (
        f"cash account at index {last_cash_idx} appears after credit at {first_credit_idx}"
    )


def test_stale_true_when_days_since_gte_30(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    # BoA Adv Plus was set 31 days ago → stale
    boa = next(a for a in data["accounts"] if a["name"] == "BoA Adv Plus")
    assert boa["stale"] is True


def test_stale_false_when_days_since_lt_30(client, seeded_accounts):
    data = client.get("/api/dashboard").get_json()
    # Chase Checking was set 13 days ago → not stale
    chase = next(a for a in data["accounts"] if a["name"] == "Chase Checking")
    assert chase["stale"] is False


# ---------------------------------------------------------------------------
# /api/summary regression — must not break
# ---------------------------------------------------------------------------

def test_api_summary_still_works(client):
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    for key in ("current_balance", "net_this_month", "total_income_this_month",
                "total_spent_this_month", "today", "by_category", "trend"):
        assert key in data
