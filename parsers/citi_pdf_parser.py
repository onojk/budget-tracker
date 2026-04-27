"""
Citi Costco Anywhere Visa credit card PDF statement parser.

PDF structure (pdftotext -layout output):
  Page 1:  Billing Period, "New balance as of MM/DD/YY: $X.XX",
           Account Summary table (Previous balance … New balance).
  Pages 2-3: Legal boilerplate — ignored.
  Last page: CARDHOLDER SUMMARY (ignored), then ACCOUNT SUMMARY with
             sub-sections:
               Payments, Credits and Adjustments   → direction: credit
               Standard purchases                  → direction: debit
               Fees Charged                        → direction: debit
               Interest Charged                    → direction: debit
             Terminated by "XXXX totals year-to-date" or
             "Interest charge calculation".

Transaction row format (two layout widths in the wild):
  Narrow (Feb):  MM/DD  [gap]  DESCRIPTION  [gap]  ±$X.XX
  Wide (Mar/Apr): MM/DD  [gap]  MM/DD  [gap]  DESCRIPTION  [gap]  ±$X.XX

  When two dates are present the second is the post date (used for storage).
  When only one date is present it is treated as the post date.

Sign convention (budget-tracker stored amounts):
  Payment   → statement −$X.XX → stored +X.XX  (credit; reduces CC debt)
  Purchase  → statement  $X.XX → stored −X.XX  (debit)
  Fee       → statement  $X.XX → stored −X.XX  (debit)
  Interest  → statement  $X.XX → stored −X.XX  (debit)

Merchant field: raw description as it appears on the statement — no synthesis,
  no cleaning.  Consistent with BoA / Chase / CapOne import convention.

Reconciliation (closed ledger):
  sum(imported amounts) == prev_balance − new_balance  (to the cent)
  Any deviation is a parser bug.
"""

from __future__ import annotations

import re
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from typing import Optional

ACCOUNT_NAME = "Citi Costco Anywhere Visa"
SOURCE_SYSTEM = "Citi"

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_BILLING_RE = re.compile(
    r'Billing Period:\s*(\d{2})/(\d{2})/(\d{2})-(\d{2})/(\d{2})/(\d{2})'
)
_NEW_BAL_RE = re.compile(
    r'New balance as of \d{2}/\d{2}/\d{2}:\s+\$([\d,]+\.\d{2})'
)
_PREV_BAL_RE = re.compile(
    r'Previous balance\s+\$([\d,]+\.\d{2})'
)
_LAST4_RE = re.compile(
    r'Account number ending in[:\s]+(\d{4})'
)

# Transaction row: optional sale date, required post/only date, description,
# amount.  The \s{2,} guard on the amount separator prevents single-space
# words in the description from being mistaken for field delimiters.
_TXN_RE = re.compile(
    r'^(?:(\d{2}/\d{2})\s+)?(\d{2}/\d{2})\s+(.+?)\s{2,}(-?\$[\d,]+\.\d{2})\s*$'
)

# Section sub-headers (matched case-insensitively)
_SECTION_MAP: dict[str, str] = {
    'payments, credits and adjustments': 'payment',
    'standard purchases':                'purchase',
    'fees charged':                      'fee',
    'interest charged':                  'interest',
}

# Phrases that mark the end of the transaction region
_STOP_PHRASES = ('totals year-to-date', 'interest charge calculation', '©')

# Artifact: account number printed sideways on paper statements → digit-only blob
_ARTIFACT_RE = re.compile(r'^\d{5,7}$')

