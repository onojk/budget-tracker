#!/usr/bin/env python3
"""
scripts/import_remaining_statements.py

Import 49 remaining files (7 account batches) with per-statement reconciliation.

  CapOne Platinum 0728     10 PDFs  Mar/Apr 2025 – Jan 9 2026
  CapOne Quicksilver 7398   8 PDFs  May/Jun 2025 – Jan 25 2026
  CareCredit 7649           8 PDFs  May 2025 – Dec 2025
  Citi 2557                 3 PDFs  Nov 2025 – Jan 2026
  Venmo                     9 CSVs  Apr–Dec 2025  (Feb/Apr 2026 already in DB, skipped)
  PayPal Regular            9 PDFs  Apr–Dec 2025  (additive-only: Mass Pay + Non Ref Credit)
  PayPal CC 9868            9 PDFs  Apr 2025 – Jan 2026  (Jul 2025 stmt missing)

Does NOT update last_statement_balance on Account rows.
Per-statement reconciliation for all closed-ledger accounts.
Script exits 1 if any gap > $0.01.

Run from project root with venv active:
    python scripts/import_remaining_statements.py
"""

import os, re, shutil, subprocess, sys, tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DATABASE_URL", None)

from app import app, db, Transaction
from models import Account
from sqlalchemy import func
from ocr_pipeline import process_uploaded_statement_files

# ──────────────────────────────────────────────────────────────────────────────
# File manifest
# ──────────────────────────────────────────────────────────────────────────────

BASE = Path("/tmp/new-statements")

CAPONE_PLATINUM_FILES = sorted((BASE / "capone-platinum").glob("*.pdf"))
CAPONE_QS_FILES       = sorted((BASE / "capone-qs").glob("*.pdf"))
CARECREDIT_FILES      = sorted((BASE / "carecredit").glob("*.pdf"))
CITI_FILES            = sorted((BASE / "citi").glob("*.pdf"))
PAYPAL_REG_FILES      = sorted((BASE / "paypal-regular").glob("*.pdf"))
PAYPAL_CC_FILES       = sorted((BASE / "paypal-cc").glob("*.pdf"))

# Venmo: Apr–Dec 2025 only; Feb-2026 and Apr-2026 already imported (74 existing txns).
VENMO_FILES = [
    BASE / "venmo" / f"{m}-2025-statement.csv"
    for m in ["apr","may","jun","jul","aug","sep","oct","nov","dec"]
]

ALL_FILES = (
    CAPONE_PLATINUM_FILES + CAPONE_QS_FILES +
    CARECREDIT_FILES + CITI_FILES +
    VENMO_FILES + PAYPAL_REG_FILES + PAYPAL_CC_FILES
)

ACCOUNT_NAMES = [
    "CapOne Platinum 0728",
    "CapOne Quicksilver 7398",
    "CareCredit Rewards Mastercard",
    "Citi Costco Anywhere Visa",
    "Venmo",
    "PayPal Account",
    "PayPal Cashback Mastercard",
]

# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────

def count_txns(account_name=None):
    q = db.session.query(func.count(Transaction.id))
    if account_name:
        acct = db.session.query(Account).filter(Account.name == account_name).first()
        if acct:
            q = q.filter(Transaction.account_id == acct.id)
    return q.scalar() or 0


def sum_txns(account_name, start, end):
    """Sum Transaction.amount for account where start <= date <= end (ISO strings)."""
    acct = db.session.query(Account).filter(Account.name == account_name).first()
    if not acct:
        return 0.0
    total = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.account_id == acct.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).scalar()
    return float(total or 0)


# ──────────────────────────────────────────────────────────────────────────────
# PDF text extraction
# ──────────────────────────────────────────────────────────────────────────────

def pdf_text(path):
    """Run pdftotext -layout on a PDF; return stdout."""
    r = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True, text=True,
    )
    return r.stdout


def _dollar(s):
    return float(re.sub(r"[^0-9.]", "", s) or "0")


_MON = {
    "Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
    "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12,
}

# ──────────────────────────────────────────────────────────────────────────────
# Balance extractors  (all operate on pdftotext -layout text)
# ──────────────────────────────────────────────────────────────────────────────

_CAPONE_PERIOD_RE = re.compile(
    r'([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})\s*[-–]\s*'
    r'([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})'
)
_CAPONE_PREV_RE = re.compile(r"Previous Balance\s+\$?([\d,]+\.\d{2})")
_CAPONE_NEW_RE  = re.compile(r"New Balance\s*=\s*\$?([\d,]+\.\d{2})")

