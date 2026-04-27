"""
Tests for parsers/capitalone_pdf_parser.py

Fixture: tests/fixtures/capone_statement.txt
  Card:   Test Rewards Card | World Mastercard ending in 9999
  Period: Jan 10, 2026 - Feb 07, 2026
  Summary:
    Previous Balance  $100.00
    Payments         - $60.00
    Transactions     + $45.00   (2 purchases: Amazon $20 + DoorDash $25)
    Fees             + $25.00   (PAST DUE FEE)
    Interest         +  $5.00
    New Balance       $115.00

  Imported rows (5 total):
    1. CAPITAL ONE MOBILE PYMT  Jan 30  +$60.00  credit   (payment)
    2. AMAZON RETA* ...         Jan 15  -$20.00  debit    (purchase)
    3. DD *DOORDASH TACOBELL    Jan 22  -$25.00  debit    (purchase)
    4. PAST DUE FEE             Jan 30  -$25.00  debit    (fee)
    5. Interest Charged         Feb 07  - $5.00  debit    (synthetic)

  Reconciliation:
    sum(imported) = +60 - 20 - 25 - 25 - 5 = -$15.00
    prev - new    = 100 - 115              = -$15.00  ✓ (closed ledger)
"""
from pathlib import Path
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "capone_statement.txt"


def get_parser():
    from parsers.capitalone_pdf_parser import parse_capitalone_statement_text
    return parse_capitalone_statement_text


def load_fixture() -> str:
    return FIXTURE.read_text()


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------

def test_parse_extracts_last4():
    _, meta = get_parser()(load_fixture(), "capone_statement.txt")
    assert meta["last4"] == "9999"


def test_parse_extracts_card_name():
    _, meta = get_parser()(load_fixture(), "capone_statement.txt")
    assert "Test Rewards Card" in meta["card_name"]


def test_parse_extracts_prev_and_new_balance():
    _, meta = get_parser()(load_fixture(), "capone_statement.txt")
    assert meta["prev"] == pytest.approx(100.00, abs=0.01)
    assert meta["new"]  == pytest.approx(115.00, abs=0.01)


# ---------------------------------------------------------------------------
# Sign / direction tests
# ---------------------------------------------------------------------------

def test_parse_payment_stored_as_positive_credit():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    payments = [r for r in rows if "PYMT" in r["Description"]]
    assert len(payments) == 1
    r = payments[0]
    assert r["Amount"] == pytest.approx(60.00, abs=0.01)
    assert r["Direction"] == "credit"


def test_parse_purchase_stored_as_negative_debit():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    amazon = [r for r in rows if "AMAZON" in r["Description"]]
    assert len(amazon) == 1
    r = amazon[0]
    assert r["Amount"] == pytest.approx(-20.00, abs=0.01)
    assert r["Direction"] == "debit"


def test_parse_fee_stored_as_negative_debit():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    fee = [r for r in rows if "PAST DUE FEE" in r["Description"]]
    assert len(fee) == 1
    r = fee[0]
    assert r["Amount"] == pytest.approx(-25.00, abs=0.01)
    assert r["Direction"] == "debit"


def test_parse_interest_stored_as_negative_debit():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    interest = [r for r in rows if r["Description"] == "Interest Charged"]
    assert len(interest) == 1
    r = interest[0]
    assert r["Amount"] == pytest.approx(-5.00, abs=0.01)
    assert r["Direction"] == "debit"
    assert r["Category"] == "Interest"


# ---------------------------------------------------------------------------
# Date tests
# ---------------------------------------------------------------------------

def test_parse_date_uses_billing_year():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    amazon = [r for r in rows if "AMAZON" in r["Description"]][0]
    assert amazon["Date"] == "2026-01-15"


def test_parse_interest_date_is_closing_date():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    interest = [r for r in rows if r["Description"] == "Interest Charged"][0]
    assert interest["Date"] == "2026-02-07"


# ---------------------------------------------------------------------------
# Merchant cleaning tests
# ---------------------------------------------------------------------------

