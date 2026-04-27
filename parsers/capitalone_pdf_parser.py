"""
Capital One credit card PDF statement parser.

PDF structure (text extracted by pdfplumber, all pages concatenated):
  Page 1:  Summary — card name, last4, billing period, Previous Balance,
           Payments, Transactions, Fees, Interest, New Balance.
  Page 2:  Legal boilerplate — skip.
  Page N:  Transaction detail:
             [NAME] #[LAST4]: Payments, Credits and Adjustments
               Trans Date Post Date Description Amount
               Mon DD Mon DD DESCRIPTION - $X.XX
             [NAME] #[LAST4]: Transactions
               Trans Date Post Date Description Amount
               Mon DD Mon DD DESCRIPTION $X.XX
             [NAME] #[LAST4]: Total Transactions $X.XX
             ...  (additional authorized-user cardholder sections)
             Fees
               Trans Date Post Date Description Amount
               Mon DD Mon DD DESCRIPTION $X.XX
             Interest Charged
               Interest Charge on Purchases $X.XX
               Interest Charge on Cash Advances $X.XX
               ...
               Total Interest for This Period $X.XX
  Last page: Interest Charge Calculation — skip.

Row format:
  Mon D[D]  Mon D[D]  DESCRIPTION  AMOUNT
  AMOUNT is "$X.XX" (purchase/fee) or "- $X.XX" (payment/credit).

Sign convention (budget tracker — stored amounts):
  Purchase   → negative  (spending)
  Payment    → positive  (reduces CC debt; mirrors Chase outflow)
  Fee        → negative  (cost)
  Interest   → negative  (cost); synthetic row, date = closing date

Reconciliation:
  sum(imported amounts) == prev_balance − new_balance  (to the cent)
  Capital One statements are closed ledgers — any deviation is a parser bug.

Authorized users:
  All cardholder sections are imported. Authorized-user charges go toward
  the same account balance and are legitimate spending.
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r'^(.+?)\s*\|\s*.+?ending in (\d{4})',
    re.MULTILINE,
)
_PERIOD_RE = re.compile(
    r'([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})\s*[-–]\s*([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})'
)
_PREV_RE = re.compile(r'Previous Balance\s+\$?([\d,]+\.\d{2})')
_NEW_RE  = re.compile(r'New Balance\s*=\s*\$?([\d,]+\.\d{2})')

# Section header: "JOHN DOE #9999: Payments, Credits and Adjustments"
_SECTION_PAY_RE = re.compile(r'^.+#\d{4}:\s*Payments, Credits and Adjustments\s*$')
_SECTION_TXN_RE = re.compile(r'^.+#\d{4}:\s*Transactions\s*$')

# Interest summary line: "Interest Charge on Purchases $13.40"
_INTEREST_LINE_RE = re.compile(
    r'^Interest Charge on (?:Purchases|Cash Advances|Other Balances)\s+\$?([\d,]+\.\d{2})\s*$'
)

# Transaction / fee row: Mon DD Mon DD DESCRIPTION AMOUNT
_ROW_RE = re.compile(
    r'^([A-Z][a-z]{2})\s+(\d{1,2})\s+[A-Z][a-z]{2}\s+\d{1,2}\s+(.+?)\s+'
    r'(-\s*\$[\d,]+\.\d{2}|\$[\d,]+\.\d{2})\s*$'
)

# Merchant cleaning
_STRIP_PREFIX_RE = re.compile(r'^(?:DD \*|SQ \*|TST\*|SP \*)\s*', re.IGNORECASE)
# Order codes contain at least one digit (e.g. "BP9RH5QU1", "ABC123SEATTLEWA").
# Requiring a digit avoids stripping legitimate all-caps words like "MOBILE".
_STRIP_CODE_RE   = re.compile(r'\s+\b(?=[A-Z0-9]*\d)[A-Z0-9]{6,}\b')

_STATES = frozenset({
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY",
})

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,  "May": 5,  "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dollar(raw: str) -> Optional[float]:
    s = re.sub(r"[^0-9.]", "", raw)
    if not s:
        return None
    try:
        return float(Decimal(s))
    except InvalidOperation:
        return None


def _parse_amount(raw: str) -> Optional[float]:
    """Parse "$X.XX" → positive float, "- $X.XX" → negative float."""
    s = raw.strip()
    neg = s.startswith("-")
    v = _parse_dollar(s)
    return (-v if neg else v) if v is not None else None


def _make_date(month_abbr: str, day: str, year: int) -> str:
    m = _MONTHS.get(month_abbr)
    if m is None:
        return f"{year}-01-01"
    try:
        return _date(year, m, int(day)).isoformat()
    except ValueError:
        return f"{year}-01-01"


def _clean_merchant(raw: str) -> str:
    s = _STRIP_PREFIX_RE.sub("", raw.strip())
    s = _STRIP_CODE_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip trailing no-space CityState token (e.g. "SEATTLEWA", "SCOTSDALEAZ").
    # Require city portion ≥ 3 chars to avoid false positives like "PYMT" → "PY"+"MT".
    words = s.split()
    if words:
        last = words[-1]
        if last.isupper() and len(last) >= 5 and last[-2:] in _STATES:
            city_part = last[:-2]
            if len(city_part) >= 3:
                words = words[:-1]
    s = " ".join(words).strip().rstrip("*").strip()
    return s or raw.strip()


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_capitalone_statement_text(
    text: str,
    filename: str = "statement.pdf",
) -> tuple[list[dict], dict]:
    """
    Parse text extracted from a Capital One credit card PDF statement.

    Returns:
        (transactions, metadata)

        transactions: list of dicts compatible with Transaction.from_dict
        metadata: {
            "prev":      float — previous balance
            "new":       float — new balance
            "last4":     str   — last 4 digits of the account
            "card_name": str   — product name (e.g. "Platinum Card")
        }
    """
    lines = text.splitlines()

    # ── Header / summary ─────────────────────────────────────────────────────
    last4 = ""
    card_name = ""
    m = _HEADER_RE.search(text)
    if m:
        card_name = m.group(1).strip()
        last4 = m.group(2)

    account_label = f"CapOne {_short_name(card_name)} {last4}" if last4 else "Capital One"

    closing_year = datetime.now().year
    closing_date_str = ""
    mp = _PERIOD_RE.search(text)
    if mp:
        closing_year = int(mp.group(6))
        closing_date_str = _make_date(mp.group(4), mp.group(5), closing_year)

    prev_balance = 0.0
    pm = _PREV_RE.search(text)
    if pm:
        prev_balance = _parse_dollar(pm.group(1)) or 0.0

    new_balance = 0.0
    nm = _NEW_RE.search(text)
    if nm:
        new_balance = _parse_dollar(nm.group(1)) or 0.0

    # ── Transaction section parsing ───────────────────────────────────────────
    transactions: list[dict] = []
    section = None          # "payments" | "transactions" | "fees" | "interest" | None
    total_interest = 0.0
    in_tx_region = False    # True once we've passed "Transactions\n"

    for line in lines:
        stripped = line.strip()

        # Enter the transactions region
        if stripped == "Transactions":
            in_tx_region = True
            continue

        if not in_tx_region:
            continue

        # Stop at the Interest Charge Calculation table (last page)
        if stripped.startswith("Interest Charge Calculation"):
            break

        # Section transitions
        if _SECTION_PAY_RE.match(stripped):
            section = "payments"
            continue
        if _SECTION_TXN_RE.match(stripped):
            section = "transactions"
            continue
        if stripped == "Fees":
            section = "fees"
            continue
        if stripped == "Interest Charged":
            section = "interest"
            continue

        # Skip header rows and totals
        if stripped in ("Trans Date Post Date Description Amount", ""):
            continue
        if stripped.startswith("Total") or stripped.startswith("Visit capitalone"):
            continue

        # Interest summary lines (no date)
        if section == "interest":
            im = _INTEREST_LINE_RE.match(stripped)
            if im:
                total_interest += _parse_dollar(im.group(1)) or 0.0
            continue

        # Transaction / fee rows
        if section in ("payments", "transactions", "fees"):
            rm = _ROW_RE.match(stripped)
            if not rm:
                continue

            month_abbr, day, desc, amt_raw = rm.group(1), rm.group(2), rm.group(3), rm.group(4)
            amount = _parse_amount(amt_raw)
            if amount is None:
                continue

            date_str = _make_date(month_abbr, day, closing_year)
            merchant = _clean_merchant(desc)

            if section == "payments":
                # Payment: "- $X.XX" on statement → positive in DB (reduces CC debt)
                db_amount = -amount  # statement amount is negative; we store positive
                direction = "credit"
            else:
                # Purchase or fee: positive on statement → negative in DB (spending)
                db_amount = -amount
                direction = "debit"

            transactions.append({
                "Date":        date_str,
                "Amount":      db_amount,
                "Direction":   direction,
                "Source":      "Capital One",
                "Account":     account_label,
                "Merchant":    merchant,
                "Description": desc,
                "Category":    "",
                "Notes":       f"capone:{last4}  from {filename}",
            })

    # ── Synthetic interest row ────────────────────────────────────────────────
    if total_interest > 0 and closing_date_str:
        transactions.append({
            "Date":        closing_date_str,
            "Amount":      -total_interest,
            "Direction":   "debit",
            "Source":      "Capital One",
            "Account":     account_label,
            "Merchant":    "Capital One Interest",
            "Description": "Interest Charged",
            "Category":    "Interest",
            "Notes":       f"capone:{last4}  from {filename}",
        })

    # ── Reconciliation log ────────────────────────────────────────────────────
    imported_sum = sum(t["Amount"] for t in transactions)
    expected_sum = prev_balance - new_balance
    gap = imported_sum - expected_sum
    pfx = f"[CapOne {last4} {filename}]"
    print(f"{pfx} imported sum:   ${imported_sum:+.2f}")
    print(f"{pfx} prev−new:       ${expected_sum:+.2f}")
    print(f"{pfx} gap:            ${gap:+.2f}" + (
        "  OK" if abs(gap) < 0.02 else "  WARNING — investigate"
    ))

    metadata = {
        "prev":      prev_balance,
        "new":       new_balance,
        "last4":     last4,
        "card_name": card_name,
    }
    return transactions, metadata


def _short_name(card_name: str) -> str:
    """Map card product name to a short label for Account field."""
    lower = card_name.lower()
    if "quicksilver" in lower:
        return "Quicksilver"
    if "savor" in lower:
        return "SavorOne"
    if "venture" in lower:
        return "Venture"
    if "platinum" in lower:
        return "Platinum"
    # Fallback: first word
    return card_name.split()[0] if card_name else "Card"
