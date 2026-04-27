"""
Regression tests for sign inference in ocr_pipeline.parse_signed_amount.

Chase statements encode debits with an explicit leading minus and credits
as unsigned positive amounts.  parse_signed_amount must therefore:
  - Respect an explicit sign when present (no flip).
  - When unsigned, infer the sign from context keywords.

Known-bad cases (pre-fix): these FAIL until the scorer is replaced.
Regression guards: these PASS both before and after the fix.
"""
import pytest


# ---------------------------------------------------------------------------
# Known-bad cases — FAIL before the fix
# ---------------------------------------------------------------------------

def test_sign_real_time_payment_credit_recd():
    """Real Time Payment Credit Recd is an inbound wire — must be positive."""
    from ocr_pipeline import parse_signed_amount
    result = parse_signed_amount(
        "2,548.00",
        context="Real Time Payment Credit Recd From Aba/Contr Bnk-021214891 From: 2,548.00",
    )
    assert float(result) == pytest.approx(2548.00), (
        f"Expected +2548.00, got {result}. "
        "'PAYMENT' in debit words beats 'CREDIT' in credit words with the old if/elif."
    )


def test_sign_payment_received_uber():
    """Payment Received from Uber (driver earnings) is inbound — must be positive."""
    from ocr_pipeline import parse_signed_amount
    result = parse_signed_amount(
        "225.40",
        context="Payment Received 04/10 Uber San Francisco CA Card 9241",
    )
    assert float(result) > 0, (
        f"Expected positive, got {result}. "
        "'PAYMENT' fires before any credit keyword, leaving no credit match."
    )


def test_sign_payment_received_venmo():
    """Payment Received via Venmo is inbound — must be positive."""
    from ocr_pipeline import parse_signed_amount
    result = parse_signed_amount(
        "100.00",
        context="Payment Received Venmo* SomeName",
    )
    assert float(result) > 0, (
        f"Expected positive, got {result}."
    )


# ---------------------------------------------------------------------------
# Regression guards — PASS before and after the fix
# ---------------------------------------------------------------------------

def test_sign_real_time_transfer_recd_venmo():
    """Real Time Transfer Recd from Venmo — currently correct, must stay positive."""
    from ocr_pipeline import parse_signed_amount
    result = parse_signed_amount(
        "245.63",
        context="Real Time Transfer Recd From Aba/Contr Bnk-021000021 From: Venmo",
    )
    assert float(result) == pytest.approx(245.63), f"Expected +245.63, got {result}"


def test_sign_card_purchase_debit():
    """Card purchase — unsigned in context, must infer as negative."""
    from ocr_pipeline import parse_signed_amount
    result = parse_signed_amount(
        "40.08",
        context="Card Purchase 01/19 Walmart.Com 800-925-6278 AR Card 9241",
    )
    assert float(result) < 0, f"Expected negative, got {result}"


def test_sign_online_transfer_to_savings():
    """Outgoing online transfer — explicit minus in Chase statement, must stay negative."""
    from ocr_pipeline import parse_signed_amount
    result = parse_signed_amount(
        "-1,300.00",
        context="01/16 Online Transfer To Sav ...9383 Transaction#: 27730316868",
    )
    assert float(result) < 0, f"Expected negative, got {result}"


def test_sign_ach_payroll_credit():
    """ACH payroll deposit — unsigned, must infer as positive."""
    from ocr_pipeline import parse_signed_amount
    result = parse_signed_amount(
        "1835.43",
        context="Millennium Healt Payroll PPD ID: 9111111103",
    )
    assert float(result) > 0, f"Expected positive, got {result}"
