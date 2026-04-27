"""
One-off migration: create Account table, add account_id FK to transaction,
seed 3 Account rows, and backfill account_name + account_id for all 439
existing Chase transactions.

Run from the project root with the venv active:
    python scripts/migrate_add_accounts.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

from app import app
from models import db, Account, Transaction

SEED_ACCOUNTS = [
    {"name": "BoA Adv Plus",         "institution": "Bank of America", "last4": "0205"},
    {"name": "Chase Checking",       "institution": "JPMorgan Chase",  "last4": "9765"},
    {"name": "Chase Savings",        "institution": "JPMorgan Chase",  "last4": "9383"},
    {"name": "Venmo",                "institution": "Venmo",           "last4": None},
    {"name": "CapOne Platinum 0728", "institution": "Capital One",     "last4": "0728"},
    {"name": "CapOne Quicksilver 7398", "institution": "Capital One",  "last4": "7398"},
    {"name": "Citi Costco Anywhere Visa",          "institution": "Citibank",       "last4": "2557"},
    {"name": "CareCredit Rewards Mastercard",       "institution": "Synchrony Bank", "last4": "7649"},
    {"name": "PayPal Cashback Mastercard",          "institution": "Synchrony Bank", "last4": "9868"},
    {"name": "PayPal Account",                      "institution": "PayPal",         "last4": None},
]

with app.app_context():
    # Create new tables (account); existing tables are left alone.
    db.create_all()

    # Add account_id column to transaction table if it doesn't exist yet.
    from sqlalchemy import text, inspect
    insp = inspect(db.engine)
    existing_cols = [c["name"] for c in insp.get_columns("transaction")]
    if "account_id" not in existing_cols:
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE "transaction" ADD COLUMN account_id INTEGER'))
        print("Added account_id column to transaction table.")
    else:
        print("account_id column already exists — skipping ALTER TABLE.")

    # Seed accounts (idempotent).
    for spec in SEED_ACCOUNTS:
        if not Account.query.filter_by(name=spec["name"]).first():
            db.session.add(Account(**spec))
    db.session.commit()
    print("Account rows seeded.")

    # Build name → id lookup.
    acct_map = {a.name: a.id for a in Account.query.all()}
    checking_id = acct_map["Chase Checking"]
    savings_id  = acct_map["Chase Savings"]

    # Backfill existing transactions.
    #
    # Heuristic: Chase combined statements list the Savings account by
    # embedding "Chk ...9765" in transfer descriptions that point *to*
    # Savings, but those rows themselves belong to Checking.  The cleaner
    # signal is the account_name string set by the parser — after the
    # Chase parser update it will be "Chase Checking" / "Chase Savings".
    # Until then, fall back to: rows already labelled "Chase Savings"
    # go to savings; everything else in JPMorgan goes to checking.

    chase_txs = Transaction.query.filter(
        Transaction.account_name.ilike("Chase%")
    ).all()

    updated = 0
    for tx in chase_txs:
        if tx.account_name == "Chase Savings":
            target_id   = savings_id
            target_name = "Chase Savings"
        else:
            target_id   = checking_id
            target_name = "Chase Checking"

        changed = False
        if tx.account_id != target_id:
            tx.account_id = target_id
            changed = True
        if not tx.account_name:
            tx.account_name = target_name
            changed = True
        if changed:
            updated += 1

    db.session.commit()
    print(f"Backfilled {updated} Chase transactions.")

    # Summary
    for a in Account.query.order_by(Account.id).all():
        cnt = Transaction.query.filter_by(account_id=a.id).count()
        print(f"  {a.name!r:25s} id={a.id}  transactions={cnt}")