# All-caps name lines (e.g. "TEST USER") — no digits, no $
_NAME_LINE_RE = re.compile(r'^[A-Z][A-Z ]{2,}[A-Z]$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_abs(s: str) -> Optional[float]:
    """Strip sign, $, commas → positive float.  Returns None on parse failure."""
    clean = re.sub(r'[^0-9.]', '', s)
    try:
        return float(Decimal(clean)) if clean else None
    except InvalidOperation:
        return None


def _make_date(mm_dd: str, period_year: int, period_end_month: int) -> str:
    """
    Convert MM/DD + period year to an ISO date string.
    Adjusts the year back by 1 when the statement closes in Jan/Feb but the
    transaction month is Nov/Dec (year-boundary statements).
    """
    mm, dd = map(int, mm_dd.split('/'))
    year = period_year
    if period_end_month <= 2 and mm >= 11:
        year -= 1
    try:
        return _date(year, mm, dd).isoformat()
    except ValueError:
        return f"{year}-{mm:02d}-{dd:02d}"


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_citi_statement_text(
    text: str,
    filename: str = "statement.pdf",
) -> tuple[list[dict], dict]:
    """
    Parse text extracted from a Citi Costco Anywhere Visa PDF statement.

    Returns:
        (transactions, metadata)

        transactions  list of dicts compatible with Transaction.from_dict()
        metadata      {"prev": float, "new": float, "last4": str}
    """
    # ── Summary extraction ────────────────────────────────────────────────────
    last4 = ""
    m4 = _LAST4_RE.search(text)
    if m4:
        last4 = m4.group(1)

    period_year = _date.today().year
    period_end_month = _date.today().month
    mb = _BILLING_RE.search(text)
    if mb:
        period_end_month = int(mb.group(4))
        period_year = 2000 + int(mb.group(6))

    prev_balance = 0.0
    mp = _PREV_BAL_RE.search(text)
    if mp:
        prev_balance = _parse_abs(mp.group(1)) or 0.0

    new_balance = 0.0
    mn = _NEW_BAL_RE.search(text)
    if mn:
        new_balance = _parse_abs(mn.group(1)) or 0.0

    # ── Transaction section parsing ───────────────────────────────────────────
    transactions: list[dict] = []
    in_account_summary = False
    section: Optional[str] = None

    for line in text.splitlines():
        stripped = line.strip()

        # Gate: enter the transaction region at the all-caps ACCOUNT SUMMARY header
        if stripped == "ACCOUNT SUMMARY":
            in_account_summary = True
            continue

        if not in_account_summary:
            continue

        # Stop at year-to-date totals or interest rate calculation table
        lower = stripped.lower()
        if any(p in lower for p in _STOP_PHRASES):
            break

        # Section header detection (case-insensitive)
        new_section = _SECTION_MAP.get(lower)
        if new_section is not None:
            section = new_section
            continue

        # ── Skip lines that are never transactions ──
        if not stripped:
            continue
        if stripped in ("No Activity", "CARDHOLDER SUMMARY"):
            continue
        if stripped.startswith("New Charges"):
            continue
        if stripped.startswith("TOTAL ") or stripped.startswith("Total "):
            continue
        # Column header rows: Sale / Post / Date / Description / Amount
        if re.match(r'^(?:Sale|Post|Date)\b', stripped):
            continue
        # Sideways-printed account number artifact (e.g. "255700")
        if _ARTIFACT_RE.match(stripped):
            continue
        # Cardholder name lines: all-caps letters+spaces, no $ or digits
        if _NAME_LINE_RE.match(stripped) and '$' not in stripped and not re.search(r'\d', stripped):
            continue

        if section is None:
            continue

        # ── Attempt to parse as a transaction row ──
        m = _TXN_RE.match(stripped)
        if not m:
            continue

        _sale_date, post_date_str, desc, amt_raw = (
            m.group(1), m.group(2), m.group(3).strip(), m.group(4)
        )

        raw_abs = _parse_abs(amt_raw)
        if raw_abs is None:
            continue

        date_str = _make_date(post_date_str, period_year, period_end_month)

        # Sign: statement negative → credit (reduces balance) → stored positive
        #       statement positive → debit (increases balance) → stored negative
        is_neg_on_statement = amt_raw.strip().startswith('-')
        if is_neg_on_statement:
            db_amount = raw_abs
            direction = "credit"
        else:
            db_amount = -raw_abs
            direction = "debit"

        transactions.append({
            "Date":        date_str,
            "Amount":      db_amount,
            "Direction":   direction,
            "Source":      SOURCE_SYSTEM,
            "Account":     ACCOUNT_NAME,
            "Merchant":    desc,
            "Description": desc,
            "Category":    "",
            "Notes":       f"citi:{last4}  from {filename}",
        })

    # ── Reconciliation log ────────────────────────────────────────────────────
    imported_sum = sum(t["Amount"] for t in transactions)
    expected_sum = prev_balance - new_balance
    gap = imported_sum - expected_sum
    pfx = f"[Citi {last4} {filename}]"
    print(f"{pfx} imported={len(transactions)} rows  sum=${imported_sum:+.2f}"
          f"  prev−new=${expected_sum:+.2f}  gap=${gap:+.2f}"
          + ("  OK" if abs(gap) < 0.02 else "  WARNING — investigate"))

    return transactions, {"prev": prev_balance, "new": new_balance, "last4": last4}
