"""
Tests for parsers/paypal_regular_parser.py

Phase A: import Mass Pay Payment rows and Non Reference Credit Payment rows.
All other row types are skipped and logged.
Intra-file dedup uses the PayPal transaction ID (ID: XXXXXXXX line).
"""

import os
import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "paypal_regular_statement.txt")


@pytest.fixture(scope="module")
def fixture_text():
    with open(FIXTURE) as f:
        return f.read()


@pytest.fixture(scope="module")
def parsed(fixture_text):
    from parsers.paypal_regular_parser import parse_paypal_regular_statement_text
    rows, skipped, meta = parse_paypal_regular_statement_text(fixture_text, "paypal_regular_statement.txt")
    return rows, skipped, meta


# ── Metadata ─────────────────────────────────────────────────────────────────

def test_metadata_period_start(parsed):
    _, _, meta = parsed
    assert meta["period_start"] == "2026-01-01"


def test_metadata_period_end(parsed):
    _, _, meta = parsed
    assert meta["period_end"] == "2026-01-31"


def test_metadata_account_email(parsed):
    _, _, meta = parsed
    assert meta["account_email"] == "onojk123@gmail.com"


# ── Imported rows ─────────────────────────────────────────────────────────────

def test_only_two_rows_imported(parsed):
    rows, _, _ = parsed
    assert len(rows) == 2


def test_mass_pay_row_is_imported(parsed):
    rows, _, _ = parsed
    mass_pay = [r for r in rows if "Mass Pay" in r["Description"]]
    assert len(mass_pay) == 1


def test_mass_pay_amount_is_positive(parsed):
    rows, _, _ = parsed
    mass_pay = next(r for r in rows if "Mass Pay" in r["Description"])
    assert mass_pay["Amount"] > 0


def test_mass_pay_amount_value(parsed):
    rows, _, _ = parsed
    mass_pay = next(r for r in rows if "Mass Pay" in r["Description"])
    assert abs(mass_pay["Amount"] - 99.04) < 0.001


def test_mass_pay_direction_is_credit(parsed):
    rows, _, _ = parsed
    mass_pay = next(r for r in rows if "Mass Pay" in r["Description"])
    assert mass_pay["Direction"] == "credit"


def test_mass_pay_date(parsed):
    rows, _, _ = parsed
    mass_pay = next(r for r in rows if "Mass Pay" in r["Description"])
    assert mass_pay["Date"] == "2026-01-30"


def test_mass_pay_merchant_contains_payer(parsed):
    rows, _, _ = parsed
    mass_pay = next(r for r in rows if "Mass Pay" in r["Description"])
    assert "Tipalti" in mass_pay["Merchant"]


def test_non_reference_credit_is_imported(parsed):
    rows, _, _ = parsed
    ncr = [r for r in rows if "Non Reference Credit" in r["Description"]]
    assert len(ncr) == 1


def test_non_reference_credit_amount(parsed):
    rows, _, _ = parsed
    ncr = next(r for r in rows if "Non Reference Credit" in r["Description"])
    assert abs(ncr["Amount"] - 3.00) < 0.001


def test_non_reference_credit_direction_is_credit(parsed):
    rows, _, _ = parsed
    ncr = next(r for r in rows if "Non Reference Credit" in r["Description"])
    assert ncr["Direction"] == "credit"


def test_account_name_is_paypal_account(parsed):
    rows, _, _ = parsed
    assert all(r["Account"] == "PayPal Account" for r in rows)


def test_source_is_paypal(parsed):
    rows, _, _ = parsed
    assert all(r["Source"] == "PayPal" for r in rows)


# ── Skipped rows ──────────────────────────────────────────────────────────────

def test_express_checkout_is_skipped(parsed):
    rows, skipped, _ = parsed
    imported_descs = [r["Description"] for r in rows]
    assert not any("Express Checkout" in d for d in imported_descs)
    assert any("Express Checkout" in s for s in skipped)


def test_preapproved_payment_is_skipped(parsed):
    rows, skipped, _ = parsed
    imported_descs = [r["Description"] for r in rows]
    assert not any("PreApproved" in d for d in imported_descs)
    assert any("PreApproved" in s for s in skipped)


def test_user_initiated_withdrawal_is_skipped(parsed):
    rows, skipped, _ = parsed
    imported_descs = [r["Description"] for r in rows]
    assert not any("Withdrawal" in d for d in imported_descs)
    assert any("Withdrawal" in s for s in skipped)


def test_general_credit_card_deposit_is_skipped(parsed):
    rows, skipped, _ = parsed
    imported_descs = [r["Description"] for r in rows]
    assert not any("General Credit Card Deposit" in d for d in imported_descs)
    assert any("General Credit Card Deposit" in s for s in skipped)


# ── Intra-file dedup (each txn appears twice in the PDF) ─────────────────────

def test_dedup_produces_exactly_two_rows_not_four(parsed):
    """Each imported transaction appears on two PDF pages — parser must dedup."""
    rows, _, _ = parsed
    assert len(rows) == 2


def test_dedup_no_duplicate_transaction_ids(parsed):
    rows, _, _ = parsed
    notes_ids = [r["Notes"] for r in rows]
    # extract ID from Notes field
    import re
    ids = [re.search(r'ID:(\S+)', n).group(1) for n in notes_ids if re.search(r'ID:(\S+)', n)]
    assert len(ids) == len(set(ids))
