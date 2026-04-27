"""
PayPal Cashback Mastercard (Synchrony Bank) PDF statement parser.

PDF structure (pdftotext -layout output):
  Page 1:  Summary — account number, period dates, previous/new balance,
           cashback details, payment coupon.
  Page 2+: Transaction section:
    "Transaction details" header           ← gate phrase
    Date  Reference #  Description  Amount (column header, no leading date)
    Payments  -$X.XX                       (section subtotal, no date)
      MM/DD  [ref]  PAYMENT - THANK YOU  -$X.XX
    Purchases and Other Debits  $X.XX      (section subtotal, no date)
      MM/DD  [ref]  MERCHANT NAME  $X.XX
    Total Fees Charged This Period  $X.XX  (no date)
    Total Interest Charged This Period  $X.XX (no date)
      MM/DD  INTEREST CHARGE ON PURCHASES &  $X.XX
             BALANCE TRANSFERS               (continuation, no date)
      MM/DD  INTEREST CHARGE ON CASH ADVANCES  $0.00  (always zero)
    [blank]
    2026 Year to date fees and interest    ← stop phrase

Transaction row format (all leading-date lines in the section):
  MM/DD  [REF_NUM]  DESCRIPTION  AMOUNT

  REF_NUM is ≥10 uppercase-alphanumeric chars (e.g. "8521853F601DE16EB").
  Interest rows have no ref number (many spaces instead).
  AMOUNT is either "-$X.XX" (credit/payment) or "$X.XX" (debit/purchase/interest).

Sign convention (budget-tracker stored amounts):
  Payment   → -$X.XX → credit → stored +X.XX
  Purchase  → $X.XX  → debit  → stored −X.XX
  Fee       → $X.XX  → debit  → stored −X.XX
  Interest  → $X.XX  → debit  → stored −X.XX
  Zero-amount rows skipped (INTEREST CHARGE ON CASH ADVANCES always $0.00).

Year inference:
  Transaction dates are MM/DD only; year comes from the closing date parsed
  out of "New balance as of MM/DD/YYYY".  If closing month ≤ 2 and
  transaction month ≥ 11 (year-boundary statement), year is decremented.

Reconciliation (closed ledger):
  sum(imported amounts) == prev_balance − new_balance  (to the cent)
  Any deviation is a parser bug.
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

ACCOUNT_NAME = "PayPal Cashback Mastercard"
SOURCE_SYSTEM = "PayPal"

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_LAST4_RE = re.compile(
    r'Account number:\s+(\d{4})'
)
_CLOSING_RE = re.compile(
    r'New balance as of (\d{2}/\d{2}/\d{4})'
)
_PREV_RE = re.compile(
    r'Previous balance as of \d{2}/\d{2}/\d{4}\s+\$([\d,]+\.\d{2})'
)
_NEW_RE = re.compile(
    r'New balance as of \d{2}/\d{2}/\d{4}\s+\$([\d,]+\.\d{2})'
)

# Transaction row: single date (MM/DD), optional reference number
# (≥10 uppercase-alphanumeric chars), description, amount.
# Amount is "-$X.XX" (credit/payment) or "$X.XX" (debit).
# Applied to the stripped line.
_TXN_RE = re.compile(
    r'^(\d{2}/\d{2})\s+'
    r'(?:[A-Z0-9]{10,}\s+)?'         # optional reference number
    r'(.+?)\s{2,}'                    # description (non-greedy, 2+ space delimiter)
    r'(-?\$[\d,]+\.\d{2})\s*$'       # amount: -$X.XX or $X.XX
)

# Stop phrases (checked case-insensitively against stripped line)
_STOP_PHRASES = ('year to date fees and interest', 'interest charge calculation')

# Continuation line that could look like content but must be skipped.
# "BALANCE TRANSFERS" has no date so TXN_RE already rejects it;
# this set is a belt-and-suspenders guard.
_SKIP_STRIPPED: frozenset[str] = frozenset({
    'BALANCE TRANSFERS',
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_abs(raw: str) -> Optional[float]:
    """Strip sign, $, commas → positive float. Returns None on failure."""
    clean = re.sub(r'[^0-9.]', '', raw)
    try:
        return float(Decimal(clean)) if clean else None
    except InvalidOperation:
        return None


def _make_date(mm_dd: str, closing_year: int, closing_month: int) -> str:
    """
    Convert MM/DD + inferred year to ISO date string.
    Adjusts year back by 1 when statement closes in Jan/Feb but the
    transaction month is Nov/Dec (year-boundary statements).
    """
    mm, dd = map(int, mm_dd.split('/'))
    year = closing_year
    if closing_month <= 2 and mm >= 11:
        year -= 1
    return _date(year, mm, dd).isoformat()


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_paypal_statement_text(
    text: str,
    filename: str = "statement.pdf",
) -> tuple[list[dict], dict]:
    """
    Parse text extracted from a PayPal Cashback Mastercard PDF statement.

    Returns:
        (transactions, metadata)

        transactions  list of dicts compatible with Transaction.from_dict()
        metadata      {"last4": str, "closing_date": date, "prev_balance": float,
                       "new_balance": float}
    """
    # ── Metadata extraction ───────────────────────────────────────────────────
    last4 = ""
    m4 = _LAST4_RE.search(text)
    if m4:
        last4 = m4.group(1)

    closing_date: Optional[_date] = None
    closing_year = _date.today().year
    closing_month = _date.today().month
    mc = _CLOSING_RE.search(text)
    if mc:
        closing_date = datetime.strptime(mc.group(1), "%m/%d/%Y").date()
        closing_year = closing_date.year
        closing_month = closing_date.month

    prev_balance = 0.0
    mp = _PREV_RE.search(text)
    if mp:
        prev_balance = _parse_abs(mp.group(1)) or 0.0

    new_balance = 0.0
    mn = _NEW_RE.search(text)
    if mn:
        new_balance = _parse_abs(mn.group(1)) or 0.0

    # ── Transaction section parsing ───────────────────────────────────────────
    transactions: list[dict] = []
    in_txn_section = False

    for line in text.splitlines():
        stripped = line.strip()

        # Enter the transaction region at "Transaction details"
        if not in_txn_section:
            if stripped == "Transaction details":
                in_txn_section = True
            continue

        # Stop at year-to-date block or interest rate table
        if any(p in stripped.lower() for p in _STOP_PHRASES):
            break

        # Skip known continuation/noise lines
        if stripped in _SKIP_STRIPPED:
            continue

        # Attempt to parse as a transaction row
        m = _TXN_RE.match(stripped)
        if not m:
            continue

        date_str_raw, desc, amt_raw = (
            m.group(1), m.group(2).strip(), m.group(3)
        )

        raw_abs = _parse_abs(amt_raw)
        if raw_abs is None:
            continue

        # Skip zero-amount rows (INTEREST CHARGE ON CASH ADVANCES)
        if raw_abs == 0.0:
            continue

        # Sign: leading - → credit (payment, reduces CC balance) → stored positive
        #       no leading - → debit (purchase/fee/interest) → stored negative
        is_credit = amt_raw.startswith('-')
        if is_credit:
            db_amount = raw_abs
            direction = "credit"
        else:
            db_amount = -raw_abs
            direction = "debit"

        date_iso = _make_date(date_str_raw, closing_year, closing_month)

        transactions.append({
            "Date":        date_iso,
            "Amount":      db_amount,
            "Direction":   direction,
            "Source":      SOURCE_SYSTEM,
            "Account":     ACCOUNT_NAME,
            "Merchant":    desc,
            "Description": desc,
            "Category":    "",
            "Notes":       f"paypal:{last4}  from {filename}",
        })

    # ── Reconciliation log ────────────────────────────────────────────────────
    imported_sum = sum(t["Amount"] for t in transactions)
    expected_sum = prev_balance - new_balance
    gap = imported_sum - expected_sum
    pfx = f"[PayPal {last4} {filename}]"
    print(
        f"{pfx} imported={len(transactions)} rows  sum=${imported_sum:+.2f}"
        f"  prev−new=${expected_sum:+.2f}  gap=${gap:+.2f}"
        + ("  OK" if abs(gap) < 0.02 else "  WARNING — investigate")
    )

    return transactions, {
        "last4":        last4,
        "closing_date": closing_date,
        "prev_balance": prev_balance,
        "new_balance":  new_balance,
    }