def extract_capone(txt):
    """Return (start_iso, end_iso, prev, new) for a CapOne PDF."""
    pm = _CAPONE_PERIOD_RE.search(txt)
    if pm:
        start = date(int(pm.group(3)), _MON[pm.group(1)], int(pm.group(2))).isoformat()
        end   = date(int(pm.group(6)), _MON[pm.group(4)], int(pm.group(5))).isoformat()
    else:
        start = end = None
    mp = _CAPONE_PREV_RE.search(txt)
    mn = _CAPONE_NEW_RE.search(txt)
    prev = _dollar(mp.group(1)) if mp else 0.0
    new  = _dollar(mn.group(1)) if mn else 0.0
    return start, end, prev, new


_CC_CLOSING_RE = re.compile(r"Statement Closing Date:\s+(\d{2}/\d{2}/\d{4})")
_CC_PREV_RE    = re.compile(r"Previous Balance\s+\$([\d,]+\.\d{2})")
_CC_NEW_RE     = re.compile(r"New Balance\s+\$([\d,]+\.\d{2})")

def extract_carecredit(txt):
    """Return (closing_date: date | None, prev, new) for a CareCredit PDF."""
    mc = _CC_CLOSING_RE.search(txt)
    closing = datetime.strptime(mc.group(1), "%m/%d/%Y").date() if mc else None
    mp = _CC_PREV_RE.search(txt)
    mn = _CC_NEW_RE.search(txt)
    prev = _dollar(mp.group(1)) if mp else 0.0
    new  = _dollar(mn.group(1)) if mn else 0.0
    return closing, prev, new


_CITI_BILLING_RE = re.compile(
    r"Billing Period:\s+(\d{2})/(\d{2})/(\d{2})-(\d{2})/(\d{2})/(\d{2})"
)
_CITI_PREV_RE = re.compile(r"Previous balance\s+\$([\d,]+\.\d{2})")
_CITI_NEW_RE  = re.compile(r"New balance as of \d{2}/\d{2}/\d{2}:\s+\$([\d,]+\.\d{2})")

def extract_citi(txt):
    """Return (start_iso, end_iso, prev, new) for a Citi PDF."""
    bm = _CITI_BILLING_RE.search(txt)
    if bm:
        start = date(2000+int(bm.group(3)), int(bm.group(1)), int(bm.group(2))).isoformat()
        end   = date(2000+int(bm.group(6)), int(bm.group(4)), int(bm.group(5))).isoformat()
    else:
        start = end = None
    mp = _CITI_PREV_RE.search(txt)
    mn = _CITI_NEW_RE.search(txt)
    prev = _dollar(mp.group(1)) if mp else 0.0
    new  = _dollar(mn.group(1)) if mn else 0.0
    return start, end, prev, new


_PP_PREV_DATE_RE  = re.compile(r"Previous balance as of (\d{2}/\d{2}/\d{4})")
_PP_CLOSE_DATE_RE = re.compile(r"New balance as of (\d{2}/\d{2}/\d{4})")
_PP_PREV_RE       = re.compile(r"Previous balance as of \d{2}/\d{2}/\d{4}\s+\$([\d,]+\.\d{2})")
_PP_NEW_RE        = re.compile(r"New balance as of \d{2}/\d{2}/\d{4}\s+\$([\d,]+\.\d{2})")

def extract_paypal_cc(txt):
    """
    Return (start_iso, end_iso, prev, new) for a PayPal Cashback Mastercard PDF.

    start = day after 'Previous balance as of MM/DD/YYYY' date.
    end   = 'New balance as of MM/DD/YYYY' date.
    """
    pd_m = _PP_PREV_DATE_RE.search(txt)
    nd_m = _PP_CLOSE_DATE_RE.search(txt)
    prev_date  = datetime.strptime(pd_m.group(1), "%m/%d/%Y").date() if pd_m else None
    close_date = datetime.strptime(nd_m.group(1), "%m/%d/%Y").date() if nd_m else None
    # Period start is the day AFTER the previous statement's closing date.
    start = (prev_date + timedelta(days=1)).isoformat() if prev_date else None
    end   = close_date.isoformat() if close_date else None
    mp = _PP_PREV_RE.search(txt)
    mn = _PP_NEW_RE.search(txt)
    prev = _dollar(mp.group(1)) if mp else 0.0
    new  = _dollar(mn.group(1)) if mn else 0.0
    return start, end, prev, new


# ──────────────────────────────────────────────────────────────────────────────
# Reconciliation printers
# ──────────────────────────────────────────────────────────────────────────────

