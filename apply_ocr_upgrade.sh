#!/usr/bin/env bash
set -e

cd ~/budget_app

echo "📦 Backing up existing files to backups_ocr_upgrade/..."
mkdir -p backups_ocr_upgrade

ts=$(date +%Y%m%d-%H%M%S)

[ -f app.py ] && cp app.py backups_ocr_upgrade/app.py.$ts
[ -f ocr_pipeline.py ] && cp ocr_pipeline.py backups_ocr_upgrade/ocr_pipeline.py.$ts
[ -f templates/import_ocr.html ] && cp templates/import_ocr.html backups_ocr_upgrade/import_ocr.html.$ts

echo "📝 Writing new app.py..."

cat << 'EOF' > app.py
#!/usr/bin/env python3
import os
from datetime import date

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash
)
from werkzeug.utils import secure_filename

from config import Config
from models import db, Transaction
from ocr_pipeline import process_ocr_files

from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, DateField, SelectField, TextAreaField
from wtforms.validators import DataRequired

from sqlalchemy import func
import pandas as pd
from dateutil import parser as dateparser

# ----------------- Flask setup -----------------

app = Flask(__name__)
app.config.from_object(Config)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Ensure we always have an upload folder
if "UPLOAD_FOLDER" not in app.config:
    app.config["UPLOAD_FOLDER"] = os.path.join(BASE_DIR, "uploads")

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)

with app.app_context():
    db.create_all()

# ----------------- Forms -----------------


class ManualTransactionForm(FlaskForm):
    date = DateField("Date", validators=[DataRequired()], default=date.today)
    amount = FloatField("Amount", validators=[DataRequired()])
    merchant = StringField("Merchant")
    description = StringField("Description")
    source_system = StringField("Source System", default="Manual")
    account_name = StringField("Account")
    category = StringField("Category")
    direction = SelectField("Direction", choices=[("debit", "Debit"), ("credit", "Credit")])
    notes = TextAreaField("Notes")


# ----------------- Helpers -----------------


def clean_text(val):
    if val is None:
        return ""
    return str(val).strip()


def parse_amount(val):
    if val is None or str(val).strip() == "":
        return 0.0
    s = str(val).replace(",", "").replace("$", "").strip()
    return float(s)


