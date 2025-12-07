🧮 Budget Tracker

A clean, fast, open-source personal budgeting tool with real-time inline editing

✨ Overview

Budget Tracker is a lightweight Flask-based personal finance manager that helps you:

Track income and spending

Categorize expenses

Monitor trends via the dashboard

Audit spending habits

Export & review transactions

Edit transactions inline, live, with no page reloads

The project focuses on speed, simplicity, and accuracy — ideal for personal use or as a starting point for a more full-featured budgeting system.

🚀 Features
🧾 Transaction Management

Add income & expenses manually or via import

Auto-sort and format transactions

Click any cell to edit instantly (inline editing)

Live updates using a JSON PUT API

Edits persist immediately to the database

📊 Dashboard

Summary cards for spending, income, deltas

Trend charts

Category totals

Daily, weekly, and monthly views

API-ready backend (/api/summary, /api/transactions)

⚙️ Technical Stack

Backend: Flask, SQLAlchemy

Database: SQLite (default), easily swappable

Frontend: HTML, Jinja, JS

Live Editing: Pure Vanilla JS (no frameworks) + REST API

Zero bloated dependencies

📦 Installation & Setup
1 — Clone the repository
git clone https://github.com/onojk/budget-tracker.git
cd budget-tracker

2 — Create a virtual environment
python3 -m venv budget-env
source budget-env/bin/activate

3 — Install dependencies

If the project includes requirements.txt:

pip install -r requirements.txt


Otherwise:

pip install flask sqlalchemy

4 — Initialize the database (first run only)
python init_db.py  # if available


(If you don't have init_db yet, the app will create tables automatically.)

5 — Run the app
python app.py


Visit:

http://127.0.0.1:5000

⚡ Inline Editing (New!)

The Transactions page now supports true real-time cell editing, powered by:

A frontend JS handler that listens to clicks on .editable table cells

A dynamic <input> that appears when you click

A PUT /api/transactions/<id> JSON update

Automatic re-render of the updated value

Editable fields:

Date

Merchant

Description

Amount (validated as float)

Category

Notes

Example JSON update:
PUT /api/transactions/42
{
  "amount": 19.99,
  "category": "Groceries",
  "notes": "Fixed amount"
}


The server responds:

{
  "status": "ok",
  "transaction": {
    "id": 42,
    "date": "2025-01-08",
    "merchant": "FOOD4LESS",
    "amount": 19.99,
    "category": "Groceries",
    "notes": ""
  }
}

📁 Project Structure
budget_tracker/
│
├── app.py                    # Main Flask app + routes + inline-edit API
├── models.py                 # SQLAlchemy models
├── templates/
│   ├── base.html             # Global layout
│   ├── dashboard.html        # Dashboard UI
│   ├── transactions.html     # Inline editing UI
│   └── partials/             # (optional reusable components)
├── static/
│   ├── styles.css            # Styles
│   └── dashboard.js          # Dashboard API fetchers
│
├── budget-env/               # Virtual environment
└── README.md                 # You're here 🌟

🔌 API Endpoints
GET /api/transactions?limit=300

Returns latest transactions (dashboard uses this).

GET /api/summary

Returns summary totals (income, spending, category breakdowns).

PUT /api/transactions/<id>

Updates a single transaction in real time.

🔒 Data Model Summary

Transaction includes:

id

date

merchant

description

amount

category

notes

import_source

checksum

created_at

Ideal for syncing with imports, OCR, or API-driven feeds.

🧭 Roadmap
Near-term

Category autocomplete

Bulk editing

Delete transaction button

Import assistants (CSV, OCR)

Dashboard enhancements (filters, drilldown)

Mid-term

Multi-account support

Rules engine (auto-classify merchants)

Budget envelopes

Monthly goals & alerts

Long-term

Mobile-friendly responsive UI

Multi-user support

Cloud sync

Export to Excel / Google Sheets

🤝 Contributing

Pull requests welcome! Before contributing:

Create a branch

Ensure project runs:

python -m py_compile app.py
python app.py


Add tests where reasonable

Submit PR on GitHub

📝 License

This project is open-source. If you'd like, I can generate:

MIT License

Apache 2.0

GPLv3

Creative Commons

Just tell me which license you prefer.

⭐ Acknowledgments

Created and maintained by ONOJK123 / TEST USER, blending:

personal finance discipline

clean software engineering

realtime UX polish