def test_parse_merchant_strips_dd_prefix():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    doordash = [r for r in rows if "DOORDASH" in r["Description"]][0]
    # Merchant should not start with "DD *"
    assert not doordash["Merchant"].startswith("DD *")


def test_parse_merchant_strips_trailing_state():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    amazon = [r for r in rows if "AMAZON" in r["Description"]][0]
    # Merchant should not end with a bare state abbreviation "WA"
    assert not amazon["Merchant"].endswith("WA")


# ---------------------------------------------------------------------------
# Account / source tag tests
# ---------------------------------------------------------------------------

def test_parse_source_tag():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    assert all(r["Source"] == "Capital One" for r in rows)


def test_parse_account_contains_last4():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    assert all("9999" in r["Account"] for r in rows)


def test_parse_notes_contains_last4_and_filename():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    assert all("9999" in r["Notes"] for r in rows)
    assert all("capone_statement.txt" in r["Notes"] for r in rows)


# ---------------------------------------------------------------------------
# Row count
# ---------------------------------------------------------------------------

def test_parse_returns_correct_row_count():
    rows, _ = get_parser()(load_fixture(), "capone_statement.txt")
    assert len(rows) == 5, (
        f"Expected 5 rows (1 payment + 2 purchases + 1 fee + 1 interest), "
        f"got {len(rows)}: {[r['Description'] for r in rows]}"
    )


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def test_reconciliation_matches_balance_delta():
    """
    Capital One statements are closed ledgers.
    sum(imported amounts) must equal prev_balance - new_balance to the cent.
    """
    rows, meta = get_parser()(load_fixture(), "capone_statement.txt")
    imported_sum = sum(r["Amount"] for r in rows)
    expected = meta["prev"] - meta["new"]
    assert abs(imported_sum - expected) < 0.02, (
        f"Reconciliation failed: imported={imported_sum:.2f}  "
        f"prev-new={expected:.2f}  gap={imported_sum - expected:.2f}"
    )


# ---------------------------------------------------------------------------
# Edge case: zero-purchase statement
# ---------------------------------------------------------------------------

def test_parse_zero_purchase_statement_imports_payment_and_interest(tmp_path):
    """
    A statement with no purchases (like Feb 0728) should still import the
    payment row and the interest synthetic row.
    """
    text = "\n".join([
        "Page 1 of 3",
        "Empty Card | World Mastercard ending in 0001",
        "Feb 01, 2026 - Mar 01, 2026 | 28 days in Billing Cycle",
        "Previous Balance $200.00",
        "Payments - $30.00",
        "Other Credits $0.00",
        "Transactions + $0.00",
        "Cash Advances + $0.00",
        "Fees Charged + $0.00",
        "Interest Charged + $10.00",
        "New Balance = $180.00",
        "Transactions",
        "JANE SMITH #0001: Payments, Credits and Adjustments",
        "Trans Date Post Date Description Amount",
        "Feb 15 Feb 15 CAPITAL ONE MOBILE PYMT - $30.00",
        "JANE SMITH #0001: Transactions",
        "Trans Date Post Date Description Amount",
        "Total Transactions for This Period $0.00",
        "Fees",
        "Trans Date Post Date Description Amount",
        "Total Fees for This Period $0.00",
        "Interest Charged",
        "Interest Charge on Purchases $10.00",
        "Interest Charge on Cash Advances $0.00",
        "Interest Charge on Other Balances $0.00",
        "Total Interest for This Period $10.00",
    ])
    rows, meta = get_parser()(text, "empty_card.pdf")
    assert meta["prev"] == pytest.approx(200.00)
    assert meta["new"]  == pytest.approx(180.00)
    # Payment (+30) + Interest (-10) = 2 rows
    assert len(rows) == 2
    amounts = sorted(r["Amount"] for r in rows)
    assert amounts[0] == pytest.approx(-10.00)  # interest
    assert amounts[1] == pytest.approx(30.00)    # payment
    # Reconciliation: +30 - 10 = +20 = 200 - 180
    imported_sum = sum(r["Amount"] for r in rows)
    assert abs(imported_sum - (meta["prev"] - meta["new"])) < 0.02
