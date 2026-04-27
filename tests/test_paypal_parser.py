"""
Tests for parsers/paypal_pdf_parser.py

Fixture: tests/fixtures/paypal_statement.txt
  Account: PayPal Cashback Mastercard, ending in 8888
  Period close: 01/31/2026

  Summary:
    Previous balance as of 01/02/2026   $300.00
    Payments                            -$50.00  → stored +50.00  credit
    Purchases & other debits            +$45.00
    Interest charges                    +$10.00
    New balance as of 01/31/2026        $305.00

  Imported rows (4 total):
    1. PAYMENT - THANK YOU        01/05   +$50.00  credit
    2. SOME MERCHANT PURCHASE     01/15   -$30.00  debit
    3. SOME KIND OF FEE           01/20   -$15.00  debit
    4. INTEREST CHARGE ON PURCHASES &  01/31  -$10.00  debit

  Skipped rows:
    - INTEREST CHARGE ON CASH ADVANCES  $0.00  (zero-amount skip)
    - BALANCE TRANSFERS continuation line (no date → regex miss)

  Reconciliation:
    sum(imported) = +50 - 30 - 15 - 10 = -$5.00
    prev - new    = 300 - 305          = -$5.00  ✓ (closed ledger)
"""
from pathlib import Path
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "paypal_statement.txt"


def get_parser():
    from parsers.paypal_pdf_parser import parse_paypal_statement_text
    return parse_paypal_statement_text


def load_fixture() -> str:
    return FIXTURE.read_text()


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_parse_extracts_last4():
    _, meta = get_parser()(load_fixture(), "paypal_statement.txt")
    assert meta["last4"] == "8888"


def test_parse_extracts_closing_date():
    from datetime import date
    _, meta = get_parser()(load_fixture(), "paypal_statement.txt")
    assert meta["closing_date"] == date(2026, 1, 31)


def test_parse_extracts_prev_and_new_balance():
    _, meta = get_parser()(load_fixture(), "paypal_statement.txt")
    assert meta["prev_balance"] == pytest.approx(300.00, abs=0.01)
    assert meta["new_balance"]  == pytest.approx(305.00, abs=0.01)


# ---------------------------------------------------------------------------
# Transaction count
# ---------------------------------------------------------------------------

def test_parse_returns_4_transactions():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    assert len(rows) == 4


# ---------------------------------------------------------------------------
# Payment row (leading -$ → credit → stored positive)
# ---------------------------------------------------------------------------

def test_parse_payment_is_credit():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    payment = next(r for r in rows if r["Direction"] == "credit")
    assert payment["Amount"] == pytest.approx(50.00, abs=0.01)


def test_parse_payment_merchant_is_raw_description():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    payment = next(r for r in rows if r["Direction"] == "credit")
    assert payment["Merchant"] == "PAYMENT - THANK YOU"


def test_parse_payment_date():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    payment = next(r for r in rows if r["Direction"] == "credit")
    assert payment["Date"] == "2026-01-05"


# ---------------------------------------------------------------------------
# Purchase row (plain $ → debit → stored negative)
# ---------------------------------------------------------------------------

def test_parse_purchase_is_debit():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    purchase = next(r for r in rows if "SOME MERCHANT" in r["Merchant"])
    assert purchase["Direction"] == "debit"
    assert purchase["Amount"] == pytest.approx(-30.00, abs=0.01)


def test_parse_purchase_merchant_is_raw_description():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    purchase = next(r for r in rows if "SOME MERCHANT" in r["Merchant"])
    assert purchase["Merchant"] == "SOME MERCHANT PURCHASE"


# ---------------------------------------------------------------------------
# Fee row
# ---------------------------------------------------------------------------

def test_parse_fee_is_debit():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    fee = next(r for r in rows if "FEE" in r["Merchant"])
    assert fee["Direction"] == "debit"
    assert fee["Amount"] == pytest.approx(-15.00, abs=0.01)


# ---------------------------------------------------------------------------
# Interest row
# ---------------------------------------------------------------------------

def test_parse_interest_is_debit():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    interest = next(r for r in rows if "INTEREST CHARGE ON PURCHASES" in r["Merchant"])
    assert interest["Direction"] == "debit"
    assert interest["Amount"] == pytest.approx(-10.00, abs=0.01)


def test_parse_interest_merchant_is_raw_description():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    interest = next(r for r in rows if "INTEREST CHARGE ON PURCHASES" in r["Merchant"])
    assert interest["Merchant"] == "INTEREST CHARGE ON PURCHASES &"


# ---------------------------------------------------------------------------
# Specific skip tests (user-required)
# ---------------------------------------------------------------------------

def test_one_interest_transaction_per_statement_not_zero_or_two():
    """'BALANCE TRANSFERS' continuation line must not create a second interest row.

    The fixture has 'INTEREST CHARGE ON PURCHASES &' on one line and
    'BALANCE TRANSFERS' on the next with no date or amount.  Only the first
    line matches TXN_RE, so exactly 1 interest transaction must be imported —
    not 0 and not 2.
    """
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    interest_rows = [r for r in rows if "INTEREST CHARGE" in r["Merchant"]]
    assert len(interest_rows) == 1


def test_cash_advance_zero_row_is_skipped():
    """'INTEREST CHARGE ON CASH ADVANCES  $0.00' must be explicitly skipped.

    The row matches TXN_RE (has a date) but the amount is $0.00.  No
    zero-amount row should appear in output.
    """
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    zero_rows = [r for r in rows if abs(r["Amount"]) < 0.01]
    assert zero_rows == []


# ---------------------------------------------------------------------------
# Account / source metadata on every row
# ---------------------------------------------------------------------------

def test_parse_account_name():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    assert all(r["Account"] == "PayPal Cashback Mastercard" for r in rows)


def test_parse_source_system():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    assert all(r["Source"] == "PayPal" for r in rows)


# ---------------------------------------------------------------------------
# Noise / skip verification
# ---------------------------------------------------------------------------

def test_total_fees_line_not_imported():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    assert not any("Total Fees" in r.get("Merchant", "") for r in rows)


def test_total_interest_line_not_imported():
    rows, _ = get_parser()(load_fixture(), "paypal_statement.txt")
    assert not any("Total Interest" in r.get("Merchant", "") for r in rows)


# ---------------------------------------------------------------------------
# Closed-ledger reconciliation
# ---------------------------------------------------------------------------

def test_reconciliation_closes_to_zero():
    rows, meta = get_parser()(load_fixture(), "paypal_statement.txt")
    imported_sum = sum(r["Amount"] for r in rows)
    expected_sum = meta["prev_balance"] - meta["new_balance"]
    assert abs(imported_sum - expected_sum) < 0.02
