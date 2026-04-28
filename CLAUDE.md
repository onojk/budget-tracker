# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A Flask-based personal budget tracker. SQLite by default. Imports transactions
from CSVs, PDF bank statements (via `pdfplumber`), and OCR'd screenshots
(via `tesseract`). Inline editing on the transactions page via a small
JSON API.

## How to run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.py.example config.py        # edit if needed
cp .env.example .env                  # edit SECRET_KEY
python app.py                         # http://127.0.0.1:5000
```

The SQLite DB is created automatically on first run via `db.create_all()`
inside `app.py`. It lives at `instance/budget.db` (Flask resolves the
`sqlite:///budget.db` URI relative to its instance folder, not the project
root). The `instance/` directory is gitignored.

## Layout

```
app.py                    Flask app, routes, API, inline-edit handler
models.py                 SQLAlchemy models: Account, Transaction (FK → Account),
                          CategoryRule, OcrRejectedLine
config.py.example         Config template — copy to config.py
ocr_pipeline.py           PDF + screenshot ingestion; contains both
                          _parse_chase_transaction_detail and
                          parse_boa_statement_text, with a content-based
                          router in process_uploaded_statement_files
ocr_import_helpers.py     Shared helpers for OCR row imports
categorizer.py            Rule-based auto-categorization
direction_rules.py        Debit/credit direction inference
chase_amount_utils.py     Chase-specific amount parsing quirks
capitalone_validator.py   Capital One statement validation
parsers/                  Per-source parsers: venmo_csv_parser.py,
                          capitalone_pdf_parser.py
templates/                Jinja2 templates
static/                   styles.css, dashboard.js, htmx.min.js
scripts/                  One-off CLI utilities (see below)
scripts/migrate_add_accounts.py  One-time schema migration: creates Account
                          table, adds account_id FK, seeds 3 accounts.
                          Idempotent — safe to re-run on a fresh clone.
archive/                  Historical/broken files — DO NOT use as reference
```

## Scripts

`scripts/` contains CLI utilities for data lifecycle work. They expect to be
run from the project root with the venv active. Common ones:

- `migrate_add_accounts.py` — create Account table, seed accounts, backfill FKs
- `import_credit_card_csv.py` — import a credit-card CSV
- `import_all_pdfs_to_db.py` — bulk import PDF statements
- `import_screenshots_now.py` — bulk import OCR'd screenshots
- `import_new_statements.py` — targeted import of specific PDFs with per-statement reconciliation report
- `dedupe_transactions.py` — remove duplicate transactions
- `reconcile_transfers.py` — link mirrored transfers between accounts
- `validate_statement_balances.py` — check imported totals vs statement totals
- `correct_wrong_sign_rows_2026_04_27.py` — one-shot data correction (idempotent): 10 sign flips, 2 dedup inserts, 7 date fixes; see script header for root-cause docs
- `hard_reset_budget_data.py` — wipe all transactions (destructive)

Several of these are destructive. Read before running.

## API

```
GET  /api/transactions?limit=N        Latest transactions
GET  /api/summary                     Dashboard totals
PUT  /api/transactions/<id>           Update one field on a transaction
```

## Conventions

- Dates earlier than `2024-01-01` are rejected on import (see `MIN_ALLOWED_DATE`).
- Amounts: debits stored negative, credits positive (`coerce_amount` in `app.py`).
- All imports go through `ocr_pipeline.py` or `parsers/` — don't write SQL directly
  from new import scripts; use `Transaction.from_dict()` so dedup/normalization
  stays consistent.
- `config.py` is gitignored. Anything secret-like goes there or in `.env`.
- Every Transaction should have `account_id` set (FK to Account). The three
  seeded accounts are:
  - **BoA Adv Plus** — Bank of America, last-4 0205
  - **Chase Checking** — JPMorgan Chase, last-4 9765
  - **Chase Savings** — JPMorgan Chase, last-4 9383
  - **Venmo** — Venmo, no last-4
  - **CapOne Platinum 0728** — Capital One, last-4 0728
  - **CapOne Quicksilver 7398** — Capital One, last-4 7398
  - **Citi Costco Anywhere Visa** — Citibank, last-4 2557
  - **CareCredit Rewards Mastercard** — Synchrony Bank, last-4 7649
  - **PayPal Cashback Mastercard** — Synchrony Bank, last-4 9868
  - **PayPal Account** — PayPal wallet, no last-4 (identified by onojk123@gmail.com)
  Adding a new account: insert a row into the `account` table (or extend
  `migrate_add_accounts.py`) before importing statements for that account.

