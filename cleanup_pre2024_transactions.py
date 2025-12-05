#!/usr/bin/env python3
"""
cleanup_pre2024_transactions.py

Delete all Transaction rows with date < 2024-01-01.
Use once after you decide 2024-01-01 is your hard cutoff.
"""

from datetime import date
from app import app, db, Transaction

CUTOFF = date(2024, 1, 1)

def main():
    with app.app_context():
        print(f"Deleting transactions with date < {CUTOFF} ...")
        q = db.session.query(Transaction).filter(Transaction.date < CUTOFF)
        count = q.count()
        print(f"Found {count} rows to delete.")
        if count:
            q.delete(synchronize_session=False)
            db.session.commit()
            print("Delete committed.")
        else:
            print("Nothing to delete.")

if __name__ == "__main__":
    main()
