#!/usr/bin/env python3
"""
Helpers to ingest OCR'd rows (statements + screenshots) directly into Transaction.

Design:
- You give us a list of dict rows from OCR.
- We normalize them and upsert into the DB.
- Re-running on the same docs is safe: we avoid creating infinite duplicates.

IMPORTANT:
- This module does NOT import `app` to avoid circular imports.
- Caller MUST ensure a Flask app context is active
  (e.g. `with app.app_context(): import_ocr_rows(rows)`).

EXPECTED INPUT PER ROW
----------------------
Each row dict should have AT LEAST:

    {
        "Date": <date or string>,
        "Amount": <float or string>,
        "Merchant": <str>,
        "Source": <str>,        # e.g. "Chase", "Cap One", "Venmo", "Screenshot OCR"
        "Account": <str>,       # e.g. "Chase Freedom", "CapOne Quicksilver", "Acct *5072"
        "Direction": <str>,     # "debit" or "credit"
        "Description": <str>,   # optional
        "Category": <str>,      # optional
        "Notes": <str>,         # optional
    }

We treat (date, amount, merchant, account_name, source_system) as the
"identity" of an OCR row. If that already exists with is_transfer=False,
we skip inserting a duplicate.
"""

from datetime import date as _date

import pandas as _pd
from sqlalchemy import and_

from models import db, Transaction


def _normalize_date(raw_date):
    if isinstance(raw_date, _date):
        return raw_date
    if raw_date is None or raw_date == "":
        return None
    return _pd.to_datetime(raw_date).date()


def _normalize_amount(raw_amount):
    if raw_amount is None or raw_amount == "":
        return 0.0
    try:
        return float(raw_amount)
    except Exception:
        # strip $, commas, etc.
        s = str(raw_amount).replace("$", "").replace(",", "")
        return float(s)


def import_ocr_rows(rows, default_source="Screenshot OCR", default_account=""):
    """
    Main entry point.

    rows: list of dicts as described above.
    default_source: used if row["Source"] is missing/empty.
    default_account: used if row["Account"] is missing/empty.

    NOTE: Requires an active Flask app context.
    Returns: (inserted_count, skipped_existing_count)
    """
    inserted = 0
    skipped = 0

    for raw in rows:
        raw_date = raw.get("Date")
        date_val = _normalize_date(raw_date)
        if date_val is None:
            # skip rows that we can't date
            skipped += 1
            continue

        amount_val = _normalize_amount(raw.get("Amount"))

        merchant = (raw.get("Merchant") or "").strip()
        source = (raw.get("Source") or default_source).strip()
        account = (raw.get("Account") or default_account).strip()
        direction = (raw.get("Direction") or "debit").strip().lower()
        description = (raw.get("Description") or "").strip()
        category = (raw.get("Category") or "").strip()
        notes = (raw.get("Notes") or "").strip()

        # Check if we already have this row as a "real" (non-transfer) row.
        existing = (
            db.session.query(Transaction)
            .filter(
                and_(
                    Transaction.date == date_val,
                    Transaction.amount == amount_val,
                    Transaction.merchant == merchant,
                    Transaction.account_name == account,
                    Transaction.source_system == source,
                    Transaction.is_transfer.is_(False),
                )
            )
            .first()
        )

        if existing:
            # We've already imported this exact OCR row.
            skipped += 1
            continue

        tx = Transaction(
            date=date_val,
            source_system=source,
            account_name=account,
            direction=direction,
            amount=amount_val,
            merchant=merchant,
            description=description,
            category=category,
            notes=notes,
        )

        db.session.add(tx)
        inserted += 1

    db.session.commit()

    print(f"OCR import: inserted={inserted}, skipped_existing={skipped}")
    return inserted, skipped