## Importing data

1. Drop statement PDFs into the browser at `/import/ocr`.
2. The router in `process_uploaded_statement_files` auto-detects the bank:
   - "bank of america" in first 2000 chars → `parse_boa_statement_text`
   - `*start*transaction detail` block → `_parse_chase_transaction_detail`
   - Otherwise falls through to the generic screenshot/OCR parser.
3. For a new bank, add a parser to `ocr_pipeline.py` and register it in the
   router before the Chase check.
4. **Reconciliation oracle**: after every import, verify that
   `sum(transaction amounts for the period) == ending_balance - beginning_balance`
   (to within 1 cent). The bank's own statement figures are ground truth.

### Reconciliation expectations

- **Chase / BoA**: closed ledgers — `sum(imported) == ending − beginning` to the
  cent. Any deviation is a parser bug.
- **Capital One**: closed ledgers — `sum(imported) == prev − new` to the
  cent. Parser imports purchases (negative), payments (positive), fees
  (negative), and a synthetic interest row (negative, dated to closing date).
  Any deviation is a parser bug.
- **Venmo**: NOT a closed ledger. The CSV omits internal Venmo adjustments
  (deferred settlements, card pre-auth, overdraft handling). Gaps of $20–$150
  per statement are expected:
  - Feb 2026: −$19, Mar 2026: −$94, Apr 2026: +$136 (after dedup)
  `parse_venmo_csv` logs the gap at import time. If a gap grows substantially
  larger or flips character, investigate — could be a new transaction type or
  parser regression. Do not attempt to "fix" the Venmo reconciliation by
  adjusting parser logic.
- **PayPal regular (wallet)**: NOT a closed ledger and not attempted. The wallet
  balance is $0 throughout (pass-through only). Phase A imports only:
  Mass Pay Payment rows (Tipalti income) and Non Reference Credit Payment rows
  (cashback). All other row types are intentionally skipped — they duplicate
  transactions already imported from the funding account (Chase x-9765) or the
  PayPal Cashback Mastercard. Jan–Mar 2026: 3 rows imported.

### Skip logs (import audit trails)

Two skip-log files capture transactions intentionally not imported because they
duplicate data already present from another account:

- `/tmp/venmo-skipped.log` — bank-funded Venmo merchant transactions (mirror of
  Chase debit entries). No per-file headers; entries are `date|type|amount|...`.
- `/tmp/paypal-regular-skipped.log` — all PayPal regular rows except Mass Pay and
  Non Reference Credit. Includes a `=== {filename} ===` header before each
  statement's entries so entries are traceable to their source PDF.

Both logs are **cleared at the start of each import run** and appended within the
session. The per-file header pattern (PayPal regular) is the standard going
forward; the Venmo log predates it and does not yet have headers.

**Future Phase B**: a matcher that reads these logs and links skipped rows to their
bank-side mirror transactions (for transfer reconciliation). Do not add this logic
until Phase A imports are complete and stable across all statement months.

## What NOT to do

- `archive/` holds old broken experiments (`ZEN_LION_*`, `*_BOMB.py`,
  `*_nuke_*`, broken backups). Do not use these as a reference, do not import
  from them, and do not resurrect their patterns. They were destructive
  in-place patch scripts.
- Don't add more "patch the codebase by writing strings to disk" scripts.
  If a change is needed, edit the source file directly.
- Don't commit `*.pdf`, `*.csv`, `*.db`, `config.py`, or `.env`.

## Roadmap (from README, prioritized)

Near-term: sort/filter UI, category autocomplete, delete-transaction button,
bulk-edit. Mid-term: budget envelopes, monthly alerts, CSV import wizard.
Long-term: multi-user, mobile responsive, cloud sync.

## Known parser quirks / data-integrity patterns

These bugs were identified and corrected 2026-04-27 during reconciliation of 25
statements (10 Chase × 2 accounts, 9 BoA). Full audit trail in
`scripts/correct_wrong_sign_rows_2026_04_27.py`.

### Bug class A — Sign-inference keyword mis-fire (Chase, FIXED)

`_parse_chase_transaction_detail` previously called `parse_signed_amount()` which
scores `DEBIT_HINT_WORDS` / `CREDIT_HINT_WORDS` against the description. The word
`"payment"` fired on "eBay Compduytyu6 **Payments**" and "Zelle **Payment** From"
(both credits); `"purchase"` fired on "Card **Purchase** Return" (also a credit).

**Fix (commit 7eedb19):** Chase parser now trusts the explicit sign in the amount
column (`-` prefix = debit, no prefix = credit) and skips keyword scoring entirely.
Regression tests in `tests/test_chase_sign_inference.py`.

