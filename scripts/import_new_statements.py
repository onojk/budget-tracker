#!/usr/bin/env python3
"""
import_new_statements.py

Import a specific list of new statement PDFs and report per-statement
reconciliation (beginning balance + sum(transactions) == ending balance).

Does NOT update last_statement_balance on Account rows.
"""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

# --- ensure project root is on path ---
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os
# Use production DB, not test DB
os.environ.pop("DATABASE_URL", None)

from app import app, db, Transaction, is_duplicate_transaction
from models import Account
from ocr_pipeline import process_uploaded_statement_files

# ── Files to import (19 new, no duplicates) ─────────────────────────────────
CHASE_DIR = Path("/tmp/new-statements/chase")
BOA_DIR   = Path("/tmp/new-statements/boa")

CHASE_FILES = sorted(CHASE_DIR.glob("Statements (*.pdf"))
BOA_FILES = [
    BOA_DIR / "eStmt_2025-04-28_20260427_130452.pdf",
    BOA_DIR / "eStmt_2025-05-28_20260427_130403.pdf",   # first of duplicate pair
    BOA_DIR / "eStmt_2025-06-26_20260427_130348.pdf",   # first of duplicate pair
    BOA_DIR / "eStmt_2025-07-29_20260427_130337.pdf",
    BOA_DIR / "eStmt_2025-08-27_20260427_130327.pdf",
    BOA_DIR / "eStmt_2025-09-26_20260427_130320.pdf",
    BOA_DIR / "eStmt_2025-10-29_20260427_130311.pdf",
    BOA_DIR / "eStmt_2025-11-25_20260427_130303.pdf",
    BOA_DIR / "eStmt_2025-12-29_20260427_130253.pdf",
]

ALL_NEW_FILES = CHASE_FILES + BOA_FILES


# ── Balance extraction helpers ───────────────────────────────────────────────

def pdf_text(path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True, text=True
    )
    return result.stdout


def extract_chase_balances(text: str):
    """
    Returns list of (label, begin, end) for each account section.
    Chase has checking + savings in one PDF.
    """
    # Summary section pattern: "Beginning Balance  $X.XX" then later "Ending Balance  $Y.YY"
    # We grab the first two begin/end pairs (checking, then savings).
    begin_re = re.compile(r"Beginning Balance\s+(-?\$[\d,]+\.\d{2})")
    end_re   = re.compile(r"Ending Balance\s+(-?\$[\d,]+\.\d{2})")

    begins = [float(m.group(1).replace("$","").replace(",","")) for m in begin_re.finditer(text)]
    ends   = [float(m.group(1).replace("$","").replace(",","")) for m in end_re.finditer(text)]

    # Summary has the first pair; detail section repeats them — take just first two unique pairs
    # deduplicate consecutive duplicates
    def dedup(lst):
        out = []
        for v in lst:
            if not out or out[-1] != v:
                out.append(v)
        return out

    begins = dedup(begins)
    ends   = dedup(ends)

    result = []
    labels = ["Chase Checking", "Chase Savings"]
    for i in range(min(len(begins), len(ends), 2)):
        result.append((labels[i], begins[i], ends[i]))
    return result


def extract_boa_balances(text: str):
    """Returns (begin, end) for BoA statement."""
    begin_m = re.search(r"Beginning balance on [A-Za-z]+ \d+, \d{4}\s+\$?([\d,]+\.\d{2})", text)
    end_m   = re.search(r"Ending balance on [A-Za-z]+ \d+, \d{4}\s+\$?([\d,]+\.\d{2})", text)
    begin = float(begin_m.group(1).replace(",","")) if begin_m else None
    end   = float(end_m.group(1).replace(",","")) if end_m else None
    return begin, end


def extract_date_range_from_text(text: str, bank: str):
    """Return (start_date_str, end_date_str) YYYY-MM-DD."""
    if bank == "chase":
        m = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+(\d{1,2}),\s+(\d{4})\s+through\s+"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+(\d{1,2}),\s+(\d{4})",
            text
        )
        if m:
            from datetime import datetime
            start = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
            end   = datetime.strptime(f"{m.group(4)} {m.group(5)} {m.group(6)}", "%B %d %Y")
            return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    elif bank == "boa":
        m = re.search(
            r"for\s+(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+(\d{1,2}),\s+(\d{4})\s+to\s+"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+(\d{1,2}),\s+(\d{4})",
            text
        )
        if m:
            from datetime import datetime
            start = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
            end   = datetime.strptime(f"{m.group(4)} {m.group(5)} {m.group(6)}", "%B %d %Y")
            return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    return None, None


def sum_transactions(account_name: str, start: str, end: str) -> float:
    """Sum Transaction.amount for account in [start, end] date range."""
    from sqlalchemy import func
    acct = db.session.query(Account).filter(Account.name == account_name).first()
    if not acct:
        return 0.0
    total = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.account_id == acct.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).scalar()
    return float(total or 0)


