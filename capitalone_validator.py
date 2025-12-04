#!/usr/bin/env python3
"""
capitalone_validator.py

Validates Capital One credit card statements by checking:

1) Extract "Previous Balance" (Beginning)
2) Extract "New Balance" (Ending)
3) Parse transaction table (Date | Description | Amount)
4) SUM(amounts)
5) Check: ending ≈ beginning + sum(amounts)

It walks: uploads/capone/*.pdf

Outputs:
- Pretty printed validation for each statement
- A CSV summary at uploads/capone_validation_report.csv
"""

import re
import csv
from decimal import Decimal
from pathlib import Path

import pdfplumber

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

MONEY_RE = re.compile(r"\$?-?\(?\d[\d,]*\.\d{2}\)?")

def parse_money(token: str) -> Decimal:
    """
    Parse $12.34 or (12.34) or -12.34 or 12.34- into signed Decimal.
    """
    t = token.strip().replace("$", "").replace(",", "")
    negative = False

    # Trailing minus, e.g. 12.34-
    if t.endswith("-"):
        negative = True
        t = t[:-1]

    # Parentheses, e.g. (12.34)
    if t.startswith("(") and t.endswith(")"):
        negative = True
        t = t[1:-1]

    # Leading minus, e.g. -12.34
    if t.startswith("-"):
        negative = True
        t = t[1:]

    if not t:
        return Decimal("0.00")

    val = Decimal(t)
    if negative:
        val = -val
    return val


def extract_capone_balances(text: str):
    """
    Look for lines like:
        Previous Balance ............. $123.45
        New Balance .................. $456.78

    Return (begin, end) as Decimals (or (None, None) if not found).
    """
    begin = None
    end = None

    prev_re = re.compile(
        r"Previous\s+Balance.*?(\$?-?\(?[\d,]+\.\d{2}\)?)", re.IGNORECASE
    )
    new_re = re.compile(
        r"New\s+Balance.*?(\$?-?\(?[\d,]+\.\d{2}\)?)", re.IGNORECASE
    )

    m_prev = prev_re.search(text)
    m_new = new_re.search(text)

    if m_prev:
        begin = parse_money(m_prev.group(1))

    if m_new:
        end = parse_money(m_new.group(1))

    return begin, end


def extract_capone_transactions(pdf_path: Path):
    """
    Extract transaction amounts from the statement tables.

    Strategy:
      - For each table on each page:
          * Assume the amount is in the last column.
          * Keep only rows where the last cell looks like money.
    Returns:
      list[Decimal] of signed amounts.
    """
    txns: list[Decimal] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for tbl in tables:
                for row in tbl:
                    if not row or len(row) < 2:
                        continue

                    raw_amount = (row[-1] or "").strip()
                    if not raw_amount:
                        continue

                    if not MONEY_RE.search(raw_amount):
                        continue

                    try:
                        amt = parse_money(raw_amount)
                    except Exception:
                        continue

                    txns.append(amt)

    return txns


def validate_capone_statements():
    """
    Main entrypoint.

    Scans uploads/capone/*.pdf, validates each, and writes:
      uploads/capone_validation_report.csv
    """
    base_dir = Path("uploads/capone")
    out_csv = Path("uploads/capone_validation_report.csv")

    if not base_dir.exists():
        print(f"No directory {base_dir}, nothing to validate.")
        return

    pdf_paths = sorted(base_dir.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs under {base_dir}, nothing to validate.")
        return

    results = []

    for pdf_path in pdf_paths:
        print(f"\n=== Validating {pdf_path.name} ===")

        # Grab all text from the PDF for header balances
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join((page.extract_text() or "") for page in pdf.pages)

        begin, end = extract_capone_balances(full_text)
        if begin is None or end is None:
            print("  ⚠️ Could not extract beginning and/or ending balance!")
            continue

        txns = extract_capone_transactions(pdf_path)
        txn_sum = sum(txns, Decimal("0.00"))

        implied = begin + txn_sum
        diff = implied - end

        print(f"  Beginning balance (Previous Balance): {begin}")
        print(f"  Ending balance   (New Balance)     : {end}")
        print(f"  Sum of txn amounts                : {txn_sum}")
        print(f"  Implied ending = begin + sum(txn) : {implied}")
        print(f"  Difference (implied - ending)     : {diff}")
        print(f"  Parsed {len(txns)} transaction rows (table-based).")

        results.append(
            {
                "filename": pdf_path.name,
                "beginning": f"{begin:.2f}",
                "ending": f"{end:.2f}",
                "txn_sum": f"{txn_sum:.2f}",
                "implied": f"{implied:.2f}",
                "difference": f"{diff:.2f}",
                "txn_count": len(txns),
            }
        )

    if results:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nCSV summary written to: {out_csv}")

    print("\nDone validating Capital One statements.")


if __name__ == "__main__":
    validate_capone_statements()
