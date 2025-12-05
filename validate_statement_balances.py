#!/usr/bin/env python3
"""
validate_statement_balances.py v3e

For each Chase OCR statement in uploads/statements/*_ocr.txt:

1) Extract beginning and ending balances from the header text.
   - Understand:
       68.02-
       (68.02)
       -68.02
       $68.02-
2) Parse ONLY the TRANSACTION DETAIL block to get per-line amounts.
   - Lines must start with a date (MM/DD or MM/DD/YY).
   - Use chase_amount_utils.extract_amount_from_txn_line() so we pick
     the AMOUNT column, not the running balance.
3) Check that: ending ≈ beginning + sum(amounts).
4) Report per-file differences and a summary at the end.
"""

import glob
import os
from decimal import Decimal

from chase_amount_utils import (
    AMOUNT_RE,
    DATE_RE,
    parse_amount_token,
    extract_amount_from_txn_line,
)


def parse_amount_from_line(line: str):
    """
    Find the LAST money-looking chunk in a line and parse it.
    Used mainly for header-like balance lines.
    """
    matches = list(AMOUNT_RE.finditer(line))
    if not matches:
        return None
    text = matches[-1].group(0)
    return parse_amount_token(text)


def find_header_balances(lines):
    """
    Try to extract beginning and ending balances from the header area.

    We first look for explicit keywords like 'beginning balance',
    'previous balance', 'ending balance', 'new balance'.

    If that fails, we fall back to:
      - begin = first amount we see in the top N lines
      - end   = the last amount we see in the whole file

    Returns (begin, end, from_keywords: bool)
    """
    begin = None
    end = None
    from_keywords = False

    header_slice = lines[:120]

    for line in header_slice:
        lower = line.lower()

        if any(k in lower for k in ["beginning balance", "previous balance", "starting balance"]):
            if begin is None:
                begin = parse_amount_from_line(line)

        if any(k in lower for k in ["ending balance", "new balance", "closing balance"]):
            if end is None:
                end = parse_amount_from_line(line)

    if begin is not None or end is not None:
        from_keywords = True
        return begin, end, from_keywords

    # Fallback: guess from first and last amounts

    # Guess beginning as first amount in header slice
    for line in header_slice:
        m = AMOUNT_RE.search(line)
        if m:
            begin = parse_amount_token(m.group(0))
            if begin is not None:
                break

    # Guess ending as last amount in entire file
    for line in reversed(lines):
        m = None
        for match in AMOUNT_RE.finditer(line):
            m = match
        if m:
            end = parse_amount_token(m.group(0))
            if end is not None:
                break

    return begin, end, from_keywords


def find_detail_block(lines):
    """
    Locate the TRANSACTION DETAIL block boundaries (start_idx, end_idx).
    If we can't find it, return (None, None).
    """
    start_idx = None

    for i, line in enumerate(lines):
        if "transaction detail" in line.lower():
            start_idx = i + 1
            break

    if start_idx is None:
        return None, None

    stop_markers = [
        "daily balance summary",
        "balance summary",
        "fees summary",
        "fee summary",
        "interest summary",
        "total for this period",
        "ending balance",
        "chase overdraft",
    ]

    end_idx = len(lines)
    for j in range(start_idx, len(lines)):
        low = lines[j].lower()
        if any(m in low for m in stop_markers):
            end_idx = j
            break

    return start_idx, end_idx


def sum_transaction_detail(lines):
    """
    Sum amounts from the TRANSACTION DETAIL block.

    We only count lines that *start with a date*, to avoid section headers
    or continuation lines.

    We use extract_amount_from_txn_line() to ensure we grab the AMOUNT column.
    """
    start_idx, end_idx = find_detail_block(lines)
    if start_idx is None:
        return None, 0

    total = Decimal("0.00")
    count = 0

    for line in lines[start_idx:end_idx]:
        if not DATE_RE.match(line):
            continue

        amt = extract_amount_from_txn_line(line)
        if amt is None:
            continue

        total += amt
        count += 1

    return total, count


def main():
    files = sorted(glob.glob("uploads/statements/*_ocr.txt"))
    if not files:
        print("No OCR statement files found in uploads/statements.")
        return

    print(f"Found {len(files)} OCR statement files.\n")

    total_files = 0
    mismatch_files = 0
    sum_abs_diff = Decimal("0.00")

    for path in files:
        total_files += 1
        fname = os.path.basename(path)
        print(f"=== Validating {fname} ===")

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        lines = text.splitlines()

        begin, end, from_keywords = find_header_balances(lines)

        if begin is None and end is None:
            print("  ⚠️ Could not find beginning or ending balances at all (even heuristic).")
            print()
            continue

        if not from_keywords:
            print("  ⚠️ Using heuristic first/last amount as begin/end; may be inaccurate.")

        if begin is not None:
            print(f"  Beginning balance    (header/guess): {begin:,.2f}")
        else:
            print("  Beginning balance    (header/guess): <missing>")

        if end is not None:
            print(f"  Ending balance       (header/guess): {end:,.2f}")
        else:
            print("  Ending balance       (header/guess): <missing>")

        sum_txns, count_txn_lines = sum_transaction_detail(lines)

        if sum_txns is None:
            print("  ⚠️ Could not locate TRANSACTION DETAIL block.")
            print()
            continue

        print(f"  Sum of txn amounts (detail): {sum_txns:,.2f}")

        implied = None
        diff = None

        if begin is not None:
            implied = begin + sum_txns
            print(f"  Implied ending = begin + sum(txns): {implied:,.2f}")

        if implied is not None and end is not None:
            diff = implied - end
            print(f"  Difference (implied - header end): {diff:,.2f}")
            if abs(diff) > Decimal("0.01"):
                mismatch_files += 1
                sum_abs_diff += abs(diff)

        print(f"  Parsed txn lines (detail block) : {count_txn_lines}")
        print()

    print("============== SUMMARY ==============")
    print(f"Total statements checked : {total_files}")
    print(f"Statements w/ mismatch   : {mismatch_files}")
    print(f"Sum of |diff| across all : {sum_abs_diff:,.2f}")


if __name__ == "__main__":
    main()


# ---- Optional helper: summarize OCR rejections ----

try:
    from app import app, db, OcrRejected  # used when running inside the Flask app context
except ImportError:
    app = None
    db = None
    OcrRejected = None


def summarize_ocr_rejected():
    """
    Print a simple summary of OcrRejected rows grouped by source_file and reason.
    Run manually, e.g.:

        python - << 'PY'
        from app import app
        from validate_statement_balances import summarize_ocr_rejected
        with app.app_context():
            summarize_ocr_rejected()
        PY
    """
    if app is None or db is None or OcrRejected is None:
        print("OcrRejected / db not available; run from within Flask app context.")
        return

    from sqlalchemy import func

    with app.app_context():
        rows = (
            db.session.query(
                OcrRejected.source_file,
                OcrRejected.reason,
                func.count(OcrRejected.id),
            )
            .group_by(OcrRejected.source_file, OcrRejected.reason)
            .order_by(OcrRejected.source_file, OcrRejected.reason)
            .all()
        )

        if not rows:
            print("No rejected OCR lines found.")
            return

        print("Rejected OCR summary:")
        for source_file, reason, count in rows:
            print(f"{source_file:40s}  {reason:24s}  {count:5d}")

