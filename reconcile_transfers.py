#!/usr/bin/env python3
"""
Reconcile internal transfers / mirrors between:
- PayPal ↔ credit card / bank
- Venmo ↔ credit card / bank
- Generic bank ↔ bank transfers

This script:
- Ensures is_transfer / linked_transaction_id columns exist on the transaction table
- Scans all transactions via ORM
- Detects likely internal transfers
- Marks the "mirror" side as is_transfer=True and links both rows
"""

from datetime import timedelta

from sqlalchemy import inspect, text

from app import app, db, Transaction


# --------- String helpers --------- #

def normalize_str(s):
    return (s or "").strip().upper()


def looks_like_paypal(merchant, desc=""):
    s = normalize_str(merchant) + " " + normalize_str(desc)
    return "PAYPAL" in s or "PP*" in s


def looks_like_venmo(merchant, desc=""):
    s = normalize_str(merchant) + " " + normalize_str(desc)
    return "VENMO" in s


def looks_like_transfer_description(merchant, desc=""):
    s = normalize_str(merchant) + " " + normalize_str(desc)
    keywords = [
        "TRANSFER",
        "XFER",
        "ONLINE TRANSFER",
        "ONLINE XFER",
        "ACH TRANSFER",
        "ACH CREDIT",
        "ACH DEBIT",
        "ZELLE",
    ]
    return any(k in s for k in keywords)


# --------- Classification helpers (work on Transaction objects) --------- #

def get_raw_description(tx: Transaction) -> str:
    # Gracefully handle if you don't have raw_description column
    return normalize_str(getattr(tx, "raw_description", "") or "")


def is_paypal(tx: Transaction) -> bool:
    src = normalize_str(tx.source_system or "")
    acct = normalize_str(tx.account_name or "")
    merch = normalize_str(tx.merchant or "")
    desc = get_raw_description(tx)
    return (
        "PAYPAL" in src
        or "PAYPAL" in acct
        or looks_like_paypal(merch, desc)
    )


def is_venmo(tx: Transaction) -> bool:
    src = normalize_str(tx.source_system or "")
    acct = normalize_str(tx.account_name or "")
    merch = normalize_str(tx.merchant or "")
    desc = get_raw_description(tx)
    return (
        "VENMO" in src
        or "VENMO" in acct
        or looks_like_venmo(merch, desc)
    )


def is_bank_or_card(tx: Transaction) -> bool:
    # Anything that's not explicitly PayPal/Venmo counts as bank/card here
    if is_paypal(tx) or is_venmo(tx):
        return False
    return True


def looks_like_transfer_pair(t1: Transaction, t2: Transaction) -> bool:
    """
    Generic "internal transfer" test:
    - different accounts
    - opposite signs, same absolute amount (within rounding)
    - dates within 2 days
    - at least one side looks like a transfer / PayPal / Venmo
    """
    if t1.account_name == t2.account_name:
        return False

    if t1.amount is None or t2.amount is None:
        return False

    # Opposite signs
    if float(t1.amount) * float(t2.amount) >= 0:
        return False

    # Same absolute amount (round to cents)
    if round(abs(float(t1.amount)) - abs(float(t2.amount)), 2) != 0:
        return False

    if t1.date is None or t2.date is None:
        return False

    day_diff = abs((t2.date - t1.date).days)
    if day_diff > 2:
        return False

    m1 = t1.merchant or ""
    m2 = t2.merchant or ""
    desc1 = get_raw_description(t1)
    desc2 = get_raw_description(t2)

    transferish = (
        looks_like_transfer_description(m1, desc1)
        or looks_like_transfer_description(m2, desc2)
        or is_paypal(t1)
        or is_paypal(t2)
        or is_venmo(t1)
        or is_venmo(t2)
    )

    return transferish


