#!/usr/bin/env python3
"""
import_all_ocr_to_db.py

Use the generic OCR parser (process_statement_files) on ALL *_ocr.txt
files in uploads/statements, then insert the parsed rows into the
Transactions table.

This bypasses the frontend upload flow and gives us a clean
"CLI import everything" command.
"""

from pathlib import Path
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app import app, db, Transaction
from ocr_pipeline import process_statement_files


BASE_DIR = Path(__file__).resolve().parent
STATEMENTS_DIR = BASE_DIR / "uploads" / "statements"


def _row_to_kwargs(row):
    """
    Normalize a parsed row (dict or simple object) into kwargs
    suitable for Transaction(...).

    We support both capitalized and lowercase field names to match
    what process_statement_files() currently returns, e.g.:

    {
        'Date': '2025-06-18',
        'Amount': 1000.0,
        'Direction': 'credit',
        'Source': 'Statement OCR',
        'Account': '',
        'Merchant': '...',
        'Description': '...',
        'Category': '',
        'Notes': 'from 2024-12-19_ocr.txt'
    }
    """

    # Helper to pull values from dict or attributes
    def get_any(names, default=None):
        if isinstance(row, dict):
            for name in names:
                if name in row and row[name] not in (None, ""):
                    return row[name]
        else:
            for name in names:
                if hasattr(row, name):
                    val = getattr(row, name)
                    if val not in (None, ""):
                        return val
        return default

    # --- Date ---
    dt = get_any(["Date", "date", "txn_date", "posted_date"])
    if isinstance(dt, datetime):
        dt = dt.date()
    elif isinstance(dt, str):
        dt = dt.strip()
        try:
            # Expect YYYY-MM-DD
            dt = date.fromisoformat(dt)
        except Exception:
            dt = None
    elif isinstance(dt, date):
        # already fine
        pass
    else:
        dt = None

    # --- Raw Amount + Direction ---
    amt = get_any(["Amount", "amount", "signed_amount", "value"])
    direction = get_any(["Direction", "direction"])

    # Normalize amount to Decimal
    if isinstance(amt, str):
        amt_str = amt.replace(",", "").strip()
        try:
            amt = Decimal(amt_str)
        except InvalidOperation:
            amt = None
    elif isinstance(amt, (int, float)):
        amt = Decimal(str(amt))
    elif isinstance(amt, Decimal):
        pass
    else:
        amt = None

    # Apply direction to get signed amount (if we can)
    signed_amount = None
    if amt is not None:
        signed_amount = amt
        if direction:
            dir_l = str(direction).strip().lower()
            if dir_l.startswith("debit") and amt > 0:
                signed_amount = -amt
            elif dir_l.startswith("credit") and amt < 0:
                # If somehow already negative for a credit, flip
                signed_amount = -amt

    # --- Text fields ---
    merchant = get_any(["Merchant", "merchant", "payee"], default="")
    description = get_any(["Description", "description", "memo"], default="")
    category = get_any(["Category", "category"], default="")
    account_name = get_any(["Account", "account", "account_name"], default="")
    source_system = get_any(["Source", "source", "source_system"], default="Statement OCR")
    notes = get_any(["Notes", "notes"], default="")

    # Minimal validity check: require date and amount
    if dt is None or signed_amount is None:
        return None

    return dict(
        date=dt,
        amount=signed_amount,
        merchant=str(merchant)[:255] if merchant is not None else "",
        description=str(description)[:255] if description is not None else "",
        category=str(category)[:100] if category is not None else "",
        account_name=str(account_name)[:255] if account_name is not None else "",
        source_system=str(source_system)[:100] if source_system is not None else "Statement OCR",
        notes=str(notes) if notes is not None else "",
    )


def main():
    # Discover all *_ocr.txt files
    ocr_files = sorted(STATEMENTS_DIR.glob("*_ocr.txt"))
    print(f"[info] Found {len(ocr_files)} *_ocr.txt files in {STATEMENTS_DIR}")
    if not ocr_files:
        return

    # Call generic parser
    rows = process_statement_files(file_paths=[str(p) for p in ocr_files])
    print(f"[info] Calling process_statement_files(...) on all OCR files…")
    print(f"[info] Parser returned {len(rows)} row(s).")

    inserted = 0
    skipped = 0

    with app.app_context():
        before = db.session.query(Transaction).count()
        print(f"[info] Transactions BEFORE import: {before}")

        for row in rows:
            kwargs = _row_to_kwargs(row)
            if not kwargs:
                skipped += 1
                continue

            # Simple duplicate check:
            # Use date, amount, merchant, description, account_name, source_system
            exists = (
                db.session.query(Transaction.id)
                .filter_by(
                    date=kwargs["date"],
                    amount=kwargs["amount"],
                    merchant=kwargs["merchant"],
                    description=kwargs["description"],
                    account_name=kwargs["account_name"],
                    source_system=kwargs["source_system"],
                )
                .first()
            )
            if exists:
                skipped += 1
                continue

            tx = Transaction(**kwargs)
            db.session.add(tx)
            inserted += 1

        db.session.commit()
        after = db.session.query(Transaction).count()

    print(f"[info] Inserted: {inserted} row(s), skipped: {skipped} row(s).")
    print(f"[info] Transactions AFTER import: {after}")


if __name__ == "__main__":
    main()
