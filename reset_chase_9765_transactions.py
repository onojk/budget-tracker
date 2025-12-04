#!/usr/bin/env python3
from app import app, db, Transaction

with app.app_context():
    # TODO: adjust filters to exactly match your Chase account
    q = (
        db.session.query(Transaction)
        .filter(Transaction.account_name.ilike("%9765%"))
        # .filter(Transaction.source_system == "Chase OCR")  # if you use this field
    )

    count = q.count()
    print(f"About to delete {count} transactions for Chase 9765...")
    confirm = input("Type 'yes' to confirm: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
    else:
        q.delete(synchronize_session=False)
        db.session.commit()
        print("Deleted. Commit complete.")
