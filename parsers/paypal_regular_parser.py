"""
PayPal regular account (wallet) statement parser — Phase A.

Format: plain-text extracted from PayPal "ACCOUNT STATEMENTS" PDFs.
Each transaction spans multiple lines:
  Line 1: DATE  DESCRIPTION  CURRENCY  AMOUNT  FEES  TOTAL*
  Line 2: optional merchant continuation or funding source
  Line 3: optional funding source
  Line N: ID: <transaction_id>

Because pdftotext duplicates every transaction once per PDF page, this parser
deduplicates on the PayPal transaction ID (the "ID: XXXX" line).

Phase A import policy:
  IMPORT:  Mass Pay Payment rows  (Tipalti income, always positive)
           Non Reference Credit Payment rows  (cashback, positive)
  SKIP+LOG: all other types:
           Express Checkout Payment, PreApproved Payment Bill User Payment,
           General Credit Card Deposit, User Initiated Withdrawal,
           Payment Refund, General Payment, and any unlisted type.

Sign convention: all imported rows are credits (positive amounts).
Reconciliation: not applicable — PayPal regular is not a closed ledger.
"""

from __future__ import annotations

import re
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from typing import Optional

ACCOUNT_NAME = "PayPal Account"
SOURCE_SYSTEM = "PayPal"

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{4})'
    r'\s*-\s*'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),',
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r'[\w.+%-]+@[\w.-]+\.\w+')

# Date line: full date MM/DD/YYYY possibly followed by description + amount columns
# pdftotext sometimes splits "03/06/202\n6" — we normalise before matching.
_DATE_RE = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.+?)\s{2,}USD\s+([-\d.]+)')

# Looser date detection to catch the start of a new transaction block
_DATE_START_RE = re.compile(r'^(\d{2}/\d{2}/\d{4})\s')

# The ID line
_ID_RE = re.compile(r'^\s*ID:\s*(\S+)')

# Amount from description line (last column after "USD")
_AMOUNT_RE = re.compile(r'USD\s+([-\d.]+)')

# Types we import vs skip
_IMPORT_PREFIXES = (
    "Mass Pay Payment",
    "Non Reference Credit Payment",
)

