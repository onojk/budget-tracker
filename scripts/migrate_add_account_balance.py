"""
One-off migration: add last_statement_balance and last_statement_date columns
to the account table, then backfill all 6 accounts from their most-recent
statements.

Idempotent — safe to re-run. If the old column names (current_balance /
balance_as_of_date) exist from a prior run, values are copied and the old
columns are dropped. Balances are always overwritten on re-run.

Run from the project root with the venv active:
    python scripts/migrate_add_account_balance.py
"""

import csv
import glob
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

from app import app
from models import db, Account

DOWNLOADS = os.path.expanduser("~/Downloads")
STATEMENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "uploads", "statements")


# ---------------------------------------------------------------------------
# Parsers — each returns (balance: Decimal, as_of: date)
# ---------------------------------------------------------------------------

def _parse_amount(s: str) -> Decimal:
    """Strip $, commas, whitespace; return Decimal."""
    return Decimal(re.sub(r"[$,\s]", "", s))


def parse_boa(ocr_dir: str) -> tuple[Decimal, date]:
    """Latest BoA eStmt OCR file → (ending_balance, statement_date)."""
    files = sorted(glob.glob(os.path.join(ocr_dir, "eStmt_*_ocr.txt")))
    if not files:
        raise FileNotFoundError(f"No eStmt_*_ocr.txt in {ocr_dir}")
    path = files[-1]
    text = open(path).read()
    m = re.search(
        r"Ending balance on (\w+ \d+, \d{4})\s+\$([\d,]+\.\d+)", text
    )
    if not m:
        raise ValueError(f"Ending balance not found in {path}")
    as_of = datetime.strptime(m.group(1), "%B %d, %Y").date()
    balance = _parse_amount(m.group(2))
    return balance, as_of


def parse_chase(ocr_dir: str) -> dict[str, tuple[Decimal, date]]:
    """
    Most-recent Chase combined statement OCR → balances for Checking and Savings.
    Returns {"Chase Checking": (bal, date), "Chase Savings": (bal, date)}.
    Selects by latest period-end date, not filename sort order.
    """
    files = glob.glob(os.path.join(ocr_dir, "Statements (*)*_ocr.txt"))
    if not files:
        raise FileNotFoundError(f"No Statements (*)*_ocr.txt in {ocr_dir}")

    # Pick file with latest period-end date
    best_path, best_date = None, date.min
    for f in files:
        text = open(f).read()
        m = re.search(r"through (\w+ \d+, \d{4})", text)
        if m:
            d = datetime.strptime(m.group(1), "%B %d, %Y").date()
            if d > best_date:
                best_date, best_path = d, f
    if best_path is None:
        raise ValueError("No period-end date found in any Chase statement file")

    path = best_path
    as_of = best_date
    text = open(path).read()
    print(f"  [Chase] Using {os.path.basename(path)} (period end {as_of})")

    # Summary table line for Checking — beginning balance may be negative
    m_chk = re.search(
        r"Chase Premier Plus Checking\s+\S+\s+-?\$[\d,]+\.\d+\s+\$([\d,]+\.\d+)",
        text,
    )
    if not m_chk:
        raise ValueError(f"Chase Checking ending balance not found in {path}")
    checking_bal = _parse_amount(m_chk.group(1))

    # Summary table line for Savings: values without $ signs ("5.01  0.01")
    m_sav = re.search(
        r"Chase Savings\s+\S+\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)",
        text,
    )
    if not m_sav:
        raise ValueError(f"Chase Savings ending balance not found in {path}")
    savings_bal = _parse_amount(m_sav.group(2))

    return {
        "Chase Checking": (checking_bal, as_of),
        "Chase Savings": (savings_bal, as_of),
    }


def parse_venmo(downloads_dir: str) -> tuple[Decimal, date]:
    """
    Latest Venmo *-statement.csv → (ending_balance, last_complete_txn_date).
    Footer row col 17 holds the ending balance. Picks the file with the latest
    complete transaction date, not alphabetical sort order.
    """
    files = glob.glob(os.path.join(downloads_dir, "*-statement.csv"))
    if not files:
        raise FileNotFoundError(f"No *-statement.csv in {downloads_dir}")

    # Pick the file with the latest complete transaction date
    best_path, best_date = None, date.min
    for f in files:
        with open(f) as fh:
            rows = list(csv.reader(fh))
        for row in rows[1:-1]:
            if row[2] and row[4] == "Complete":
                d = datetime.fromisoformat(row[2]).date()
                if d > best_date:
                    best_date, best_path = d, f
    if best_path is None:
        raise FileNotFoundError("No Venmo CSV with complete transactions found")
    path = best_path
    print(f"  [Venmo] Using {os.path.basename(path)} (latest txn {best_date})")
    with open(path) as f:
        rows = list(csv.reader(f))

    footer = rows[-1]
    balance = _parse_amount(footer[17])

    last_date = None
    for row in rows[1:-1]:
        if row[2] and row[4] == "Complete":
            last_date = row[2]
    if last_date is None:
        raise ValueError(f"No complete transactions found in {path}")
    as_of = datetime.fromisoformat(last_date).date()
    return balance, as_of