This vulnerability likely exists in other parsers that call `parse_signed_amount()`
(BoA, PayPal CC, etc.). Watch for it when reconciliation gaps appear as credits
stored negative.

### Bug class B — Dedup collision on same-day identical transactions

The dedup key is `(date, amount, merchant, account_name)`. Two genuinely separate
transactions sharing all four fields are treated as one. Observed for:
- Same-day Venmo/Real-Time-Transfer credits from different senders (same truncated
  merchant name, different amounts are not a problem — it's same amounts that collide)
- Same-day identical Card Purchase Returns from the same retailer

When the first transaction was also stored with wrong sign (Bug A), the second
arrived with the same wrong sign and was silently dropped. After fixing the first
row's sign, the second is still missing. **Detection:** reconciliation gap equals
exactly the missing amount. **Fix:** manual `INSERT` after verifying in OCR text
that two distinct entries exist (confirmed via running balance).

### Bug class C — Year-inference error at year-boundary statements (multiple parsers)

Statements spanning two calendar years (e.g., Dec 2025 – Jan 2026) use dates
without an explicit year. Parsers that infer year from statement context can assign
the end-year (2026) to December transactions that belong in the start-year (2025).

**Confirmed in two parsers so far:**

1. **Chase PDF** (`_parse_chase_transaction_detail`): when the savings account section
   is parsed after the checking section has already seen January dates, the rollover
   logic applies 2026 to December savings rows.
   - *Data corrected 2026-04-27:* 7 Chase Savings rows (ids 1697–1703) re-dated from
     `2026-12-xx` to `2025-12-xx`.
   - *Parser fix deferred:* anchor December dates to the start-year when the
     statement end-month is January. Apply before importing future cross-year Chase
     statements.

2. **Capital One** (`parsers/capitalone_pdf_parser.py`): same root cause; a CapOne
   Platinum statement spanning Dec 2025 / Jan 2026 stored two December rows in 2026.
   - *Data corrected 2026-04-27:* 2 CapOne Platinum rows (ids 3164, 3165) re-dated
     from `2026-12-xx` to `2025-12-xx` — a payment (+$25.00) and a DoorDash charge
     (−$40.31).
   - *Parser fix deferred:* same fix needed in `capitalone_pdf_parser.py`.

**Watch for Bug C in any parser that handles year-boundary statements.** The pattern
is: unexpected `2026-12-xx` dates appearing in December for accounts whose most recent
statement spans Dec–Jan. Check after every cross-year import batch.

### Reconciliation state after 2026-04-27 corrections

All 25 initially imported statements close to $0.00:
- 10 Chase statements × 2 accounts (Checking + Savings): 20/20 OK
- 9 BoA statements: 9/9 OK
- Total transactions in DB at that point: 2795
- Additional imports since then: CareCredit, Citi, PayPal CC/wallet, CapOne —
  total now 3,399 (verified 2026-04-27). Date span: Mar 2025 – Apr 2026 (~13.8 months).

## Testing

213 tests across 11 files, all passing. Run with:

```bash
.venv/bin/pytest -v
```

Test files:
- `tests/test_smoke.py` — Flask routes and API contract
- `tests/test_transactions.py` — delete endpoint, transfer unlinking
- `tests/test_sign_inference.py` — debit/credit sign inference edge cases
- `tests/test_chase_sign_inference.py` — 8 Chase-specific sign tests (regression for Bug A above)
- `tests/test_ocr_pipeline.py` — Chase parser, merchant extraction, routing
- `tests/test_boa_parser.py` — BoA parser, explicit-negative amounts, routing
- `tests/test_venmo_parser.py` — Venmo CSV parser, skip logic, dedup, reconciliation
- `tests/test_capitalone_parser.py` — Capital One PDF parser, sign rules,
  merchant cleaning, interest synthesis, closed-ledger reconciliation
- `tests/test_carecredit_parser.py` — CareCredit PDF parser, parenthesized credits,
  zero cash-advance skip, closed-ledger reconciliation
- `tests/test_paypal_parser.py` — PayPal Cashback Mastercard parser, year inference,
  interest dedup, zero cash-advance skip
- `tests/test_paypal_regular_parser.py` — PayPal regular wallet parser, Phase A
  import policy, intra-file dedup, skip log entries

Synthetic fixtures live in `tests/fixtures/`. Tests use an in-memory SQLite
database via `DATABASE_URL` set in `tests/conftest.py` — no live DB is touched.
