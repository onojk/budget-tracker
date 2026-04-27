"""
tests/test_chase_sign_inference.py

Verifies that _parse_chase_transaction_detail assigns the correct sign to
every transaction type found in Chase checking/savings statements.

Chase PDF format convention:
  - Debits (money OUT)  → amount column has an explicit '-' prefix, e.g. -420.00
  - Credits (money IN)  → amount column has NO sign prefix,       e.g.  135.67

The parser must honour the explicit sign rather than falling back to
keyword heuristics, which misfire on phrases like "Ebay Compduytyu6 Payments"
(the word "payment" appears in DEBIT_HINT_WORDS as a substring match).

Tests 1-2 are the regression targets for the sign-inference bug:
  - They FAIL before the fix and PASS after.
Tests 3-7 are keep-alive regression tests that must pass both before and after.
Test 8 is a closed-ledger reconciliation: beg + sum(rows) must equal end.
"""

import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_ocr(period_line: str, transactions: list[str]) -> str:
    """
    Wrap transaction lines in a minimal Chase *_ocr.txt skeleton.

    `period_line`  e.g. "August 15, 2025 through September 15, 2025"
    `transactions` list of raw transaction lines (without leading indent)
    """
    tx_block = "\n".join(f"             {t}" for t in transactions)
    return textwrap.dedent(f"""\
        {period_line}
        Account Number: 000000009765
        *start*transaction detail

                                   Beginning Balance                  100.00

        {tx_block}

        *end*transaction detail
    """)


def _parse(tmp_path: Path, ocr_text: str) -> list[dict]:
    """Write ocr_text to a temp file and call the Chase parser."""
    from ocr_pipeline import _parse_chase_transaction_detail
    f = tmp_path / "test_ocr.txt"
    f.write_text(ocr_text)
    return _parse_chase_transaction_detail(f)


def _row(rows: list[dict], substring: str) -> dict:
    """Return the first row whose Merchant contains `substring`."""
    for r in rows:
        if substring.lower() in r["Merchant"].lower() or substring.lower() in r["Description"].lower():
            return r
    raise AssertionError(
        f"No row with {substring!r} found in:\n"
        + "\n".join(f"  {r['Merchant']}" for r in rows)
    )


# ---------------------------------------------------------------------------
# Individual sign tests
# ---------------------------------------------------------------------------

