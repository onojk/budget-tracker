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
- `dedupe_transactions.py` — remove duplicate transactions
- `reconcile_transfers.py` — link mirrored transfers between accounts
- `validate_statement_balances.py` — check imported totals vs statement totals
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

## Testing

60 tests across 7 files, all passing. Run with:

```bash
.venv/bin/pytest -v
```

Test files:
- `tests/test_smoke.py` — Flask routes and API contract
- `tests/test_transactions.py` — delete endpoint, transfer unlinking
- `tests/test_sign_inference.py` — debit/credit sign inference edge cases
- `tests/test_ocr_pipeline.py` — Chase parser, merchant extraction, routing
- `tests/test_boa_parser.py` — BoA parser, explicit-negative amounts, routing
- `tests/test_venmo_parser.py` — Venmo CSV parser, skip logic, dedup, reconciliation
- `tests/test_capitalone_parser.py` — Capital One PDF parser, sign rules,
  merchant cleaning, interest synthesis, closed-ledger reconciliation

Synthetic fixtures live in `tests/fixtures/`. Tests use an in-memory SQLite
database via `DATABASE_URL` set in `tests/conftest.py` — no live DB is touched.
