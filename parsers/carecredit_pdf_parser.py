"""
CareCredit Rewards Mastercard (Synchrony Bank) PDF statement parser.

PDF structure (pdftotext -layout output):
  Page 1:  Summary — account number, statement closing date,
           Previous Balance, New Balance, payment info.
  Transaction section (same page or page 2):
    Transaction Summary header
    OTHER TRANSACTIONS   → payments (parenthesized amounts)
    [STANDARD PURCHASES] → purchases (plain amounts, may be absent)
    FEES                 → fees (plain amounts)
    INTEREST CHARGED     → interest rows (plain amounts)
      "INTEREST CHARGE ON PURCHASES &" (line wraps to "BALANCE TRANSFERS")
      "INTEREST CHARGE ON CASH ADVANCES  0.00" (always zero — skipped)
  Terminated by "2026 Totals Year-to-Date" or "Interest Charge Calculation".

Transaction row format (after pdftotext -layout):
  MM/DD/YYYY  MM/DD/YYYY  [REF_NUM]  DESCRIPTION  (AMOUNT)   ← credit/payment
  MM/DD/YYYY  MM/DD/YYYY  [REF_NUM]  DESCRIPTION   AMOUNT    ← debit

  REF_NUM is ≥10 uppercase-alphanumeric chars (e.g. "8534812F600Y2B8VF").
  Descriptions are multi-word with spaces, so they can't be confused with refs.
  Some rows (interest) have no ref number.

Sign convention (budget-tracker stored amounts):
  Payment   → parenthesized ($X.XX) → credit → stored +X.XX
  Purchase  → plain X.XX            → debit  → stored −X.XX
  Fee       → plain X.XX            → debit  → stored −X.XX
  Interest  → plain X.XX            → debit  → stored −X.XX
  Zero-amount rows are skipped regardless of type.

Reconciliation (closed ledger):
  sum(imported amounts) == prev_balance − new_balance  (to the cent)
  Any deviation is a parser bug.
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

ACCOUNT_NAME = "CareCredit Rewards Mastercard"
SOURCE_SYSTEM = "CareCredit"

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_LAST4_RE = re.compile(
    r'Account Number\s*:\s+(?:xxxx\s+){3}(\d{4})'
)
_CLOSING_RE = re.compile(
    r'Statement Closing Date:\s+(\d{2}/\d{2}/\d{4})'
)
_PREV_RE = re.compile(
    r'Previous Balance\s+\$([\d,]+\.\d{2})'
)
_NEW_RE = re.compile(
    r'New Balance\s+\$([\d,]+\.\d{2})'
)

# Transaction row: two full dates (MM/DD/YYYY), optional reference number
# (≥10 uppercase-alphanumeric chars), description, amount.
# Amount is either parenthesized (credit: payment) or plain (debit).
# Applied to the stripped line, so no leading-whitespace concern.
_TXN_RE = re.compile(
    r'^(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+'
    r'(?:[A-Z0-9]{10,}\s+)?'         # optional reference number
    r'(.+?)\s{2,}'                    # description (non-greedy, 2+ space delimiter)
    r'(\(\$?[\d,]+\.\d{2}\)|\$?[\d,]+\.\d{2})\s*$'  # parenthesized or plain amount
)

# Stop phrases (checked case-insensitively against stripped line)
_STOP_PHRASES = ('year-to-date', 'interest charge calculation')

# Section/total header lines: never transactions even if they matched TXN_RE.
# In practice they all lack a leading date, so TXN_RE rejects them anyway —
# this set is a belt-and-suspenders guard.
_SKIP_STRIPPED: frozenset[str] = frozenset({
    'Transaction Summary',
    'OTHER TRANSACTIONS',
    'STANDARD PURCHASES',
    'FEES',
    'INTEREST CHARGED',
    'BALANCE TRANSFERS',
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_abs(raw: str) -> Optional[float]:
    """Strip parens, $, commas → positive float. Returns None on failure."""
    clean = re.sub(r'[^0-9.]', '', raw)
    try:
        return float(Decimal(clean)) if clean else None
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_carecredit_statement_text(
    text: str,
    filename: str = "statement.pdf",
) -> tuple[list[dict], dict]:
    """
    Parse text extracted from a CareCredit Rewards Mastercard PDF statement.

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
    mc = _CLOSING_RE.search(text)
    if mc:
        closing_date = datetime.strptime(mc.group(1), "%m/%d/%Y").date()

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

        # Enter the transaction region at "Transaction Summary"
        if not in_txn_section:
            if stripped == "Transaction Summary":
                in_txn_section = True
            continue

        # Stop at year-to-date totals or interest rate table
        if any(p in stripped.lower() for p in _STOP_PHRASES):
            break

        # Skip known section/total headers (belt-and-suspenders)
        if stripped in _SKIP_STRIPPED:
            continue

        # Attempt to parse as a transaction row
        m = _TXN_RE.match(stripped)
        if not m:
            continue

        tran_date_str, post_date_str, desc, amt_raw = (
            m.group(1), m.group(2), m.group(3).strip(), m.group(4)
        )

        raw_abs = _parse_abs(amt_raw)
        if raw_abs is None:
            continue

        # Skip zero-amount rows (e.g. INTEREST CHARGE ON CASH ADVANCES)
        if raw_abs == 0.0:
            continue

        # Sign: parenthesized → credit (reduces CC debt) → stored positive
        #       plain          → debit (charge/interest/fee) → stored negative
        is_credit = amt_raw.strip().startswith('(')
        if is_credit:
            db_amount = raw_abs
            direction = "credit"
        else:
            db_amount = -raw_abs
            direction = "debit"

        tran_date = datetime.strptime(tran_date_str, "%m/%d/%Y").date()
        date_str = tran_date.isoformat()

        transactions.append({
            "Date":        date_str,
            "Amount":      db_amount,
            "Direction":   direction,
            "Source":      SOURCE_SYSTEM,
            "Account":     ACCOUNT_NAME,
            "Merchant":    desc,
            "Description": desc,
            "Category":    "",
            "Notes":       f"carecredit:{last4}  from {filename}",
        })

    # ── Reconciliation log ────────────────────────────────────────────────────
    imported_sum = sum(t["Amount"] for t in transactions)
    expected_sum = prev_balance - new_balance
    gap = imported_sum - expected_sum
    pfx = f"[CareCredit {last4} {filename}]"
    print(
        f"{pfx} imported={len(transactions)} rows  sum=${imported_sum:+.2f}"
        f"  prev−new=${expected_sum:+.2f}  gap=${gap:+.2f}"
        + ("  OK" if abs(gap) < 0.02 else "  WARNING — investigate")
    )

    return transactions, {
        "last4":         last4,
        "closing_date":  closing_date,
        "prev_balance":  prev_balance,
        "new_balance":   new_balance,
    }
