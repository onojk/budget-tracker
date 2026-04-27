# 🧮 Budget Tracker

A clean, fast, open-source personal budgeting tool with real-time inline
editing, built on Flask + SQLite.

## Features

- **Transactions** — add, view, sort, categorize, with click-to-edit cells in
  the transactions table (saves via `PUT /api/transactions/<id>`, no reload)
- **Dashboard** — income, expenses, net totals; daily / weekly / monthly views
- **Imports**
  - CSV (banks, credit cards, Venmo)
  - PDF statements via `pdfplumber`
  - Screenshots / images via `tesseract` OCR
- **Auto-categorization** — rule-based, learns from past assignments
- **Transfer reconciliation** — links mirrored debit/credit pairs across accounts

## Stack

- **Backend:** Flask, SQLAlchemy, WTForms
- **Database:** SQLite (default), any SQLAlchemy-supported DB
- **Frontend:** Jinja2 + vanilla JS + htmx
- **Imports:** pandas, pdfplumber, pytesseract

## Setup

```bash
git clone https://github.com/onojk/budget-tracker.git
cd budget-tracker

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp config.py.example config.py
cp .env.example .env
# edit .env to set a real SECRET_KEY

python app.py
# → http://127.0.0.1:5000
```

The SQLite database is created automatically on first run.

### Optional: OCR support

For PDF statements and screenshot imports, install Tesseract at the system
level:

```bash
# macOS
brew install tesseract

# Ubuntu / Debian
sudo apt-get install tesseract-ocr
```

## Project structure

```
.
├── app.py                  # Flask app, routes, API
├── models.py               # SQLAlchemy models
├── config.py.example       # → copy to config.py
├── ocr_pipeline.py         # PDF + screenshot ingestion
├── categorizer.py          # auto-categorization rules
├── direction_rules.py      # debit/credit direction inference
├── chase_amount_utils.py   # Chase-specific parsing
├── capitalone_validator.py # Capital One validation
├── parsers/                # per-source parsers (Venmo, …)
├── templates/              # Jinja2 templates
├── static/                 # CSS, JS, htmx
├── scripts/                # one-off CLI utilities (imports, dedup, etc.)
└── archive/                # historical experiments (gitignored)
```

## REST API

| Method | Path                       | Description                       |
| ------ | -------------------------- | --------------------------------- |
| GET    | `/api/transactions?limit=N`| Latest transactions               |
| GET    | `/api/summary`             | Totals for dashboard panels       |
| PUT    | `/api/transactions/<id>`   | Update one or more fields         |

Example inline-edit request:

```json
{
  "amount": 42.19,
  "category": "Dining",
  "notes": "Corrected value"
}
```

## Data model

`Transaction` fields: `id`, `date`, `merchant`, `description`, `amount`,
`category`, `notes`, `source_system`, `account_name`, `direction`,
`is_transfer`, `linked_transaction_id`, `file_checksum`, `source_filename`.

Conventions:

- Dates before `2024-01-01` are rejected on import.
- Debits are stored as negative amounts; credits as positive.

## Scripts

`scripts/` contains CLI utilities you'll occasionally need:

- `import_credit_card_csv.py` — import a credit-card CSV
- `import_all_pdfs_to_db.py` — bulk import PDF statements
- `import_screenshots_now.py` — bulk import OCR'd screenshots
- `dedupe_transactions.py` — remove duplicate transactions
- `reconcile_transfers.py` — link mirrored transfers
- `validate_statement_balances.py` — sanity-check imported totals
- `hard_reset_budget_data.py` — wipe all transactions ⚠️ destructive

Several scripts are destructive. Read them before running.

## Roadmap

**Near-term:** sort/filter UI, category autocomplete, delete-transaction
button, bulk-edit selections.

**Mid-term:** budget envelopes, monthly alerts, CSV import mapping wizard.

**Long-term:** multi-user, mobile-responsive UI, cloud sync + backup.

## Contributing

PRs welcome. Fork, branch, run locally, submit. There aren't tests yet —
adding `pytest` would be a great first contribution.

## License

MIT — © 2025 the author. See `LICENSE`.
