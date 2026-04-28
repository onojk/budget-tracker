#!/usr/bin/env python3
"""
scripts/correct_wrong_sign_rows_2026_04_27.py

One-shot data-correction script applied 2026-04-27.
Fixes three categories of parser bugs found during reconciliation of the
19 new statements imported 2026-04-27 (10 Chase + 9 BoA).

Run from project root with the venv active:
    python scripts/correct_wrong_sign_rows_2026_04_27.py

The script is IDEMPOTENT: each operation checks preconditions before
executing so it can be re-run safely without double-applying corrections.

==========================================================================
BUG CLASS A: Sign-inference mis-fire (Chase PDF parser)
==========================================================================

Root cause (ocr_pipeline.py, _parse_chase_transaction_detail):
    The parser called parse_signed_amount(amt_str, context=...) which
    scores DEBIT_HINT_WORDS / CREDIT_HINT_WORDS against the full
    description string. Several hint words matched as substrings of
    longer tokens that have the OPPOSITE direction:

      "payment"  in DEBIT_HINT_WORDS fires on "Payments" (eBay seller
                 proceeds) and "Payment From" (Zelle inbound credit)
                 — these are credits, not debits.

      "purchase" in DEBIT_HINT_WORDS fires on "Card Purchase Return"
                 — these are refunds/credits, not purchases.

    Chase PDF format encodes direction unambiguously: debits carry a
    leading '-' in the amount column; credits have no sign prefix.
    The fix (commit 7eedb19) changes the parser to trust the explicit
    sign and skip keyword scoring entirely for Chase PDFs.

Affected rows corrected here: 10 rows, all Chase Checking.
    8 × Card Purchase Return (credits stored as debits)
    2 × Zelle Payment From   (credits stored as debits)

Correction: UPDATE amount = -amount, direction = 'credit' for each row.

==========================================================================
BUG CLASS B: Dedup collision on same-day identical transactions
==========================================================================

Root cause (app.py, is_duplicate_transaction):
    The dedup key is (date, amount, merchant, account_name). Two
    genuinely separate transactions that share all four fields are
    treated as one. This occurs in practice for:

      1. Same-day identical Venmo/Real-Time-Transfer credits from
         different senders — both have the same truncated merchant name.

      2. Same-day identical Card Purchase Returns from the same retailer
         — same date, amount, and description.

    When the first transaction was also stored with wrong sign (Bug A),
    the second arrived with the same wrong sign and was dropped. After
    fixing the first row's sign, the second is still missing.

Affected rows corrected here: 2 INSERT operations, Chase Checking.
    1 × $39.30 Real-Time-Transfer credit (2025-04-28, Stmt 14)
    1 × $2.70  Card Purchase Return credit (2026-01-05, Stmt 6)

Correction: INSERT the missing row as a credit with the correct amount.

==========================================================================
BUG CLASS C: Year-inference bug at year-boundary statements (Chase PDF)
==========================================================================

Root cause (ocr_pipeline.py, _parse_chase_transaction_detail):
    Chase statements that span two calendar years (e.g., Dec 2025 –
    Jan 2026) present two-digit month/day dates without a year. The
    parser infers the year from the statement period. When the savings
    account section is parsed AFTER the checking section has already
    seen January dates, the year-rollover logic assigns the end-year
    (2026) to December transactions in the savings block that should
    have the start-year (2025).

    The parser fix for this is deferred; a future commit should anchor
    the year to the statement's start-year for any month number greater
    than the statement-end month, and to the end-year otherwise.

Affected rows corrected here: 7 rows, Chase Savings, Stmt 6
    (Dec 13, 2025 → Jan 15, 2026). All stored as 2026-12-xx; correct
    dates are 2025-12-xx.

Correction: UPDATE date = REPLACE(date, '2026-12', '2025-12').

==========================================================================
Reconciliation result after all corrections:
    All 25 statements (10 Chase × 2 accounts + 9 BoA) closed to $0.00.
    Total transactions in DB after corrections: 2795.

==========================================================================
BUG CLASS C (ADDENDUM): Year-inference in Capital One parser (2026-04-27)
==========================================================================

Root cause (parsers/capitalone_pdf_parser.py):
    Same year-inference failure as Chase Savings: a CapOne statement
    spanning Dec 2025 / Jan 2026 assigned 2026 to the December
    transactions instead of 2025.

Affected rows: 2 rows, CapOne Platinum 0728 (ids 3164, 3165)
    id=3164  2026-12-19  +$25.00   CAPITAL ONE MOBILE PYMT
    id=3165  2026-12-27  -$40.31   DOORDASH JERSEYMIKSAN

Correction: UPDATE date = REPLACE(date, '2026-12', '2025-12').

Bug C is now confirmed in three parsers:
    1. Chase PDF     (_parse_chase_transaction_detail)  — 7 rows fixed 2026-04-27
    2. Capital One   (capitalone_pdf_parser.py)         — 2 rows fixed 2026-04-27
    3. ??? (watch for more at year boundaries in other parsers)
==========================================================================
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.pop("DATABASE_URL", None)

from app import app, db
from sqlalchemy import text


def run_correction():
    with app.app_context():
        # ----------------------------------------------------------------
        # BUG A: flip 10 wrong-sign rows (sign-inference mis-fire)
        # ----------------------------------------------------------------
        # These rows were stored with amount < 0 (debit) but the source
        # PDF amount column had no '-' prefix, meaning they are credits.
        #
        # Precondition check: each id should still have amount < 0.
        # If already positive, the flip has been applied — skip.
        # ----------------------------------------------------------------
        FLIP_IDS = [988, 1229, 1451, 1516, 1550, 1646, 1663, 1664, 1840, 1841]

        id_placeholders = ",".join(str(i) for i in FLIP_IDS)
        rows = db.session.execute(text(
            f"SELECT id, amount FROM \"transaction\" WHERE id IN ({id_placeholders}) ORDER BY id"
        )).fetchall()

        to_flip = [r[0] for r in rows if r[1] < 0]
        already_flipped = [r[0] for r in rows if r[1] >= 0]

        if already_flipped:
            print(f"Bug A: {len(already_flipped)} rows already positive (idempotent skip): {already_flipped}")
        if to_flip:
            flip_placeholders = ",".join(str(i) for i in to_flip)
            db.session.execute(text(
                f"UPDATE \"transaction\" SET amount = -amount, "
                f"direction = CASE WHEN direction='debit' THEN 'credit' ELSE 'debit' END "
                f"WHERE id IN ({flip_placeholders})"
            ))
            print(f"Bug A: flipped {len(to_flip)} rows to positive: {to_flip}")
        else:
            print("Bug A: all rows already correct — no action taken")

        # ----------------------------------------------------------------
        # BUG B: insert 2 dedup-victim rows
        # ----------------------------------------------------------------
        # Each INSERT is guarded by an existence check on a unique note
        # string to prevent double-insertion.
        # ----------------------------------------------------------------
        cc_id = db.session.execute(text(
            "SELECT id FROM account WHERE name = 'Chase Checking'"
        )).scalar()

        inserts = [
            {
                "date": "2025-04-28",
                "amount": 39.30,
                "direction": "credit",
                "merchant": "Real Time Transfer Recd From Aba/Contr Bnk-021000021 From:",
                "description": "Real Time Transfer Recd From Aba/Contr Bnk-021000021 From:",
                "note_tag": "dedup-victim-2025-04-28-39.30",
                "note": ("Inserted 2026-04-27: dedup victim — second of two identical $39.30 "
                         "RT transfers on 2025-04-28 in Stmt(14). First was stored with wrong "
                         "sign (-39.30), causing dedup to drop this one."),
            },
            {
                "date": "2026-01-05",
                "amount": 2.70,
                "direction": "credit",
                "merchant": "Card Purchase Return 01/04 Amazon.Com Amzn.Com/Bill WA Card 9241",
                "description": "Card Purchase Return 01/04 Amazon.Com Amzn.Com/Bill WA Card 9241",
                "note_tag": "dedup-victim-2026-01-05-2.70",
                "note": ("Inserted 2026-04-27: dedup victim — second of two identical $2.70 "
                         "Amazon Card Purchase Returns on 2026-01-05 in Stmt(6). First was "
                         "stored with wrong sign (-2.70), causing dedup to drop this one."),
            },
        ]

        for ins in inserts:
            # Idempotency: check for any row with this date, amount, and account
            # that has "dedup" in the notes (matches both manual and script inserts).
            existing = db.session.execute(text(
                "SELECT COUNT(*) FROM \"transaction\" "
                "WHERE date = :date AND amount = :amount AND account_id = :acct "
                "AND notes LIKE '%dedup%'"
            ), {"date": ins["date"], "amount": ins["amount"], "acct": cc_id}).scalar()
            if existing:
                print(f"Bug B: {ins['date']} ${ins['amount']:.2f} already present — skip")
                continue
            db.session.execute(text(
                """INSERT INTO "transaction"
                   (is_transfer, date, amount, direction, merchant, description,
                    account_id, account_name, source_system, category, notes)
                   VALUES (0, :date, :amount, :direction, :merchant, :description,
                           :acct_id, 'Chase Checking', 'Statement OCR', '', :note)"""
            ), {
                "date": ins["date"],
                "amount": ins["amount"],
                "direction": ins["direction"],
                "merchant": ins["merchant"],
                "description": ins["description"],
                "acct_id": cc_id,
                "note": ins["note"],
            })
            print(f"Bug B: inserted {ins['note_tag']}")

        # ----------------------------------------------------------------
        # BUG C: fix 7 wrong-year dates in Chase Savings (Stmt 6)
        # ----------------------------------------------------------------
        # Precondition: rows should have dates starting with '2026-12'.
        # If already '2025-12', the fix has been applied — skip.
        # ----------------------------------------------------------------
        DATE_FIX_IDS = [1697, 1698, 1699, 1700, 1701, 1702, 1703]

        date_id_placeholders = ",".join(str(i) for i in DATE_FIX_IDS)
        rows_c = db.session.execute(text(
            f"SELECT id, date FROM \"transaction\" WHERE id IN ({date_id_placeholders}) ORDER BY id"
        )).fetchall()

        to_fix = [r[0] for r in rows_c if r[1].startswith("2026-12")]
        already_fixed = [r[0] for r in rows_c if not r[1].startswith("2026-12")]

        if already_fixed:
            print(f"Bug C: {len(already_fixed)} rows already corrected (idempotent skip): {already_fixed}")
        if to_fix:
            fix_placeholders = ",".join(str(i) for i in to_fix)
            db.session.execute(text(
                f"UPDATE \"transaction\" SET date = REPLACE(date, '2026-12', '2025-12') "
                f"WHERE id IN ({fix_placeholders})"
            ))
            print(f"Bug C: corrected year on {len(to_fix)} rows: {to_fix}")
        else:
            print("Bug C: all rows already correct — no action taken")

        # ----------------------------------------------------------------
        # BUG C (ADDENDUM): fix 2 wrong-year dates in CapOne Platinum
        # ----------------------------------------------------------------
        # Same year-inference bug as Chase Savings above, but triggered
        # in capitalone_pdf_parser.py for a Dec 2025 / Jan 2026 statement.
        # Idempotent: skip rows already dated 2025-12.
        # ----------------------------------------------------------------
        CAONE_DATE_FIX_IDS = [3164, 3165]

        caone_placeholders = ",".join(str(i) for i in CAONE_DATE_FIX_IDS)
        rows_caone = db.session.execute(text(
            f"SELECT id, date FROM \"transaction\" WHERE id IN ({caone_placeholders}) ORDER BY id"
        )).fetchall()

        to_fix_caone = [r[0] for r in rows_caone if str(r[1]).startswith("2026-12")]
        already_fixed_caone = [r[0] for r in rows_caone if not str(r[1]).startswith("2026-12")]

        if already_fixed_caone:
            print(f"Bug C (CapOne): {len(already_fixed_caone)} rows already corrected (skip): {already_fixed_caone}")
        if to_fix_caone:
            caone_fix_placeholders = ",".join(str(i) for i in to_fix_caone)
            db.session.execute(text(
                f"UPDATE \"transaction\" SET date = REPLACE(CAST(date AS TEXT), '2026-12', '2025-12') "
                f"WHERE id IN ({caone_fix_placeholders})"
            ))
            print(f"Bug C (CapOne): corrected year on {len(to_fix_caone)} rows: {to_fix_caone}")
        else:
            print("Bug C (CapOne): all rows already correct — no action taken")

        db.session.commit()
        print("\nAll corrections committed.")

        total = db.session.execute(text('SELECT COUNT(*) FROM "transaction"')).scalar()
        print(f"Total transactions in DB: {total}")


if __name__ == "__main__":
    run_correction()
