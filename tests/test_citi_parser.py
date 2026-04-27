"""
Tests for parsers/citi_pdf_parser.py

Fixture: tests/fixtures/citi_statement.txt
  Account: Citi Costco Anywhere Visa, ending in 9999
  Period:  01/10/26-02/07/26

  Summary:
    Previous balance  $500.00
    Payments         -$100.00
    Purchases         +$50.00   (1 row: COSTCO WHOLESALE #1234 VISTA CA)
    Fees              +$25.00   (1 row: LATE FEE)
    Interest          +$10.00   (INTEREST CHARGED TO STANDARD PURCH)
    New balance       $485.00

  Imported rows (4 total):
    1. ONLINE PAYMENT, THANK YOU       01/20  +$100.00  credit  (payment)
    2. COSTCO WHOLESALE #1234 VISTA CA 01/16   -$50.00  debit   (purchase, post date)
    3. LATE FEE                        01/25   -$25.00  debit   (fee)
    4. INTEREST CHARGED TO STANDARD PURCH  02/07  -$10.00  debit   (interest)

  Reconciliation:
    sum(imported) = +100 - 50 - 25 - 10 = +$15.00
    prev - new    = 500 - 485           = +$15.00  ✓ (closed ledger)
"""
from pathlib import Path
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "citi_statement.txt"


def get_parser():
    from parsers.citi_pdf_parser import parse_citi_statement_text
    return parse_citi_statement_text


def load_fixture() -> str:
    return FIXTURE.read_text()


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_parse_extracts_last4():
    _, meta = get_parser()(load_fixture(), "citi_statement.txt")
    assert meta["last4"] == "9999"


def test_parse_extracts_prev_and_new_balance():
    _, meta = get_parser()(load_fixture(), "citi_statement.txt")
    assert meta["prev"] == pytest.approx(500.00, abs=0.01)
    assert meta["new"]  == pytest.approx(485.00, abs=0.01)


# ---------------------------------------------------------------------------
# Transaction count
# ---------------------------------------------------------------------------

def test_parse_returns_4_transactions():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    assert len(rows) == 4


# ---------------------------------------------------------------------------
# Payment row
# ---------------------------------------------------------------------------

def test_parse_payment_is_credit():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    payment = next(r for r in rows if r["Direction"] == "credit")
    assert payment["Amount"] == pytest.approx(100.00, abs=0.01)


def test_parse_payment_merchant_is_raw_description():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    payment = next(r for r in rows if r["Direction"] == "credit")
    assert payment["Merchant"] == "ONLINE PAYMENT, THANK YOU"


def test_parse_payment_date():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    payment = next(r for r in rows if r["Direction"] == "credit")
    assert payment["Date"] == "2026-01-20"


# ---------------------------------------------------------------------------
# Purchase row
# ---------------------------------------------------------------------------

def test_parse_purchase_is_debit():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    purchase = next(r for r in rows if "COSTCO WHOLESALE" in r["Merchant"])
    assert purchase["Direction"] == "debit"
    assert purchase["Amount"] == pytest.approx(-50.00, abs=0.01)


def test_parse_purchase_merchant_is_raw_description():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    purchase = next(r for r in rows if "COSTCO WHOLESALE" in r["Merchant"])
    assert purchase["Merchant"] == "COSTCO WHOLESALE #1234 VISTA CA"


def test_parse_purchase_uses_post_date():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    purchase = next(r for r in rows if "COSTCO WHOLESALE" in r["Merchant"])
    assert purchase["Date"] == "2026-01-16"


# ---------------------------------------------------------------------------
# Fee row
# ---------------------------------------------------------------------------

def test_parse_fee_is_debit():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    fee = next(r for r in rows if "LATE FEE" in r["Merchant"])
    assert fee["Direction"] == "debit"
    assert fee["Amount"] == pytest.approx(-25.00, abs=0.01)


def test_parse_fee_merchant_is_raw_description():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    fee = next(r for r in rows if "LATE FEE" in r["Merchant"])
    assert fee["Merchant"] == "LATE FEE"


# ---------------------------------------------------------------------------
# Interest row
# ---------------------------------------------------------------------------

def test_parse_interest_is_debit():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    interest = next(r for r in rows if "INTEREST CHARGED" in r["Merchant"])
    assert interest["Direction"] == "debit"
    assert interest["Amount"] == pytest.approx(-10.00, abs=0.01)


def test_parse_interest_date_is_period_close():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    interest = next(r for r in rows if "INTEREST CHARGED" in r["Merchant"])
    assert interest["Date"] == "2026-02-07"


def test_parse_interest_merchant_is_raw_description():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    interest = next(r for r in rows if "INTEREST CHARGED" in r["Merchant"])
    assert interest["Merchant"] == "INTEREST CHARGED TO STANDARD PURCH"


# ---------------------------------------------------------------------------
# Account / source metadata on every row
# ---------------------------------------------------------------------------

def test_parse_account_name():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    assert all(r["Account"] == "Citi Costco Anywhere Visa" for r in rows)


def test_parse_source_system():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    assert all(r["Source"] == "Citi" for r in rows)


# ---------------------------------------------------------------------------
# Noise / skip verification
# ---------------------------------------------------------------------------

def test_total_fees_line_not_imported():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    assert not any("TOTAL FEES" in r.get("Merchant", "") for r in rows)


def test_total_interest_line_not_imported():
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    assert not any("TOTAL INTEREST" in r.get("Merchant", "") for r in rows)


def test_column_header_rows_not_imported():
    """'Sale', 'Date', 'Description' column-header words must not appear as merchants."""
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    assert not any(r["Merchant"] in ("Sale", "Date", "Description", "Amount") for r in rows)


def test_account_number_artifact_not_imported():
    """The '255700' page artifact must not appear as a transaction."""
    rows, _ = get_parser()(load_fixture(), "citi_statement.txt")
    assert not any("255700" in r.get("Merchant", "") for r in rows)


# ---------------------------------------------------------------------------
# Closed-ledger reconciliation
# ---------------------------------------------------------------------------

def test_reconciliation_closes_to_zero():
    rows, meta = get_parser()(load_fixture(), "citi_statement.txt")
    imported_sum = sum(r["Amount"] for r in rows)
    expected_sum = meta["prev"] - meta["new"]
    assert abs(imported_sum - expected_sum) < 0.02