def parse_date(value):
    """Parse many date formats. Return date or None if invalid/placeholder."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or "XX" in s:
        # for things like 2025-11-XX
        return None
    try:
        # flexible parse (handles 2025-11-03, 11/03/2025, etc.)
        dt = dateparser.parse(s, dayfirst=False, yearfirst=True)
        return dt.date()
    except Exception:
        return None


def build_transaction_from_row(row, invalid_dates=None):
    """Convert a row dict (CSV or OCR) into a Transaction object."""
    raw_date = row.get("Date")
    dt = parse_date(raw_date)
    if dt is None:
        if invalid_dates is not None:
            invalid_dates.add(str(raw_date))
        raise ValueError(f"Invalid date: {raw_date}")

    direction = (row.get("Direction") or "debit").lower()
    amount_val = parse_amount(row.get("Amount"))

    # Normalize sign: debits negative, credits positive
    if direction == "debit" and amount_val > 0:
        amount_val = -amount_val
    if direction == "credit" and amount_val < 0:
        amount_val = -amount_val

    source_system = (
        row.get("Source")
        or row.get("source_system")
        or clean_text(row.get("Source System"))
    )
    account_name = row.get("Account") or row.get("account_name") or ""

    merchant = clean_text(row.get("Merchant"))
    description = clean_text(
        row.get("Description") or row.get("description") or merchant
    )
    category = clean_text(row.get("Category"))
    notes = clean_text(row.get("Notes") or row.get("notes"))

    tx = Transaction(
        date=dt,
        source_system=source_system,
        account_name=account_name,
        direction=direction,
        amount=amount_val,
        merchant=merchant,
        description=description,
        category=category,
        notes=notes,
    )
    return tx


# ----------------- Routes -----------------


@app.route("/")
def dashboard():
    # Totals
    total_spending = (
        db.session.query(func.sum(Transaction.amount))
        .filter(Transaction.amount < 0)
        .scalar()
        or 0.0
    )
    total_income = (
        db.session.query(func.sum(Transaction.amount))
        .filter(Transaction.amount > 0)
        .scalar()
        or 0.0
    )
    net = total_income + total_spending

    # Spending by category (absolute values for chart)
    cat_rows = (
        db.session.query(
            Transaction.category,
            func.sum(Transaction.amount),
        )
        .group_by(Transaction.category)
        .all()
    )

    cat_labels = []
    cat_values = []
    for cat, amt in cat_rows:
        label = cat or "Uncategorized"
        cat_labels.append(label)
        cat_values.append(abs(float(amt or 0.0)))

    # Daily running net
    daily_rows = (
        db.session.query(
            Transaction.date,
            func.sum(Transaction.amount),
        )
        .group_by(Transaction.date)
        .order_by(Transaction.date)
        .all()
    )

    running = 0.0
    daily_net = []
    for d, amt in daily_rows:
        running += float(amt or 0.0)
        daily_net.append({"date": d.isoformat(), "net": round(running, 2)})

    return render_template(
        "dashboard.html",
        net=round(net, 2),
        total_spending=round(total_spending, 2),
        total_income=round(total_income, 2),
        cat_labels=cat_labels,
        cat_values=cat_values,
        daily_net=daily_net,
    )


@app.route("/transactions")
def transactions():
    txs = (
        Transaction.query.order_by(
            Transaction.date.desc(), Transaction.id.desc()
        ).all()
    )
    return render_template("transactions.html", transactions=txs)


@app.route("/import/csv", methods=["GET", "POST"])
def import_csv():
    if request.method == "GET":
        return render_template("import_csv.html")

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("import_csv"))

    upload_folder = app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)
    tmp_path = os.path.join(upload_folder, "import.csv")
    file.save(tmp_path)

    try:
        df = pd.read_csv(tmp_path)
    except Exception as e:
        flash(f"Failed to read CSV: {e}", "danger")
        return redirect(url_for("import_csv"))

    records = df.to_dict(orient="records")
    invalid_dates = set()
    imported = 0
    skipped = 0

    for row in records:
        try:
            tx = build_transaction_from_row(row, invalid_dates)
            db.session.add(tx)
            imported += 1
        except ValueError:
            skipped += 1
        except Exception:
            skipped += 1

    db.session.commit()

    msg = f"Imported {imported} rows from CSV."
    if skipped > 0:
        example = next(iter(invalid_dates)) if invalid_dates else "see log"
        msg += f" Skipped {skipped} rows with invalid dates (e.g. '{example}')."

    flash(msg, "success")
    return redirect(url_for("transactions"))


@app.route("/import/ocr", methods=["GET", "POST"])
def import_ocr():
    if request.method == "GET":
        return render_template("import_ocr.html")

    files = request.files.getlist("files")
    if not files or files == [None]:
        flash("No files selected.", "danger")
        return redirect(url_for("import_ocr"))

    upload_folder = app.config["UPLOAD_FOLDER"]
    ocr_folder = os.path.join(upload_folder, "ocr")
    os.makedirs(ocr_folder, exist_ok=True)

    saved_paths = []
    for f in files:
        if not f or f.filename == "":
            continue
        safe_name = secure_filename(f.filename)
        path = os.path.join(ocr_folder, safe_name)
        f.save(path)
        saved_paths.append(path)

    if not saved_paths:
        flash("No valid files uploaded.", "danger")
        return redirect(url_for("import_ocr"))

    # OCR -> row dicts (Date, Source, Account, Direction, Amount, Merchant, Description, Category, Notes)
    try:
        rows = process_ocr_files(saved_paths)
    except Exception as e:
        flash(f"OCR processing failed: {e}", "danger")
        return redirect(url_for("import_ocr"))

    imported = 0
    skipped = 0
    invalid_dates = set()

    for row in rows:
        try:
            tx = build_transaction_from_row(row, invalid_dates)
            db.session.add(tx)
            imported += 1
        except ValueError:
            skipped += 1
        except Exception:
            skipped += 1

    db.session.commit()

    msg = f"OCR Import completed. Imported {imported} rows."
    if skipped > 0:
        example = next(iter(invalid_dates)) if invalid_dates else "see log"
        msg += f" Skipped {skipped} rows with invalid dates (e.g. '{example}')."

    flash(msg, "success")
    return redirect(url_for("transactions"))


@app.route("/add/manual", methods=["GET", "POST"])
def add_manual():
    form = ManualTransactionForm()
    if form.validate_on_submit():
        dt = form.date.data
        amount_val = form.amount.data or 0.0
        direction = form.direction.data

        if direction == "debit" and amount_val > 0:
            amount_val = -amount_val
        if direction == "credit" and amount_val < 0:
            amount_val = -amount_val

        tx = Transaction(
            date=dt,
            source_system=form.source_system.data or "Manual",
            account_name=form.account_name.data or "",
            direction=direction,
            amount=amount_val,
            merchant=form.merchant.data or "",
            description=form.description.data or "",
            category=form.category.data or "",
            notes=form.notes.data or "",
        )
        db.session.add(tx)
        db.session.commit()
        flash("Manual transaction added.", "success")
        return redirect(url_for("transactions"))

    return render_template("add_manual.html", form=form)


if __name__ == "__main__":
    app.run(debug=True)
EOF

echo "📝 Writing new ocr_pipeline.py..."

cat << 'EOF' > ocr_pipeline.py
import re
from typing import List, Dict

import pytesseract
from pdf2image import convert_from_path
from PIL import Image


def extract_text_from_image(path: str) -> str:
    img = Image.open(path)
    text = pytesseract.image_to_string(img)
    return text


def extract_text_from_pdf(path: str) -> str:
    pages = convert_from_path(path)
    text_chunks = []
    for pg in pages:
        text_chunks.append(pytesseract.image_to_string(pg))
    return "\n".join(text_chunks)


def extract_transactions(text: str) -> List[Dict]:
    """
    Very simple starter parser.

    Tries to catch lines like:
      11/25 WALMART -59.97
      2025-11-03 Starbucks 6.45
    """

    rows: List[Dict] = []
    for line in text.splitlines():
        ln = line.strip()
        if not ln:
            continue

        # date + amount pattern (you can enhance this later per bank)
        m = re.search(
            r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}).*?(?P<amount>-?\$?\d+\.\d{2})",
            ln,
        )
        if not m:
            continue

        dt = m.group("date")
        amt = m.group("amount").replace("$", "")
        merchant = ln  # crude default; refine later with custom templates

        direction = "debit"
        try:
            if float(amt) > 0:
                direction = "credit"
        except ValueError:
            pass

        row = {
            "Date": dt,
            "Source": "OCR",
            "Account": "OCR",
            "Direction": direction,
            "Amount": amt,
            "Merchant": merchant,
            "Description": merchant,
            "Category": "",
            "Notes": "Extracted via OCR",
        }
        rows.append(row)

    return rows


def process_ocr_files(files: List[str]) -> List[Dict]:
    all_rows: List[Dict] = []
    for path in files:
        path_lower = path.lower()
        if path_lower.endswith(".pdf"):
            text = extract_text_from_pdf(path)
        else:
            text = extract_text_from_image(path)

        tx_rows = extract_transactions(text)
        all_rows.extend(tx_rows)

    return all_rows
EOF

echo "📝 Writing templates/import_ocr.html..."

mkdir -p templates

cat << 'EOF' > templates/import_ocr.html
{% extends "base.html" %}
{% block content %}
<div class="container mt-4">
    <h2>Import via OCR</h2>

    <form method="post" enctype="multipart/form-data">
        <div class="mb-3">
            <label for="files" class="form-label">Upload Screenshots or PDF Statements</label>
            <input class="form-control" type="file" name="files" id="files" multiple>
        </div>

        <div class="alert alert-info">
            Supported: PNG, JPG, JPEG, PDF<br>
            You may upload multiple files at once.
        </div>

        <button type="submit" class="btn btn-primary">Process via OCR</button>
    </form>
</div>
{% endblock %}
EOF

echo "✅ OCR upgrade applied. Now restart the app with:"
echo "   cd ~/budget_app"
echo "   source budget-env/bin/activate"
echo "   python app.py"
EOF
