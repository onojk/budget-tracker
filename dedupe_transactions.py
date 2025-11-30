#!/usr/bin/env python3
"""
Dedupe transactions by marking extra copies as is_transfer=True.

Two passes:

A) True duplicates within the same source:
   - same (date, amount, merchant, account_name, source_system)
   -> keep lowest-id row, mark others as is_transfer=True, notes += "[AUTO_DUPLICATE]"

B) Screenshot OCR clones of another source:
   - source_system = "Screenshot OCR"
   - same (date, amount, merchant) as some other transaction with different source_system
   -> mark the Screenshot OCR row as is_transfer=True, notes += "[AUTO_SSHOT_DUP]"
"""

from sqlalchemy import func

from app import app, db, Transaction


def mark_true_duplicates():
    """Pass A: true duplicates within the same source."""
    print("Pass A: marking intra-source exact duplicates...")

    dup_groups = (
        db.session.query(
            Transaction.date,
            Transaction.amount,
            Transaction.merchant,
            Transaction.account_name,
            Transaction.source_system,
            func.count().label("cnt"),
        )
        .group_by(
            Transaction.date,
            Transaction.amount,
            Transaction.merchant,
            Transaction.account_name,
            Transaction.source_system,
        )
        .having(func.count() > 1)
        .all()
    )

    print(f"  Found {len(dup_groups)} duplicate groups (same source).")

    marked = 0

    for g in dup_groups:
        date, amount, merchant, account_name, source_system, cnt = g

        rows = (
            db.session.query(Transaction)
            .filter(
                Transaction.date == date,
                Transaction.amount == amount,
                Transaction.merchant == merchant,
                Transaction.account_name == account_name,
                Transaction.source_system == source_system,
            )
            .order_by(Transaction.id)
            .all()
        )

        if len(rows) <= 1:
            continue

        keep = rows[0]
        for r in rows[1:]:
            if not r.is_transfer:
                r.is_transfer = True
                if r.notes:
                    if "[AUTO_DUPLICATE]" not in r.notes:
                        r.notes = r.notes + " [AUTO_DUPLICATE]"
                else:
                    r.notes = "[AUTO_DUPLICATE]"
                marked += 1

        print(
            f"  Group ({date}, {amount}, {merchant}, {account_name}, {source_system}) "
            f"- kept id={keep.id}, marked {len(rows) - 1} as duplicates."
        )

    db.session.commit()
    print(f"Pass A complete. Marked {marked} rows as is_transfer=True.")
    return marked


def mark_screenshot_clones():
    """Pass B: mark Screenshot OCR rows that duplicate any other source on (date, amount, merchant)."""
    print("Pass B: marking Screenshot OCR clones of other sources...")

    # Build an index of (date, amount, merchant) -> has_non_screenshot flag
    key_to_non_screenshot = {}

    all_rows = db.session.query(Transaction).all()
    for t in all_rows:
        if t.date is None or t.amount is None or t.merchant is None:
            continue
        key = (t.date, float(t.amount), t.merchant)

        is_screenshot = (t.source_system or "").strip().lower() == "screenshot ocr"
        info = key_to_non_screenshot.get(key, {"has_non_screenshot": False})

        if not is_screenshot:
            info["has_non_screenshot"] = True

        key_to_non_screenshot[key] = info

    # Now, for Screenshot OCR rows with a real twin, mark them
    marked = 0
    screenshot_rows = (
        db.session.query(Transaction)
        .filter(func.lower(Transaction.source_system) == "screenshot ocr")
        .all()
    )

    for t in screenshot_rows:
        if t.date is None or t.amount is None or t.merchant is None:
            continue
        key = (t.date, float(t.amount), t.merchant)
        info = key_to_non_screenshot.get(key)
        if not info:
            continue
        if not info.get("has_non_screenshot"):
            # No better source; keep it
            continue

        if not t.is_transfer:
            t.is_transfer = True
            if t.notes:
                if "[AUTO_SSHOT_DUP]" not in t.notes:
                    t.notes = t.notes + " [AUTO_SSHOT_DUP]"
            else:
                t.notes = "[AUTO_SSHOT_DUP]"
            marked += 1

            print(
                f"  Screenshot OCR clone id={t.id} marked as is_transfer for key={key}"
            )

    db.session.commit()
    print(f"Pass B complete. Marked {marked} Screenshot OCR rows as is_transfer=True.")
    return marked


def main():
    with app.app_context():
        total_marked = 0
        total_marked += mark_true_duplicates()
        total_marked += mark_screenshot_clones()
        print(f"Done. Total rows marked as is_transfer=True: {total_marked}")


if __name__ == "__main__":
    main()