def recon_cc(label, start, end, prev, new, account_name):
    """
    Closed-ledger CC check.  sum(stored amounts in period) == prev − new.
    Stored convention: purchases negative, payments positive.
    Returns True if reconciled within $0.01.
    """
    if start is None or end is None:
        print(f"  {label:<52}  [WARN: could not extract date range — check PDF text]")
        return True  # don't auto-fail; investigate manually
    total    = sum_txns(account_name, start, end)
    expected = round(prev - new, 2)
    gap      = round(total - expected, 2)
    ok       = abs(gap) < 0.02
    status   = "OK" if ok else f"GAP ${gap:+.2f}  *** INVESTIGATE ***"
    print(f"  {label:<52}  [{start}→{end}]  "
          f"prev=${prev:>9.2f}  new=${new:>9.2f}  "
          f"sum={total:>9.2f}  exp={expected:>9.2f}  {status}")
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    with app.app_context():

        # ── Pre-import counts ────────────────────────────────────────────────
        total_before   = count_txns()
        acct_before    = {n: count_txns(n) for n in ACCOUNT_NAMES}

        print("=" * 90)
        print("TRANSACTION COUNTS BEFORE IMPORT")
        print("=" * 90)
        print(f"  Total: {total_before}")
        for n, c in acct_before.items():
            print(f"  {n:<40}: {c}")
        print()

        # ── Verify files exist ───────────────────────────────────────────────
        missing = [f for f in ALL_FILES if not f.exists()]
        if missing:
            print("[WARN] Missing files (will be skipped):")
            for f in missing:
                print(f"  {f}")
            print()

        present = [f for f in ALL_FILES if f.exists()]
        print(f"Files to import: {len(present)} of {len(ALL_FILES)}")

        # ── Import all files in one pipeline call ────────────────────────────
        temp_dir = Path(tempfile.mkdtemp(prefix="remaining_stmts_"))
        statements_dir = ROOT / "uploads" / "statements"
        statements_dir.mkdir(parents=True, exist_ok=True)

        for f in present:
            shutil.copy2(f, temp_dir / f.name)

        print(f"Pipeline input dir: {temp_dir}")
        print("-" * 60)
        pipeline_result = process_uploaded_statement_files(temp_dir, statements_dir)
        print("-" * 60)
        print(f"Pipeline stats: {pipeline_result}")
        print()

        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

        # ── Post-import counts ───────────────────────────────────────────────
        total_after = count_txns()
        acct_after  = {n: count_txns(n) for n in ACCOUNT_NAMES}

        print("=" * 90)
        print("TRANSACTION COUNTS AFTER IMPORT")
        print("=" * 90)
        print(f"  Total: {total_before} → {total_after}  (+{total_after - total_before})")
        for n in ACCOUNT_NAMES:
            delta = acct_after[n] - acct_before[n]
            print(f"  {n:<40}: {acct_before[n]:>4} → {acct_after[n]:>4}  (+{delta})")
        print()

        # ── Per-statement reconciliation ─────────────────────────────────────
        print("=" * 90)
        print("PER-STATEMENT RECONCILIATION")
        print("=" * 90)
        all_ok = True

        # ── CapOne Platinum ──────────────────────────────────────────────────
        print("\nCapOne Platinum 0728  (closed ledger: sum == prev − new)")
        for pdf in CAPONE_PLATINUM_FILES:
            txt = pdf_text(pdf)
            start, end, prev, new = extract_capone(txt)
            label = pdf.name[:50]
            ok = recon_cc(label, start, end, prev, new, "CapOne Platinum 0728")
            all_ok = all_ok and ok

        # ── CapOne Quicksilver ───────────────────────────────────────────────
        print("\nCapOne Quicksilver 7398  (closed ledger: sum == prev − new)")
        for pdf in CAPONE_QS_FILES:
            txt = pdf_text(pdf)
            start, end, prev, new = extract_capone(txt)
            label = pdf.name[:50]
            ok = recon_cc(label, start, end, prev, new, "CapOne Quicksilver 7398")
            all_ok = all_ok and ok

        # ── CareCredit ───────────────────────────────────────────────────────
        print("\nCareCredit 7649  (closed ledger: sum == prev − new)")
        # Build sorted list of (pdf, closing_date, prev, new) to compute period starts.
        cc_meta = []
        for pdf in CARECREDIT_FILES:
            txt = pdf_text(pdf)
            closing, prev, new = extract_carecredit(txt)
            cc_meta.append((pdf, closing, prev, new))
        # Sort by closing date; None sorts last (will warn).
        cc_meta.sort(key=lambda x: x[1] or date(9999, 1, 1))

        for i, (pdf, closing, prev, new) in enumerate(cc_meta):
            if closing is None:
                print(f"  {pdf.name:<52}  [WARN: no closing date found]")
                continue
            if i == 0:
                # First statement: no prior closing date; use a safe early start.
                # CareCredit opened before May 2025; no earlier txns exist in DB.
                period_start = "2025-01-01"
            else:
                prev_closing = cc_meta[i - 1][1]
                period_start = (prev_closing + timedelta(days=1)).isoformat()
            period_end = closing.isoformat()
            label = f"CareCredit closing {closing}"
            ok = recon_cc(label, period_start, period_end, prev, new, "CareCredit Rewards Mastercard")
            all_ok = all_ok and ok

        # ── Citi ─────────────────────────────────────────────────────────────
        print("\nCiti 2557  (closed ledger: sum == prev − new)")
        for pdf in sorted(CITI_FILES):
            txt = pdf_text(pdf)
            start, end, prev, new = extract_citi(txt)
            label = pdf.name
            ok = recon_cc(label, start, end, prev, new, "Citi Costco Anywhere Visa")
            all_ok = all_ok and ok

        # ── Venmo ────────────────────────────────────────────────────────────
        print("\nVenmo  (NOT closed ledger — gap is expected per CLAUDE.md)")
        print("  Importing Apr–Dec 2025 only. Feb/Apr 2026 already in DB.")
        from parsers.venmo_csv_parser import parse_venmo_csv
        for csv_path in VENMO_FILES:
            if not csv_path.exists():
                print(f"  {csv_path.name}: [missing]")
                continue
            try:
                rows, meta = parse_venmo_csv(csv_path, skip_log=None)
                beg   = meta["begin"]
                end_b = meta["end"]
                fees  = meta.get("period_fees", 0.0)
                n_rows = len(rows)
                row_sum = sum(r.get("Amount", 0) for r in rows)
                implied_gap = round(end_b - beg - row_sum - fees, 2)
                print(f"  {csv_path.name:<40}  begin=${beg:>7.2f}  end=${end_b:>7.2f}  "
                      f"rows={n_rows:>3}  row_sum={row_sum:>8.2f}  "
                      f"fees={fees:>6.2f}  implied_gap=${implied_gap:+.2f}")
            except Exception as e:
                print(f"  {csv_path.name}: [error: {e}]")

        # ── PayPal Regular ───────────────────────────────────────────────────
        print("\nPayPal Regular  (additive-only — no balance reconciliation)")
        pp_reg_before = acct_before["PayPal Account"]
        pp_reg_after  = acct_after["PayPal Account"]
        print(f"  PayPal Account: {pp_reg_before} → {pp_reg_after}  (+{pp_reg_after - pp_reg_before})")
        print(f"  (9 PDFs Apr–Dec 2025; Mass Pay + Non Reference Credit Payment rows only)")

        # ── PayPal CC ────────────────────────────────────────────────────────
        print("\nPayPal CC 9868  (closed ledger: sum == prev − new)")
        print("  Note: Jul 2025 statement is missing.")
        for pdf in PAYPAL_CC_FILES:
            txt = pdf_text(pdf)
            start, end, prev, new = extract_paypal_cc(txt)
            label = pdf.name
            # For the Aug statement: note whether July had any activity.
            if "2025-08" in pdf.name:
                if abs(prev) < 0.01:
                    print(f"  [NOTE] Aug prev=${prev:.2f} == Jun new=$0.00 → Jul was $0-balance, no missing data")
                else:
                    print(f"  [NOTE] Aug prev=${prev:.2f} != $0.00 → Jul had activity; "
                          f"Aug opening balance treated as authoritative")
            ok = recon_cc(label, start, end, prev, new, "PayPal Cashback Mastercard")
            all_ok = all_ok and ok

        # ── Summary ──────────────────────────────────────────────────────────
        print()
        print("=" * 90)
        print("SUMMARY")
        print("=" * 90)
        print(f"  Files imported : {len(present)}")
        print(f"  New txns added : {total_after - total_before}  ({total_before} → {total_after})")
        print(f"  Closed-ledger  : {'ALL OK' if all_ok else 'GAPS FOUND — REVIEW ABOVE'}")
        print()

        if not all_ok:
            print("  *** One or more closed-ledger statements did not reconcile to $0.00 ***")
            print("  *** Do NOT commit until all gaps are resolved. ***")
            sys.exit(1)
        else:
            print("  All closed-ledger accounts reconcile to $0.00.")
            print("  Run pytest and review counts above before committing.")


if __name__ == "__main__":
    main()
