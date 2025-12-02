#!/usr/bin/env python3
"""
validate_statement_balances.py v3

For each Chase OCR statement in uploads/statements/*_ocr.txt:

1) Extract beginning and ending balances from the header text.
   - Understand trailing minus "68.02-" and parentheses "(68.02)".
2) Parse ONLY the TRANSACTION DETAIL block to get per-line amounts.
3) Check that: ending ≈ beginning + sum(amounts).
4) Report per-file differences and a summary at the end.
"""

import glob
import os
import re
from decimal import Decimal, InvalidOperation

# -------------------------------------------------------------------
# Helpers for parsing dollar amounts and balances
# -------------------------------------------------------------------

AMOUNT_RE = re.compile(r"\$?-?\d[\d,]*\.\d{2}")

def parse_amount_raw(token: str):
    """
    Basic amount parser: strip $, commas, and parse a single token.
    Does NOT handle trailing '-' or parentheses by itself.
    """
    token = token.replace("\u2212", "-")  # Unicode minus
    token = token.replace(",", "").replace("$", "")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def parse_balance_from_line(line: str):
    """
    Parse a balance from a header line, handling:
      - 68.02-
      - (68.02)
      - -68.02
      - $68.02-
    Strategy:
      - Find the LAST money-looking chunk in the line.
      - Inspect for trailing '-' or parentheses to decide sign.
    """
    line = line.replace("\u2212", "-")  # normalize unicode minus
    matches = list(AMOUNT_RE.finditer(line))
    if not matches:
        return None

    m = matches[-1]  # use the last numeric chunk; Chase often prints balance last
    text = m.group(0)
    start, end = m.span()

    # Look at context around the number
    trailing = line[end : end + 3]  # just in case "- " or "-\n"
    leading = line[max(0, start - 2) : start + len(text) + 2]

    minus = False
    # Case 1: trailing minus, e.g. "68.02-"
    if "-" in trailing:
        minus = True

    # Case 2: parentheses "(68.02)"
    if "(" in leading and ")" in leading:
        minus = True

    # Parse the numeric part normally (it might already have a leading '-')
    amt = parse_amount_raw(text)
    if amt is None:
        return None

    if minus and amt > 0:
        amt = -amt

    return amt


# -------------------------------------------------------------------
# Header balance extraction
# -------------------------------------------------------------------

def extract_header_balances(text: str):
    """
    Try to extract 'Beginning balance' and 'Ending balance' from the header
    region of a Chase OCR statement.

    We scan roughly the first 200 lines, looking for lines that contain those
    phrases and then apply parse_balance_from_line() for sign-aware parsing.
    """
    begin = None
    end = None

    lines = text.splitlines()
    header_lines = lines[:200]

    for line in header_lines:
        low = line.lower()
        if "beginning balance" in low and begin is None:
            begin = parse_balance_from_line(line)
        if "ending balance" in low and end is None:
            end = parse_balance_from_line(line)

    return begin, end


# -------------------------------------------------------------------
# Helpers for locating the TRANSACTION DETAIL block and summing txns
# -------------------------------------------------------------------

DETAIL_START_MARKERS = [
    "transaction detail",
    "checking account activity",
    "checking activity",
]

DETAIL_END_MARKERS = [
    "daily ending balance",
    "total deposits and additions",
    "total withdrawals and debits",
    "total checks paid",
    "overdraft and returned item fees",
    "in case of errors or questions",
]

def iter_detail_lines(text: str):
    """
    Yield lines that are within the TRANSACTION DETAIL block.

    We flip a boolean once we see a 'start marker', and stop once we
    hit an 'end marker'.
    """
    lines = text.splitlines()
    in_detail = False

    for line in lines:
        low = line.lower()

        if not in_detail:
            if any(m in low for m in DETAIL_START_MARKERS):
                in_detail = True
            continue

        # Once we're in the detail section, look for an end marker
        if any(m in low for m in DETAIL_END_MARKERS):
            break

        yield line


def sum_txn_amounts_from_detail(text: str):
    """
    Scan only the TRANSACTION DETAIL block and sum transaction amounts.

    Heuristic:
      - candidate lines start with MM/DD
      - we then look for money amounts in the rest of the line
      - if 2+ money amounts are found, we treat the FIRST as the txn amount
        and ignore the rest (usually the running balance column).
    """
    total = Decimal("0.00")
    parsed_lines = 0

    DATE_LINE_RE = re.compile(r"^\s*(\d{2})/(\d{2})\b")

    for line in iter_detail_lines(text):
        if not DATE_LINE_RE.match(line):
            continue

        nums = AMOUNT_RE.findall(line)
        if len(nums) < 2:
            continue

        amt = parse_amount_raw(nums[0])
        if amt is None:
            continue

        total += amt
        parsed_lines += 1

    return total, parsed_lines


# -------------------------------------------------------------------
# Main driver
# -------------------------------------------------------------------

def main():
    paths = sorted(glob.glob("uploads/statements/*_ocr.txt"))
    if not paths:
        print("No OCR statement files found in uploads/statements/")
        return

    print(f"Found {len(paths)} OCR statement files.\n")

    total_files = 0
    mismatch_files = 0
    sum_abs_diff = Decimal("0.00")

    for path in paths:
        name = os.path.basename(path)
        total_files += 1

        with open(path, "r", errors="ignore") as f:
            txt = f.read()

        begin, end = extract_header_balances(txt)
        txn_sum, parsed_lines = sum_txn_amounts_from_detail(txt)

        print(f"=== Validating {name} ===")

        if begin is None or end is None:
            print("  ⚠️ Could not find both beginning and ending balances in header.")
            print(f"  -> begin: {begin}, end: {end}")
            print(f"  Parsed txn lines (detail block only): {parsed_lines}")
            print()
            continue

        implied_end = begin + txn_sum
        diff = implied_end - end
        sum_abs_diff += abs(diff)

        if diff != 0:
            mismatch_files += 1

        print(f"  Beginning balance    (header): {begin:,.2f}")
        print(f"  Ending balance       (header): {end:,.2f}")
        print(f"  Sum of txn amounts (detail): {txn_sum:,.2f}")
        print(f"  Implied ending = begin + sum(txns): {implied_end:,.2f}")
        print(f"  Difference (implied - header end): {diff:,.2f}")
        print(f"  Parsed txn lines (detail block) : {parsed_lines}")
        print()

    print("============== SUMMARY ==============")
    print(f"Total statements checked : {total_files}")
    print(f"Statements w/ mismatch   : {mismatch_files}")
    print(f"Sum of |diff| across all : {sum_abs_diff:,.2f}")


if __name__ == "__main__":
    main()