def parse_capitalone_pdf(pdf_path: str) -> tuple[Decimal, date]:
    """
    Capital One PDF → (new_balance, period_end_date).
    Uses pdfplumber on page 0.
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as p:
        text = p.pages[0].extract_text()

    # Period: "Mon DD, YYYY - Mon DD, YYYY"
    m_period = re.search(
        r"\w+ \d+, \d{4}\s*[-–]\s*(\w+ \d+, \d{4})", text
    )
    if not m_period:
        raise ValueError(f"Period not found in {pdf_path}")
    as_of = datetime.strptime(m_period.group(1), "%b %d, %Y").date()

    # New Balance line: "New Balance Minimum Payment Due" then "$NNN.NN" on next chunk
    m_bal = re.search(r"New Balance\s+Minimum Payment Due\s+\$([\d,]+\.\d+)", text)
    if not m_bal:
        raise ValueError(f"New Balance not found in {pdf_path}")
    balance = _parse_amount(m_bal.group(1))
    return balance, as_of


def find_capitalone_pdfs(downloads_dir: str) -> dict[str, str]:
    """
    Match April Capital One PDFs to account names by extracting last4 from page 0.
    Returns {"CapOne Platinum 0728": path, "CapOne Quicksilver 7398": path}.
    """
    import pdfplumber

    april_pdfs = glob.glob(os.path.join(downloads_dir, "April card statement*.pdf"))
    result = {}
    for pdf_path in april_pdfs:
        with pdfplumber.open(pdf_path) as p:
            page0 = p.pages[0].extract_text()
        m = re.search(r"ending in (\d{4})", page0)
        if not m:
            continue
        last4 = m.group(1)
        if "Platinum" in page0 or "Quicksilver" in page0:
            card_type = "Platinum" if "Platinum" in page0 else "Quicksilver"
            key = f"CapOne {card_type} {last4}"
            result[key] = pdf_path
    return result


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def add_columns_if_missing():
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    existing = {c["name"] for c in insp.get_columns("account")}
    with db.engine.begin() as conn:
        # --- last_statement_balance ---
        if "last_statement_balance" in existing:
            print("Column last_statement_balance already exists — skipping")
        elif "current_balance" in existing:
            conn.execute(text("ALTER TABLE account ADD COLUMN last_statement_balance NUMERIC(12,2)"))
            conn.execute(text("UPDATE account SET last_statement_balance = current_balance"))
            conn.execute(text("ALTER TABLE account DROP COLUMN current_balance"))
            print("Renamed current_balance → last_statement_balance (values copied)")
        else:
            conn.execute(text("ALTER TABLE account ADD COLUMN last_statement_balance NUMERIC(12,2)"))
            print("Added column: last_statement_balance")

        # --- last_statement_date ---
        if "last_statement_date" in existing:
            print("Column last_statement_date already exists — skipping")
        elif "balance_as_of_date" in existing:
            conn.execute(text("ALTER TABLE account ADD COLUMN last_statement_date DATE"))
            conn.execute(text("UPDATE account SET last_statement_date = balance_as_of_date"))
            conn.execute(text("ALTER TABLE account DROP COLUMN balance_as_of_date"))
            print("Renamed balance_as_of_date → last_statement_date (values copied)")
        else:
            conn.execute(text("ALTER TABLE account ADD COLUMN last_statement_date DATE"))
            print("Added column: last_statement_date")


def backfill_balances():
    print("\nParsing balances from source statements...")

    balances: dict[str, tuple[Decimal, date]] = {}

    # BoA
    boa_bal, boa_date = parse_boa(STATEMENTS)
    balances["BoA Adv Plus"] = (boa_bal, boa_date)
    print(f"  BoA Adv Plus:         ${boa_bal:>9.2f}  as of {boa_date}")

    # Chase
    chase = parse_chase(STATEMENTS)
    for name, (bal, d) in chase.items():
        balances[name] = (bal, d)
        print(f"  {name:<22} ${bal:>9.2f}  as of {d}")

    # Venmo
    venmo_bal, venmo_date = parse_venmo(DOWNLOADS)
    balances["Venmo"] = (venmo_bal, venmo_date)
    print(f"  Venmo:                ${venmo_bal:>9.2f}  as of {venmo_date}")

    # Capital One
    capone_pdfs = find_capitalone_pdfs(DOWNLOADS)
    if not capone_pdfs:
        print("  WARNING: No April Capital One PDFs found — skipping CapOne accounts")
    for acct_name, pdf_path in sorted(capone_pdfs.items()):
        bal, d = parse_capitalone_pdf(pdf_path)
        balances[acct_name] = (bal, d)
        print(f"  {acct_name:<22} ${bal:>9.2f}  as of {d}")

    print("\nWriting to database...")
    updated = 0
    for acct_name, (bal, d) in balances.items():
        acct = Account.query.filter_by(name=acct_name).first()
        if acct is None:
            print(f"  WARN: Account '{acct_name}' not found in DB — skipped")
            continue
        acct.last_statement_balance = bal
        acct.last_statement_date = d
        updated += 1
    db.session.commit()
    print(f"Updated {updated} account(s).")


def print_summary():
    print("\n--- Account table (current state) ---")
    print(f"{'id':>3}  {'name':<26}  {'institution':<18}  {'last4':>4}  {'last_statement_balance':>21}  {'last_statement_date':<12}  {'days_since':>10}")
    print("-" * 103)
    for a in Account.query.order_by(Account.id).all():
        bal = f"${a.last_statement_balance:>10.2f}" if a.last_statement_balance is not None else "            None"
        d = str(a.last_statement_date) if a.last_statement_date else "        None"
        days = str(a.days_since_last_statement) if a.days_since_last_statement is not None else "None"
        print(f"{a.id:>3}  {a.name:<26}  {a.institution:<18}  {str(a.last4 or ''):>4}  {bal:>21}  {d:<12}  {days:>10}")


with app.app_context():
    add_columns_if_missing()
    backfill_balances()
    print_summary()
