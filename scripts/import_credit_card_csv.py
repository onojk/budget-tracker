#!/usr/bin/env python3
"""
Generic importer for credit card / debt statement CSVs.

This script converts a CSV into Transaction rows using your existing
Transaction.from_dict() convention:

    {
        "Date":        <date or string>,
        "Source":      <source_system>,   # e.g. "Capital One"
        "Account":     <account_name>,    # e.g. "Cap One Quicksilver"
        "Direction":   "debit"|"credit",
        "Amount":      <float>,
        "Merchant":    <merchant>,
        "Description": <desc>,
        "Category":    "",
        "Notes":       <optional>,
    }

Usage examples
--------------

1) Amount sign determines direction:
   - Positive = charge (debit), Negative = payment/refund (credit)

    python import_credit_card_csv.py \\
        --csv /path/to/capone.csv \\
        --source "Capital One" \\
        --account "Cap One Quicksilver" \\
        --date-col "Transaction Date" \\
        --date-format "%m/%d/%Y" \\
        --amount-col "Amount" \\
        --merchant-col "Description" \\
        --desc-col "Description" \\
        --direction-mode sign

2) Separate type column (e.g. "DEBIT"/"CREDIT" or "charge"/"payment"):

    python import_credit_card_csv.py \\
        --csv /path/to/chase_card.csv \\
        --source "Chase Card" \\
        --account "Chase Freedom" \\
        --date-col "Post Date" \\
        --date-format "%Y-%m-%d" \\
        --amount-col "Amount" \\
        --merchant-col "Merchant" \\
        --desc-col "Merchant" \\
        --direction-mode column \\
        --direction-col "Type" \\
        --debit-values "DEBIT,CHARGE,PURCHASE" \\
        --credit-values "CREDIT,PAYMENT,REFUND"

By default the script:
- Skips blank rows
- Skips lines where date or amount can’t be parsed
- Skips rows that already exist (same date, amount, merchant, account, source)
"""

import argparse
import csv
from datetime import datetime

from sqlalchemy import and_

from app import app, db, Transaction


def parse_args():
    p = argparse.ArgumentParser(description="Import credit card / debt CSV into Transaction table.")

    p.add_argument("--csv", required=True, help="Path to CSV file.")

    p.add_argument("--source", required=True, help='Value for Source (source_system), e.g. "Capital One".')
    p.add_argument("--account", required=True, help='Value for Account (account_name), e.g. "Cap One Quicksilver".')

    p.add_argument("--date-col", required=True, help="CSV column name for the transaction date.")
    p.add_argument(
        "--date-format",
        default="%m/%d/%Y",
        help="Python strptime format for the date column. Default: %%m/%%d/%%Y",
    )

    p.add_argument("--amount-col", required=True, help="CSV column name for the amount.")
    p.add_argument(
        "--merchant-col",
        required=True,
        help="CSV column for merchant/payee (used as Merchant field).",
    )
    p.add_argument(
        "--desc-col",
        required=True,
        help="CSV column for description (used as Description field; can be same as merchant).",
    )

    # How to derive debit / credit
    p.add_argument(
        "--direction-mode",
        choices=["sign", "column"],
        default="sign",
        help="How to derive debit/credit: 'sign' (from amount sign) or 'column' (from a type column).",
    )
    p.add_argument(
        "--direction-col",
        default=None,
        help="If direction-mode=column, CSV column that holds a type indicator (e.g. DEBIT/CREDIT).",
    )
    p.add_argument(
        "--debit-values",
        default="DEBIT,CHARGE,PURCHASE,SALE",
        help="Comma-separated values in direction-col that should be treated as debits.",
    )
    p.add_argument(
        "--credit-values",
        default="CREDIT,PAYMENT,REFUND,RETURN,ADJUSTMENT",
        help="Comma-separated values in direction-col that should be treated as credits.",
    )

    p.add_argument(
        "--notes-col",
        default=None,
        help="Optional CSV column to append into Notes.",
    )

    p.add_argument(
        "--prefix-notes",
        default=None,
        help="Optional string to prepend to Notes, e.g. '[CC_IMPORT Capital One]'.",
    )

    p.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip rows that already exist (same date, amount, merchant, account, source). Default: True.",
    )

    return p.parse_args()


