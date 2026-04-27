"""
Venmo monthly statement CSV parser.

CSV structure (0-indexed rows):
  0   Account Statement - (@handle) ...
  1   Account Activity ...
  2   column headers: ,ID,Datetime,Type,Status,Note,From,To,Amount (total),...
  3   beginning balance stub: only col 16 (Beginning Balance) is populated
  4+  transaction rows (ID in col 1)
  last  footer row (ID blank, col 17=Ending Balance, col 18=Period Fees)

Columns of interest:
  1  ID             transaction ID → stored in Notes for auditability
  2  Datetime       ISO-8601 with T → take date part
  3  Type           see SKIP_TYPES / IMPORT_TYPES below
  4  Status         usually "Complete" — we only import Complete rows
  5  Note           user-entered description ("pizza", emoji, etc.)
  6  From           sender name
  7  To             recipient / merchant name
  8  Amount (total) "+ $25.00" or "- $15.00"
  11 Amount (fee)   Instant Transfer fee, e.g. "- $0.88"
  14 Funding Source "Venmo balance", "Visa *3253", "BANK OF AMERICA N.A. ..."
  15 Destination    bank destination for Instant Transfer
  16 Beginning Balance  populated only in the stub row (row 3)
  17 Ending Balance     populated only in the footer row
  18 Statement Period Venmo Fees  populated only in the footer row

Direction rule: taken directly from the sign of Amount (total). No inference.

Skip rules (Phase A — conservative):
  - Type in SKIP_TYPES (Instant Add Funds, Instant Transfer, Venmo account repayment)
  - Type == "Merchant Transaction" AND Funding Source indicates a bank/card
    (not "Venmo balance").  These are logged to skip_log for Phase B matching.

Merchant extraction:
  - Venmo Card Transaction / Merchant Transaction: merchant = "To" column
  - Payment received (Amount > 0): merchant = "From" column (the payer)
  - Payment sent (Amount < 0): merchant = "To" column (the payee)

Reconciliation note:
  The Venmo wallet balance is a pass-through account (~$0–$25 range).
  Instant Add Funds and Instant Transfer move money in/out of the wallet but
  typically cancel each other in a given period.  For a synthetic fixture where
  they exactly cancel, the identity holds:
      sum(imported amounts) + period_fees ≈ ending_balance - beginning_balance
  For real statements this is an approximation only — Venmo's internal ledger
  includes deferred settlements and overdraft handling that the CSV doesn't
  fully expose.
"""

from __future__ import annotations

import csv
import re
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_TYPES = frozenset(
    {
        "Instant Add Funds",
        "Instant Transfer",
        "Venmo account repayment",
    }
)

IMPORT_TYPES = frozenset(
    {
        "Payment",
        "Venmo Card Transaction",
        "Merchant Transaction",
    }
)

_BANK_FUNDING_RE = re.compile(
    r"bank of america|jpmorgan|chase bank|wells fargo|citibank|us bank|capital one",
    re.IGNORECASE,
)

# Column indices (0-based within each CSV row)
_COL_ID       = 1
_COL_DATETIME = 2
_COL_TYPE     = 3
_COL_STATUS   = 4
_COL_NOTE     = 5
_COL_FROM     = 6
_COL_TO       = 7
_COL_AMOUNT   = 8
_COL_FEE      = 11
_COL_FUNDING  = 14
_COL_DEST     = 15
_COL_BEGIN    = 16
_COL_END      = 17
_COL_FEES     = 18

DEFAULT_SKIP_LOG = None  # callers opt-in by passing a Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_venmo_amount(raw: str) -> Optional[float]:
    """
    Parse Venmo's signed amount string:
      "+ $25.00"  → +25.00
      "- $15.00"  → -15.00
    Returns None if unparseable.
    """
    s = raw.strip()
    if not s:
        return None
    negative = s.startswith("-")
    s = re.sub(r"[^0-9.]", "", s)
    if not s:
        return None
    try:
        v = float(s)
        return -v if negative else v
    except ValueError:
        return None


