📘 Budget App – OCR-Driven Personal Finance Manager

A fully local, privacy-first personal finance dashboard that automatically imports, OCR-parses, categorizes, and visualizes transactions across all major financial accounts, including:

Chase Credit Cards

Capital One (Platinum, Quicksilver)

CareCredit / Synchrony

PayPal Credit

PayPal Main Account Statements

Citi Costco

Venmo CSV Monthly Summaries

Generic PDF credit card statements

The app provides:

A web UI (Flask) with charts, tables, and reports

An advanced, multi-pass OCR engine for PDFs/images

Full bank-specific parsers (Chase, Capital One, PayPal Credit)

Duplicate detection via SHA-256 checksums

Bulk import tools including a Unified Super-Importer

A local SQLite database of structured transactions

All processing is done locally, ensuring your financial data never leaves your machine.

🚀 Features
✔ Automatic PDF Import & OCR

Place statements into uploads/statements/ or use the super importer, and the app:

Computes a checksum to avoid duplicates

OCRs PDFs → text (*_ocr.txt)

Parses the text using specialized parsers

Inserts clean, structured financial transactions

✔ Bank-Specific Enhanced Parsers

Each institution has a dedicated extraction pipeline:

Chase — Full "Transaction Detail" block parser

Capital One — Full debit/credit detection + interest parsing

PayPal Credit — Synchrony-format parser

Generic OCR Parser — Fallback for unknown bank layouts

✔ Interactive Web Dashboard

View totals, spending trends, monthly breakdowns

Filter by category, date, or source account

Search merchants

Export to CSV

✔ Unified Import Tools

The project includes multiple import utilities:

Script	Purpose
import_all_ocr_to_db.py	Import all *_ocr.txt files
import_all_pdfs_to_db.py	Import all PDFs already inside uploads/statements/
import_everything_from_downloads.py	🔥 Full multi-account import from ~/Downloads/accounts (CareCredit, Chase, CapitalOne, PayPal, Citi, etc.)
📂 Project Structure
budget_app/
│
├── app.py                     # Flask webserver + routes
├── ocr_pipeline.py            # OCR engine + parsers
├── models.py                  # Transaction model
├── import_all_ocr_to_db.py    # Bulk import from OCR text
├── import_all_pdfs_to_db.py   # Bulk import from PDFs
├── import_everything_from_downloads.py  # SUPER IMPORTER
│
├── static/
│   ├── styles.css
│   ├── dashboard.js
│   └── ...
│
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── transactions.html
│   └── reports.html
│
└── uploads/
    └── statements/
        ├── *_ocr.txt
        ├── *.pdf
        └── checksum index files

🔧 Installation
1. Clone the repo
git clone https://github.com/onojk/budget_app.git
cd budget_app

2. Create & activate the virtualenv
python3 -m venv budget-env
source budget-env/bin/activate
pip install -r requirements.txt

3. Start the server
python app.py


Open browser → http://127.0.0.1:5000

🖨 OCR Pipeline

The OCR pipeline performs multiple passes to ensure accuracy:

PDF → image → text extraction (3-pass consistency)

Cleans text, normalizes spacing, removes OCR artifacts

Parses into structured rows:

{
  "Date": "2025-06-18",
  "Amount": -42.17,
  "Merchant": "ALBERTSONS #0733",
  "Category": "",
  "Source": "Chase",
  "Notes": "from 2025-06-18.pdf"
}

📥 Importing Transactions
Option A — Import OCR text files
python import_all_ocr_to_db.py

Option B — Import PDFs already in uploads/statements
python import_all_pdfs_to_db.py

Option C — SUPER IMPORTER (Recommended)

Automatically imports EVERYTHING under ~/Downloads/accounts, including:

carecredit/*

capitalone/*

chase_9383/*

chase_9765/*

citi_costco/*

paypal_CC/*

paypal_general/*

venmo/*.csv

Run:

python import_everything_from_downloads.py


This will:

Recursively find all PDFs

Copy them into a temp folder

Run the full OCR → parse → insert pipeline

Handle duplicates

Clean up

Perfect for monthly imports or rebuilding from scratch.

🧹 Resetting the Database

If you want to start clean:

python - << 'PY'
from app import app, db, Transaction
with app.app_context():
    db.session.query(Transaction).delete()
    db.session.commit()
PY

📊 Dashboard Features

Top categories

Spending over time

Income vs expenses

Merchant heatmap

Transaction search

Monthly reports

🧪 Development Notes
Debugging OCR

To inspect extracted rows:

python - << 'PY'
from ocr_pipeline import process_statement_files
rows = process_statement_files(["uploads/statements/2025-06-18_ocr.txt"])
print(rows)
PY

Debugging Database
python - << 'PY'
from app import app, db, Transaction
with app.app_context():
    for tx in Transaction.query.limit(10):
        print(tx)
PY

🚀 Roadmap

✨ Automatic category assignment (ML or rule-based)

✨ Merchant normalization (Amazon → "Amazon", DoorDash → "DoorDash")

✨ Monthly budgeting tools

✨ Import UI so no terminal commands are needed

✨ CareCredit-specialized parser

✨ Venmo CSV importer with full transaction merging

💬 Support / Contact

Created by Jonathan Kendall
GitHub: https://github.com/onojk

All processing is offline, local, and private.
