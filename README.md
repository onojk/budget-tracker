# Budget Tracker

A personal budgeting tool with real-time inline editing and a comprehensive
budget summary view, built on Flask + SQLite. Designed for thorough
multi-account reconciliation across banks, credit cards, and digital wallets.

## What it does

**1. Transaction ledger** — multi-account import and management with
click-to-edit cells, sortable columns, automatic deduplication, and transfer
reconciliation across accounts.

**2. Position dashboard** — real-time view of cash on hand, total CC debt,
debt thermometer breakdown by card, account freshness indicators, and a
per-account balance grid with sortable columns.

**3. Budget summary** — narrative-style financial position page with income
breakdown, structural gap analysis (waterfall charts), variable spending
detail, year-trend charts, and a slide-mode presentation view. Designed for
household financial conversations.

## Supported sources

Per-source parsers with closed-ledger reconciliation verification:

| Source | Format | Notes |
|---|---|---|
| Chase Checking / Savings | PDF | Year-boundary date inference handled |
| Bank of America Adv Plus | PDF | Explicit-negative amount parsing |
| Capital One Platinum / Quicksilver | PDF | Interest synthesis, sign rules |
| Citi Costco Anywhere Visa | PDF | |
| Synchrony CareCredit | PDF | Parenthesized credit parsing |
| Synchrony PayPal Cashback MC | PDF | Interest dedup, zero cash-advance skip |
| Venmo | CSV | Phase A: known reconciliation gaps documented |
| PayPal wallet | PDF | Phase A: Mass Pay + Non-Reference Credit only |

PDF statements parsed via `pdfplumber`. Screenshots via `tesseract` OCR.
CSV statements imported directly.

## Stack

- **Backend:** Flask, SQLAlchemy
- **Database:** SQLite (default), any SQLAlchemy-supported backend
- **Frontend:** Jinja2 templates, vanilla JS for inline editing, Chart.js
  for time-series, server-rendered SVG for static visualizations
- **OCR:** tesseract via pytesseract
- **Parsing:** pdfplumber, pandas

## Setup

```bash
git clone https://github.com/onojk/budget-tracker.git
cd budget-tracker

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp config.py.example config.py
cp .env.example .env
# edit .env — set a real SECRET_KEY

python app.py
# → http://127.0.0.1:5000
```

The SQLite database is created automatically on first run at
`instance/budget.db`.

### OCR support

For PDF and screenshot imports, install Tesseract at the system level:

```bash
# macOS
brew install tesseract

# Ubuntu / Debian
sudo apt-get install tesseract-ocr
```

## Running tests

```bash
.venv/bin/pytest -v
```

232 tests across 11 files, all passing. Tests use an in-memory SQLite
database via `DATABASE_URL` set in `tests/conftest.py` — no live DB is
touched.

| File | Coverage |
|---|---|
| `test_smoke.py` | Flask routes and API contract |
| `test_transactions.py` | Delete endpoint, transfer unlinking |
| `test_sign_inference.py` | Debit/credit direction inference |
| `test_chase_sign_inference.py` | Chase-specific sign regression tests |
| `test_ocr_pipeline.py` | Chase parser, merchant extraction, routing |
| `test_boa_parser.py` | BoA parser, explicit-negative amounts |
| `test_venmo_parser.py` | Venmo CSV, dedup, reconciliation |
| `test_capitalone_parser.py` | CapOne sign rules, interest synthesis |
| `test_carecredit_parser.py` | CareCredit parenthesized credits |
| `test_paypal_parser.py` | PayPal Cashback MC, year inference |
| `test_paypal_regular_parser.py` | PayPal wallet Phase A import policy |
| `test_budget_summary.py` | Budget summary route, charts, SGA section |

## Project layout

```
app.py                    Flask app, routes, API, inline-edit handler
models.py                 SQLAlchemy models (Account, Transaction,
                          CategoryRule, OcrRejectedLine)
config.py.example         Config template — copy to config.py
ocr_pipeline.py           PDF + screenshot ingestion; Chase and BoA
                          parsers with content-based routing
ocr_import_helpers.py     Shared helpers for OCR row imports
categorizer.py            Rule-based auto-categorization
direction_rules.py        Debit/credit direction inference
chase_amount_utils.py     Chase-specific amount parsing
capitalone_validator.py   Capital One statement validation
parsers/                  Per-source parsers:
  venmo_csv_parser.py
  capitalone_pdf_parser.py
templates/                Jinja2 templates
static/                   styles.css, dashboard.js, htmx.min.js
scripts/                  One-off CLI utilities (see below)
tests/                    pytest suite (232 tests)
archive/                  Historical experiments — do not use
```

## REST API

| Method | Path | Description |
|---|---|---|
| GET | `/api/transactions?limit=N` | Latest transactions |
| GET | `/api/summary` | Dashboard totals |
| PUT | `/api/transactions/<id>` | Update one field on a transaction |

## Data model conventions

- Debits stored as negative amounts; credits as positive.
- Dates before `2024-01-01` are rejected on import (`MIN_ALLOWED_DATE`).
- Every `Transaction` has an `account_id` FK to `Account`.
- Dedup key: `(date, amount, merchant, account_name)`.

## Scripts

`scripts/` contains CLI utilities for data lifecycle work. Run from the
project root with the venv active.

| Script | Purpose |
|---|---|
| `migrate_add_accounts.py` | One-time schema migration, idempotent |
| `import_credit_card_csv.py` | Import a credit-card CSV |
| `import_all_pdfs_to_db.py` | Bulk import PDF statements |
| `import_new_statements.py` | Targeted import with per-statement reconciliation |
| `dedupe_transactions.py` | Remove duplicate transactions |
| `reconcile_transfers.py` | Link mirrored transfers between accounts |
| `validate_statement_balances.py` | Check imported totals vs statement totals |
| `correct_wrong_sign_rows_2026_04_27.py` | Idempotent data correction (see header) |
| `hard_reset_budget_data.py` | Wipe all transactions — destructive |

Several scripts are destructive. Read before running.

## Reconciliation approach

After every import, verify:
```
sum(transaction amounts for the period) == ending_balance - beginning_balance
```
to within one cent. Chase, BoA, Capital One, and Synchrony parsers all
produce closed-ledger results. Venmo and PayPal have documented expected
gaps (see `CLAUDE.md`).

## License

MIT — see `LICENSE`.