def pick_primary_vs_mirror(t1: Transaction, t2: Transaction):
    """
    Decide which record should be considered the "real" transaction
    for budgeting, and which is just a mirror/transfer.

    Rules:
    - Prefer Venmo/PayPal over generic bank/card entries
    - If both are bank/card, just pick t1 as primary
    """
    t1_venmo = is_venmo(t1)
    t2_venmo = is_venmo(t2)
    if t1_venmo and not t2_venmo:
        return t1, t2
    if t2_venmo and not t1_venmo:
        return t2, t1

    t1_paypal = is_paypal(t1)
    t2_paypal = is_paypal(t2)
    if t1_paypal and not t2_paypal:
        return t1, t2
    if t2_paypal and not t1_paypal:
        return t2, t1

    # Otherwise they're both bank/card or both the same type
    return t1, t2


# --------- DB column safety --------- #

def ensure_columns_exist():
    """
    Ensure is_transfer and linked_transaction_id columns exist on the table.
    Uses inspector + raw ALTER TABLE via db.session.execute.

    NOTE: table name is "transaction", which is a reserved word,
    so we quote it as "transaction".
    """
    engine = db.engine
    inspector = inspect(engine)
    table_name = Transaction.__tablename__
    print(f"Using table: {table_name}")

    quoted_table = f'"{table_name}"'  # e.g. "transaction" for SQLite

    cols = {c["name"] for c in inspector.get_columns(table_name)}

    if "is_transfer" not in cols:
        print(f"Adding column is_transfer to {table_name}")
        db.session.execute(
            text(f"ALTER TABLE {quoted_table} ADD COLUMN is_transfer BOOLEAN DEFAULT 0 NOT NULL")
        )

    if "linked_transaction_id" not in cols:
        print(f"Adding column linked_transaction_id to {table_name}")
        db.session.execute(
            text(f"ALTER TABLE {quoted_table} ADD COLUMN linked_transaction_id INTEGER")
        )

    db.session.commit()


# --------- Main reconciliation routine --------- #

def reconcile_internal_transfers():
    with app.app_context():
        ensure_columns_exist()

        txs = (
            db.session.query(Transaction)
            .order_by(Transaction.date, Transaction.id)
            .all()
        )

        print(f"Loaded {len(txs)} transactions from DB.")

        # Index by absolute amount for speed
        by_amount = {}
        for t in txs:
            if t.amount is None:
                continue
            key = round(abs(float(t.amount)), 2)
            by_amount.setdefault(key, []).append(t)

        seen_pairs = set()
        num_marked = 0

        for amount, group in by_amount.items():
            group.sort(key=lambda t: (t.date, t.id))

            for i, t1 in enumerate(group):
                for t2 in group[i + 1 :]:
                    # stop if dates > 2 days apart
                    if t1.date and t2.date and (t2.date - t1.date).days > 2:
                        break

                    pair_key = tuple(sorted((t1.id, t2.id)))
                    if pair_key in seen_pairs:
                        continue

                    if not looks_like_transfer_pair(t1, t2):
                        continue

                    # If they're already marked/linked, skip
                    if getattr(t1, "is_transfer", False) and t1.linked_transaction_id:
                        if getattr(t2, "is_transfer", False) and t2.linked_transaction_id:
                            continue

                    primary, mirror = pick_primary_vs_mirror(t1, t2)

                    if not getattr(mirror, "is_transfer", False):
                        mirror.is_transfer = True
                        num_marked += 1

                    primary.linked_transaction_id = mirror.id
                    mirror.linked_transaction_id = primary.id

                    seen_pairs.add(pair_key)

                    print(
                        f"[TRANSFER] primary={primary.id}({primary.account_name}, {primary.merchant}, {primary.amount}) "
                        f"<-> mirror={mirror.id}({mirror.account_name}, {mirror.merchant}, {mirror.amount})"
                    )

        db.session.commit()
        print(f"Reconciliation complete. Marked {num_marked} mirror transactions as transfers.")


if __name__ == "__main__":
    reconcile_internal_transfers()