_SKIP_PREFIXES = (
    "Express Checkout Payment",
    "PreApproved Payment Bill User Payment",
    "General Credit Card Deposit",
    "User Initiated Withdrawal",
    "Payment Refund",
    "General Payment",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_period(text: str) -> tuple[Optional[str], Optional[str]]:
    m = _PERIOD_RE.search(text)
    if not m:
        return None, None
    mon1_str, day1, year1, mon2_str, day2 = (
        m.group(1), int(m.group(2)), int(m.group(3)),
        m.group(4), int(m.group(5)),
    )
    mon1 = _MONTH_MAP[mon1_str.lower()]
    mon2 = _MONTH_MAP[mon2_str.lower()]
    # End year: same as start year, or +1 if statement crosses Dec→Jan
    year2 = year1 + 1 if mon2 < mon1 else year1
    start = _date(year1, mon1, day1).isoformat()
    end   = _date(year2, mon2, day2).isoformat()
    return start, end


def _parse_amount(raw: str) -> Optional[float]:
    clean = re.sub(r'[^0-9.-]', '', raw)
    try:
        return float(Decimal(clean)) if clean else None
    except InvalidOperation:
        return None


def _mmddyyyy_to_iso(s: str) -> str:
    mm, dd, yyyy = s.split('/')
    return _date(int(yyyy), int(mm), int(dd)).isoformat()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_paypal_regular_statement_text(
    text: str,
    filename: str = "statement.txt",
) -> tuple[list[dict], list[str], dict]:
    """
    Parse plain-text PayPal regular account statement.

    Returns:
        (rows, skipped_log, metadata)

        rows        list of dicts compatible with Transaction.from_dict()
        skipped_log list of human-readable strings for each skipped transaction
        metadata    {"period_start": ISO, "period_end": ISO, "account_email": str}
    """
    # ── Metadata ──────────────────────────────────────────────────────────────
    period_start, period_end = _parse_period(text)

    account_email = ""
    me = _EMAIL_RE.search(text)
    if me:
        account_email = me.group(0)

    # ── Transaction parsing ───────────────────────────────────────────────────
    # Strategy: scan line-by-line. When we see a full date (MM/DD/YYYY), begin
    # accumulating a transaction block until we hit the next date or end-of-section.
    # Extract the ID: line to dedup. Then decide import vs skip.

    rows: list[dict] = []
    skipped: list[str] = []
    seen_ids: set[str] = set()

    lines = text.splitlines()

    # Normalise split dates like "03/06/202\n6" into a single line before scanning.
    normalised: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Check if line ends with an incomplete date fragment "MM/DD/202" and next
        # line is a single digit (the year's last digit).
        m = re.match(r'^(\d{2}/\d{2}/202)$', line.rstrip())
        if m and i + 1 < len(lines) and re.match(r'^\d\s', lines[i + 1]):
            digit = lines[i + 1][0]
            rest  = lines[i + 1][1:].lstrip()
            normalised.append(m.group(1) + digit + ('  ' + rest if rest else ''))
            i += 2
            continue
        normalised.append(line)
        i += 1

    # Collect transaction blocks: each block starts at a full-date line and
    # ends at the next full-date line or a known section boundary.
    blocks: list[list[str]] = []
    current: list[str] = []
    in_activity = False

    for line in normalised:
        stripped = line.strip()

        # Enter ACCOUNT ACTIVITY section
        if stripped == "ACCOUNT ACTIVITY":
            in_activity = True
            continue

        if not in_activity:
            continue

        # Stop at footer markers
        if stripped.startswith("*For each transaction") or stripped.startswith("In case of errors"):
            if current:
                blocks.append(current)
                current = []
            in_activity = False
            continue

        # A line starting with MM/DD/YYYY signals a new transaction
        if _DATE_START_RE.match(stripped):
            if current:
                blocks.append(current)
            current = [stripped]
        elif current:
            current.append(stripped)

    if current:
        blocks.append(current)

    for block in blocks:
        if not block:
            continue

        # First line: DATE  DESCRIPTION...USD  AMOUNT  FEES  TOTAL
        first = block[0]

        date_m = _DATE_START_RE.match(first)
        if not date_m:
            continue
        date_str = first[:10]
        rest_of_first = first[10:].strip()

        # Extract amount from first line (last numeric column after USD)
        amt_m = _AMOUNT_RE.search(first)
        if not amt_m:
            continue
        amount_raw = amt_m.group(1)
        amount = _parse_amount(amount_raw)
        if amount is None:
            continue

        # Extract description: text between the date and the USD column
        # Description is everything up to the first "  USD" or multi-space gap
        desc_m = re.match(r'^(.+?)\s{2,}USD', rest_of_first)
        if desc_m:
            description = desc_m.group(1).strip()
        else:
            description = rest_of_first.split('USD')[0].strip()

        # Find the ID line in the block
        txn_id = ""
        for bline in block[1:]:
            id_m = _ID_RE.match(bline)
            if id_m:
                txn_id = id_m.group(1)
                break

        # Dedup: skip if we've seen this ID before
        dedup_key = txn_id if txn_id else f"{date_str}|{description}|{amount_raw}"
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)

        # Decide: import or skip
        should_import = any(description.startswith(p) for p in _IMPORT_PREFIXES)
        should_skip   = any(description.startswith(p) for p in _SKIP_PREFIXES)

        if not should_import:
            log_msg = f"SKIP [{date_str}] {description}  ${amount_raw}  id={txn_id}"
            skipped.append(log_msg)
            continue

        # All imported rows are credits (income / cashback)
        if amount < 0:
            # Unexpected — log and skip
            skipped.append(f"SKIP-NEG [{date_str}] {description}  ${amount_raw}  id={txn_id}")
            continue

        date_iso = _mmddyyyy_to_iso(date_str)

        # Merchant: for Mass Pay, extract payer after the colon
        merchant = description
        colon_m = re.search(r'Mass Pay Payment:\s*(.+)', description)
        if colon_m:
            merchant = colon_m.group(1).strip()

        rows.append({
            "Date":        date_iso,
            "Amount":      amount,
            "Direction":   "credit",
            "Source":      SOURCE_SYSTEM,
            "Account":     ACCOUNT_NAME,
            "Merchant":    merchant,
            "Description": description,
            "Category":    "",
            "Notes":       f"ID:{txn_id}  from {filename}",
        })

    meta = {
        "period_start":  period_start or "",
        "period_end":    period_end or "",
        "account_email": account_email,
    }

    print(
        f"[PayPal Regular {filename}] imported={len(rows)} rows  skipped={len(skipped)}"
    )

    return rows, skipped, meta