def normalize_str(x):
    return (x or "").strip()


def infer_direction_from_sign(amount):
    # You can invert this if you prefer the opposite convention
    if amount > 0:
        return "debit"   # purchase / charge
    elif amount < 0:
        return "credit"  # payment / refund
    else:
        # interest / fees / oddities; treat as debit by default
        return "debit"


def infer_direction_from_column(raw_value, debit_set, credit_set):
    if raw_value is None:
        return None
    s = normalize_str(raw_value).upper()
    if s in debit_set:
        return "debit"
    if s in credit_set:
        return "credit"
    return None


def row_exists(date_obj, amount, merchant, account, source):
    """Check if a non-transfer row with this signature already exists."""
    q = (
        db.session.query(Transaction)
        .filter(
            Transaction.date == date_obj,
            Transaction.amount == amount,
            Transaction.merchant == merchant,
            Transaction.account_name == account,
            Transaction.source_system == source,
            Transaction.is_transfer.is_(False),
        )
    )
    return db.session.query(q.exists()).scalar()


def main():
    args = parse_args()

    debit_values = {s.strip().upper() for s in args.debit_values.split(",") if s.strip()}
    credit_values = {s.strip().upper() for s in args.credit_values.split(",") if s.strip()}

    total = 0
    imported = 0
    skipped_existing = 0
    skipped_invalid = 0

    with app.app_context():
        with open(args.csv, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1

                # Basic safety: require date & amount feel non-empty
                raw_date = normalize_str(row.get(args.date-col))
                raw_amount = normalize_str(row.get(args.amount-col))

                if not raw_date or not raw_amount:
                    skipped_invalid += 1
                    continue

                try:
                    date_obj = datetime.strptime(raw_date, args.date-format).date()
                except Exception:
                    skipped_invalid += 1
                    continue

                try:
                    amount = float(raw_amount.replace(",", ""))
                except Exception:
                    skipped_invalid += 1
                    continue

                merchant = normalize_str(row.get(args.merchant-col))
                desc = normalize_str(row.get(args.desc-col))

                # Direction
                if args.direction-mode == "sign":
                    direction = infer_direction_from_sign(amount)
                else:
                    raw_dir = row.get(args.direction-col)
                    direction = infer_direction_from_column(raw_dir, debit_values, credit_values)
                    # Fallback if we couldn't map it:
                    if direction is None:
                        direction = infer_direction_from_sign(amount)

                # Notes
                notes_parts = []
                if args.prefix-notes:
                    notes_parts.append(args.prefix-notes.strip())
                if args.notes-col:
                    extra = normalize_str(row.get(args.notes-col))
                    if extra:
                        notes_parts.append(extra)
                notes = " ".join(notes_parts)

                # Skip if already present
                if args.skip-existing and row_exists(date_obj, amount, merchant, args.account, args.source):
                    skipped_existing += 1
                    continue

                # Build dict in the format Transaction.from_dict expects
                data = {
                    "Date": date_obj,
                    "Source": args.source,
                    "Account": args.account,
                    "Direction": direction,
                    "Amount": amount,
                    "Merchant": merchant,
                    "Description": desc,
                    "Category": "",
                    "Notes": notes,
                }

                tx = Transaction.from_dict(data)
                db.session.add(tx)
                imported += 1

        db.session.commit()

    print("Import complete.")
    print(f"  Total CSV rows seen:     {total}")
    print(f"  Imported new rows:       {imported}")
    print(f"  Skipped existing rows:   {skipped_existing}")
    print(f"  Skipped invalid rows:    {skipped_invalid}")


if __name__ == "__main__":
    main()
