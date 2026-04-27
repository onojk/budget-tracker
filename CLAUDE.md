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
models.py                 SQLAlchemy models: Transaction, CategoryRule, OcrRejectedLine
config.py.example         Config template — copy to config.py
ocr_pipeline.py           PDF + screenshot ingestion (pdfplumber + tesseract)
ocr_import_helpers.py     Shared helpers for OCR row imports
categorizer.py            Rule-based auto-categorization
direction_rules.py        Debit/credit direction inference
chase_amount_utils.py     Chase-specific amount parsing quirks
capitalone_validator.py   Capital One statement validation
parsers/                  Per-source parsers (currently: venmo)
templates/                Jinja2 templates
static/                   styles.css, dashboard.js, htmx.min.js
scripts/                  One-off CLI utilities (see below)
archive/                  Historical/broken files — DO NOT use as reference
```

## Scripts

`scripts/` contains CLI utilities for data lifecycle work. They expect to be
run from the project root with the venv active. Common ones:

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

There are currently no automated tests. Adding `pytest` + a `tests/` directory
is a reasonable first improvement.
