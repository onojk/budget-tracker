🧮 Budget Tracker

A clean, fast, open-source personal budgeting tool with real-time inline editing.

✨ Overview

Budget Tracker is a lightweight, developer-friendly Flask app that helps you manage your personal finances with clarity and speed. It includes a dashboard for quick insights, a transaction manager, import support, and now true inline editing right inside the transactions table — no page reload required.

This project prioritizes:

⏱ Speed

🧽 Simplicity

📊 Transparency

🔧 A codebase that’s easy to modify or extend

🚀 Features
🧾 Transaction Management

Add, view, sort, and categorize transactions

Import workflow (CSV / OCR integrations optional)

Real-time inline editing (click a cell, edit instantly)

Amount validation

Auto-update JSON API

Category + notes editing

📊 Dashboard

Income, expenses, and net totals

Trendline-ready API endpoints

Daily / weekly / monthly insights

Clean UI using standard HTML + JS

⚙️ Technical Stack

Backend: Flask + SQLAlchemy

Database: SQLite by default

Frontend: HTML, CSS, Jinja, vanilla JavaScript

Live Editing: Custom inline editor + REST API (PUT /api/transactions/<id>)

📦 Installation
1 — Clone the repository
git clone https://github.com/onojk/budget-tracker.git
cd budget-tracker

2 — Create a virtual environment
python3 -m venv budget-env
source budget-env/bin/activate

3 — Install dependencies
pip install -r requirements.txt


Or manually:

pip install flask sqlalchemy

4 — Run the application
python app.py


Go to:

http://127.0.0.1:5000/

⚡ Inline Editing (New)

The Transactions page supports true inline editing via a JSON API.
Click any cell in the table to edit:

Date

Merchant

Description

Amount

Category

Notes

Press Enter or click away to save.
The backend updates instantly through:

PUT /api/transactions/<id>


Example request sent via JS:

{
  "amount": 42.19,
  "category": "Dining",
  "notes": "Corrected value"
}

📁 Project Structure
budget_tracker/
│
├── app.py                      # Flask app, routes, API, inline edit handler
├── models.py                   # SQLAlchemy ORM models
│
├── templates/
│   ├── base.html               # global layout
│   ├── dashboard.html          # summary + charts
│   ├── transactions.html       # inline editing UI
│   └── partials/               # optional components
│
├── static/
│   ├── styles.css              # site styling
│   └── dashboard.js            # AJAX dashboard fetchers
│
├── budget-env/                 # virtual environment
└── README.md

🔌 REST API
GET /api/transactions?limit=N

Returns latest transactions.

GET /api/summary

Returns totals for dashboard panels.

PUT /api/transactions/<id>

Updates a single transaction field.
Returns updated transaction object.

🧠 Data Model

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

SQLite auto-creates the schema at first run.

🧭 Roadmap
Near-term

Sorting + Filtering UI

Category autocomplete

Delete transaction button

Bulk-edit selections

Mid-term

Budget envelopes

Monthly alerts

CSV importer with mapping wizard

Long-term

Multi-user support

Responsive mobile UI

Cloud sync + backup

🤝 Contributing

Pull requests welcome!

To contribute:

Fork the repository

Create a feature branch

Run tests + local build

Submit a PR

📜 License — MIT
MIT License

Copyright (c) 2025 TEST USER

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

⭐ Acknowledgments

Developed by ONOJK123 (TEST USER) — combining clean engineering, data accuracy, and UX polish into a compact personal finance tool.