def _parse_dollar(raw: str) -> Optional[Decimal]:
    """Parse a bare "$X.XX" string → Decimal, or None."""
    s = re.sub(r"[^0-9.]", "", raw.strip())
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _get(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_venmo_csv(
    path: Path,
    skip_log: Optional[Path] = DEFAULT_SKIP_LOG,
) -> tuple[list[dict], dict]:
    """
    Parse a Venmo monthly statement CSV.

    Returns:
        (transactions, metadata)

        transactions: list of dicts compatible with import_ocr_rows / Transaction.from_dict
        metadata: {
            "begin":        float — beginning Venmo balance
            "end":          float — ending Venmo balance
            "period_fees":  float — Instant Transfer fees for the period
        }

    Side effect:
        Skipped bank-funded Merchant Transactions are appended to skip_log
        (one line per transaction) for Phase B matching.
    """
    path = Path(path)

    with path.open(newline="", errors="ignore") as fh:
        all_rows = list(csv.reader(fh))

    # ── Preamble ──────────────────────────────────────────────────────────────
    # Row 3 (0-indexed) is the beginning balance stub.
    begin_balance: float = 0.0
    if len(all_rows) > 3:
        raw_begin = _get(all_rows[3], _COL_BEGIN)
        v = _parse_dollar(raw_begin)
        if v is not None:
            begin_balance = float(v)

    end_balance: float = 0.0
    period_fees: float = 0.0

    # ── Detect owner name ─────────────────────────────────────────────────────
    # The owner is whoever appears in "From" for Venmo Card Transaction rows
    # (the card is always used by the account holder).
    owner_name = ""
    for row in all_rows[4:]:
        if _get(row, _COL_TYPE) == "Venmo Card Transaction":
            name = _get(row, _COL_FROM)
            if name:
                owner_name = name
                break

    # ── Transaction rows ──────────────────────────────────────────────────────
    transactions: list[dict] = []
    skipped_bank_funded: list[str] = []
    seen_ids: set[str] = set()  # deduplicate by Venmo transaction ID

    for row in all_rows[4:]:
        txid = _get(row, _COL_ID)

        # Footer row has blank ID but may have Ending Balance / fees.
        if not txid:
            raw_end  = _get(row, _COL_END)
            raw_fees = _get(row, _COL_FEES)
            v_end  = _parse_dollar(raw_end)
            v_fees = _parse_dollar(raw_fees)
            if v_end  is not None:
                end_balance = float(v_end)
            if v_fees is not None:
                period_fees = float(v_fees)
            continue

        if txid in seen_ids:
            print(f"[Venmo] duplicate ID {txid} skipped in {path.name}")
            continue
        seen_ids.add(txid)

        typ     = _get(row, _COL_TYPE)
        status  = _get(row, _COL_STATUS)
        note    = _get(row, _COL_NOTE)
        from_   = _get(row, _COL_FROM)
        to_     = _get(row, _COL_TO)
        amt_raw = _get(row, _COL_AMOUNT)
        funding = _get(row, _COL_FUNDING)
        dest    = _get(row, _COL_DEST)

        # Only process completed transactions.
        if status and status.lower() != "complete":
            continue

        # ── Skip rules ────────────────────────────────────────────────────────
        if typ in SKIP_TYPES:
            continue

        if typ not in IMPORT_TYPES:
            continue

        # Bank-funded Merchant Transaction → skip + log for Phase B.
        if typ == "Merchant Transaction" and _BANK_FUNDING_RE.search(funding):
            date_str = _get(row, _COL_DATETIME)[:10]
            merchant = to_ or from_
            log_line = (
                f"{date_str}\t{amt_raw}\t{merchant}\t{funding}\t{txid}\n"
            )
            skipped_bank_funded.append(log_line)
            continue

        # ── Parse amount ──────────────────────────────────────────────────────
        amount = _parse_venmo_amount(amt_raw)
        if amount is None:
            continue

        # ── Date ─────────────────────────────────────────────────────────────
        raw_dt = _get(row, _COL_DATETIME)
        date_str = raw_dt[:10]  # "2026-02-05"

        # ── Merchant ─────────────────────────────────────────────────────────
        if typ in ("Venmo Card Transaction", "Merchant Transaction"):
            merchant = to_ or "Venmo Merchant"
        elif amount > 0:
            # Payment received — payer is in From
            merchant = from_ if from_ != owner_name else to_
        else:
            # Payment sent — payee is in To
            merchant = to_ if to_ != owner_name else from_

        direction = "credit" if amount > 0 else "debit"
        description = note or merchant

        transactions.append(
            {
                "Date":        date_str,
                "Amount":      amount,
                "Direction":   direction,
                "Source":      "Venmo",
                "Account":     "Venmo",
                "Merchant":    merchant,
                "Description": description,
                "Category":    "",
                "Notes":       f"venmo:{txid}  from {path.name}",
            }
        )

    # ── Write skip log ────────────────────────────────────────────────────────
    if skipped_bank_funded and skip_log is not None:
        skip_log = Path(skip_log)
        with skip_log.open("a", encoding="utf-8") as fh:
            fh.writelines(skipped_bank_funded)
        # Note: opened in append mode so multiple files in one import session
        # accumulate. The router is responsible for clearing the log before a
        # full import run if a clean slate is desired.

    # ── Reconciliation gap log ────────────────────────────────────────────────
    imported_sum  = sum(t["Amount"] for t in transactions)
    balance_delta = end_balance - begin_balance
    gap           = balance_delta - imported_sum
    _pfx = f"[Venmo {path.name}]"
    print(f"{_pfx} imported sum:   ${imported_sum:+.2f}")
    print(f"{_pfx} balance delta:  ${balance_delta:+.2f}")
    print(
        f"{_pfx} gap:            ${gap:+.2f}"
        "  (Venmo CSV is not a closed ledger; see CLAUDE.md)"
    )

    metadata = {
        "begin":       begin_balance,
        "end":         end_balance,
        "period_fees": period_fees,
    }
    return transactions, metadata