# --- Test 1: eBay seller proceeds — amount has no sign, must be CREDIT -----
# Bug: "payment" substring in DEBIT_HINT_WORDS fires on "Payments", flipping
# legitimate seller-proceeds credits to negative.
def test_ebay_seller_payment_is_credit(tmp_path):
    ocr = _build_ocr(
        "August 15, 2025 through September 15, 2025",
        [
            "09/09  Ebay Compduytyu6 Payments Qmiehcptxceev6G CCD ID: 1618206000  135.67  167.88",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert rows, "parser returned no rows"
    r = _row(rows, "Ebay")
    assert r["Amount"] > 0, (
        f"eBay seller payment must be a credit (+); got {r['Amount']}"
    )
    assert abs(r["Amount"] - 135.67) < 0.01


# --- Test 2: Zelle Payment From — received money, no sign, must be CREDIT --
# Bug: "payment" fires on "Payment From", storing received Zelle as a debit.
def test_zelle_payment_from_is_credit(tmp_path):
    ocr = _build_ocr(
        "December 13, 2025 through January 15, 2026",
        [
            "12/30  Zelle Payment From Test A User Abc12B34Wxyz  755.00  1090.35",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert rows, "parser returned no rows"
    r = _row(rows, "User")
    assert r["Amount"] > 0, (
        f"Zelle Payment From must be a credit (+); got {r['Amount']}"
    )
    assert abs(r["Amount"] - 755.00) < 0.01


# --- Test 3: PayPal CC Repayment — explicit minus, must be DEBIT -----------
def test_paypal_cc_repayment_is_debit(tmp_path):
    ocr = _build_ocr(
        "August 15, 2025 through September 15, 2025",
        [
            "08/29  Paypal Inst Xfer Ppcr Cc Repayme Web ID: Paypalsi77  -29.00  1519.13",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert rows, "parser returned no rows"
    r = _row(rows, "Ppcr")
    assert r["Amount"] < 0, (
        f"PayPal CC Repayment must be a debit (-); got {r['Amount']}"
    )
    assert abs(r["Amount"] - (-29.00)) < 0.01


# --- Test 4: Payroll direct deposit — no sign, must be CREDIT --------------
def test_payroll_is_credit(tmp_path):
    ocr = _build_ocr(
        "August 15, 2025 through September 15, 2025",
        [
            "08/15  Millennium Healt Payroll PPD ID: 9111111103  1789.19  1458.56",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert rows, "parser returned no rows"
    r = _row(rows, "Payroll")
    assert r["Amount"] > 0, (
        f"Payroll must be a credit (+); got {r['Amount']}"
    )
    assert abs(r["Amount"] - 1789.19) < 0.01


# --- Test 5: Card Purchase — explicit minus, must be DEBIT -----------------
def test_card_purchase_is_debit(tmp_path):
    ocr = _build_ocr(
        "August 15, 2025 through September 15, 2025",
        [
            "08/15  Card Purchase With Pin 08/15 7-Eleven Oceanside CA Card 9241  -6.30  1032.26",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert rows, "parser returned no rows"
    r = _row(rows, "7-Eleven")
    assert r["Amount"] < 0, (
        f"Card Purchase must be a debit (-); got {r['Amount']}"
    )
    assert abs(r["Amount"] - (-6.30)) < 0.01


# --- Test 6: Online Transfer To Savings — explicit minus, must be DEBIT ----
def test_online_transfer_to_savings_is_debit(tmp_path):
    ocr = _build_ocr(
        "August 15, 2025 through September 15, 2025",
        [
            "08/15  08/15 Online Transfer To Sav ...9383 Transaction#: 25864567461  -420.00  1038.56",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert rows, "parser returned no rows"
    r = _row(rows, "Transfer To Sav")
    assert r["Amount"] < 0, (
        f"Online Transfer To Savings must be a debit (-); got {r['Amount']}"
    )
    assert abs(r["Amount"] - (-420.00)) < 0.01


# --- Test 7: Online Transfer From Savings — no sign, must be CREDIT --------
def test_online_transfer_from_savings_is_credit(tmp_path):
    ocr = _build_ocr(
        "August 15, 2025 through September 15, 2025",
        [
            "08/18  Online Transfer From Sav ...9383 Transaction#: 25896542683  500.00  1709.56",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert rows, "parser returned no rows"
    r = _row(rows, "Transfer From Sav")
    assert r["Amount"] > 0, (
        f"Online Transfer From Savings must be a credit (+); got {r['Amount']}"
    )
    assert abs(r["Amount"] - 500.00) < 0.01


# --- Test 8: Closed-ledger reconciliation ----------------------------------
# A mini-statement covering all 7 transaction types.
# beg($100.00) + sum(transactions) must equal end($2,824.56) to the cent.
#
# Expected net:
#   +1789.19  payroll
#   - 420.00  transfer to savings
#   -   29.00  paypal cc repayment
#   + 135.67  ebay seller proceeds
#   + 755.00  zelle received
#   -   6.30  card purchase
#   + 500.00  transfer from savings
#   --------
#   +2724.56  net  →  end = 100.00 + 2724.56 = 2824.56
def test_closed_ledger_reconciliation(tmp_path):
    BEG = 100.00
    END = 2824.56
    EXPECTED_NET = END - BEG   # 2724.56

    ocr = _build_ocr(
        "August 15, 2025 through September 15, 2025",
        [
            "08/15  Millennium Healt Payroll PPD ID: 9111111103  1789.19  1889.19",
            "08/15  08/15 Online Transfer To Sav ...9383 Transaction#: 25864  -420.00  1469.19",
            "08/29  Paypal Inst Xfer Ppcr Cc Repayme Web ID: Paypalsi77  -29.00  1440.19",
            "09/09  Ebay Compduytyu6 Payments Qmiehcptxceev6G CCD ID: 1618  135.67  1575.86",
            "09/12  Zelle Payment From Test A User Abc12B34Wxyz  755.00  2330.86",
            "09/15  Card Purchase 09/14 7-Eleven Oceanside CA Card 9241  -6.30  2324.56",
            "09/15  Online Transfer From Sav ...9383 Transaction#: 25896  500.00  2824.56",
        ],
    )
    rows = _parse(tmp_path, ocr)
    assert len(rows) == 7, f"expected 7 rows, got {len(rows)}"

    actual_net = sum(r["Amount"] for r in rows)
    assert abs(actual_net - EXPECTED_NET) < 0.01, (
        f"Closed-ledger mismatch: beg={BEG} + net={actual_net:.2f} "
        f"≠ end={END}  (expected net={EXPECTED_NET:.2f})"
    )