def count_transactions(account_name: str = None) -> int:
    q = db.session.query(Transaction)
    if account_name:
        acct = db.session.query(Account).filter(Account.name == account_name).first()
        if acct:
            q = q.filter(Transaction.account_id == acct.id)
    return q.count()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    with app.app_context():
        total_before = count_transactions()
        chase_checking_before = count_transactions("Chase Checking")
        chase_savings_before  = count_transactions("Chase Savings")
        boa_before            = count_transactions("BoA Adv Plus")

        print(f"\nTransaction counts BEFORE import:")
        print(f"  Total:          {total_before}")
        print(f"  Chase Checking: {chase_checking_before}")
        print(f"  Chase Savings:  {chase_savings_before}")
        print(f"  BoA Adv Plus:   {boa_before}")
        print()

        # ── Run pipeline on just the 19 new files ───────────────────────────
        temp_uploads = Path(tempfile.mkdtemp(prefix="new_stmts_"))
        statements_dir = ROOT / "uploads" / "statements"
        statements_dir.mkdir(parents=True, exist_ok=True)

        for f in ALL_NEW_FILES:
            if f.exists():
                shutil.copy2(f, temp_uploads / f.name)
            else:
                print(f"  [WARN] file not found: {f}")

        print(f"Importing {len(list(temp_uploads.glob('*.pdf')))} PDFs from {temp_uploads} ...")
        result = process_uploaded_statement_files(
            uploads_dir=temp_uploads,
            statements_dir=statements_dir,
        )
        print(f"\nPipeline result: {result}\n")

        try:
            shutil.rmtree(temp_uploads)
        except Exception:
            pass

        # ── Per-statement reconciliation ─────────────────────────────────────
        print("=" * 72)
        print("PER-STATEMENT RECONCILIATION")
        print("=" * 72)

        all_ok = True

        # Chase statements
        for pdf in sorted(CHASE_FILES):
            text = pdf_text(pdf)
            start, end = extract_date_range_from_text(text, "chase")
            balances = extract_chase_balances(text)
            print(f"\n{pdf.name}  [{start} → {end}]")
            for label, beg, ending in balances:
                tx_sum = sum_transactions(label, start, end)
                gap = round(beg + tx_sum - ending, 2)
                status = "OK" if abs(gap) < 0.02 else f"GAP ${gap:+.2f}"
                if abs(gap) >= 0.02:
                    all_ok = False
                print(f"  {label:<22}  beg=${beg:>10.2f}  sum={tx_sum:>10.2f}  end=${ending:>10.2f}  gap=${gap:>7.2f}  {status}")

        # BoA statements
        print()
        for pdf in BOA_FILES:
            if not pdf.exists():
                print(f"  [WARN] missing: {pdf.name}")
                continue
            text = pdf_text(pdf)
            start, end = extract_date_range_from_text(text, "boa")
            beg, ending = extract_boa_balances(text)
            tx_sum = sum_transactions("BoA Adv Plus", start, end)
            if beg is None or ending is None:
                print(f"\n{pdf.name}  [{start} → {end}]  [WARN: could not extract balances]")
                continue
            gap = round(beg + tx_sum - ending, 2)
            status = "OK" if abs(gap) < 0.02 else f"GAP ${gap:+.2f}"
            if abs(gap) >= 0.02:
                all_ok = False
            print(f"\n{pdf.name}")
            print(f"  [{start} → {end}]  beg=${beg:.2f}  sum={tx_sum:.2f}  end=${ending:.2f}  gap=${gap:+.2f}  {status}")

        # ── Post-import counts ───────────────────────────────────────────────
        total_after = count_transactions()
        chase_checking_after = count_transactions("Chase Checking")
        chase_savings_after  = count_transactions("Chase Savings")
        boa_after            = count_transactions("BoA Adv Plus")

        print()
        print("=" * 72)
        print("TRANSACTION COUNTS AFTER IMPORT")
        print("=" * 72)
        print(f"  Total:          {total_before:>5} → {total_after:>5}  (+{total_after - total_before})")
        print(f"  Chase Checking: {chase_checking_before:>5} → {chase_checking_after:>5}  (+{chase_checking_after - chase_checking_before})")
        print(f"  Chase Savings:  {chase_savings_before:>5} → {chase_savings_after:>5}  (+{chase_savings_after - chase_savings_before})")
        print(f"  BoA Adv Plus:   {boa_before:>5} → {boa_after:>5}  (+{boa_after - boa_before})")
        print()
        print("Overall reconciliation:", "ALL OK" if all_ok else "REVIEW GAPS ABOVE")


if __name__ == "__main__":
    main()
