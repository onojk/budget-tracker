import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, date

START_DATE = date(2024, 1, 1)
from collections import OrderedDict

from ocr_pipeline import process_screenshot_files, process_statement_files, process_uploaded_statement_files
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, func, or_

MIN_ALLOWED_DATE = date(2024, 1, 1)

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from markupsafe import Markup
from werkzeug.utils import secure_filename
from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    FloatField,
    DateField,
    SelectField,
    TextAreaField,
)
from wtforms.validators import DataRequired
import pandas as pd

from config import Config
from models import db, Transaction, CategoryRule, OcrRejectedLine

# Backwards-compat: other modules use `from app import OcrRejected`
OcrRejected = OcrRejectedLine


# Import the module only; we'll check for functions with hasattr
import ocr_pipeline


# -------------------------------------------------------------------
# Forms
# -------------------------------------------------------------------


class ManualTransactionForm(FlaskForm):
    date = DateField(
        "Date",
        validators=[DataRequired()],
        default=date.today,
        render_kw={"min": "2024-01-01"},
    )
    amount = FloatField("Amount", validators=[DataRequired()])
    merchant = StringField("Merchant")
    description = StringField("Description")
    source_system = StringField("Source System", default="Manual")
    account_name = StringField("Account")
    category = StringField("Category")
    direction = SelectField(
        "Direction",
        choices=[("debit", "Debit (money out)"), ("credit", "Credit (money in)")],
        default="debit",
    )
    notes = TextAreaField("Notes")


# -------------------------------------------------------------------
# App setup
# -------------------------------------------------------------------


app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

with app.app_context():
    db.create_all()


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def normalize_string(value) -> str:
    """Handle None / NaN / floats from pandas safely and return a clean string."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def parse_date_safe(raw):
    """
    Parse YYYY-MM-DD dates; reject anything before 2024-01-01.
    Returns (date_obj, reason) where date_obj may be None.
    """
    s = normalize_string(raw)
    if not s:
        return None, "empty"
    if "XX" in s:
        return None, "contains XX"
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        if d < MIN_ALLOWED_DATE:
            return None, "before-min-date"
        return d, None
    except ValueError as e:
        return None, str(e)

def parse_date_safer(raw):
    """Backward-compatible alias – just call parse_date_safe()."""
    return parse_date_safe(raw)




def coerce_amount(raw_amount, direction: str) -> float:
    """
    Ensure debits are negative, credits positive.
    CSV may already include sign; we normalize.
    """
    if raw_amount is None or (isinstance(raw_amount, float) and pd.isna(raw_amount)):
        amt = 0.0
    else:
        try:
            amt = float(raw_amount)
        except Exception:
            amt = 0.0

    d = (direction or "").strip().lower()
    if d == "debit" and amt > 0:
        amt = -amt
    if d == "credit" and amt < 0:
        amt = -amt
    return amt


def is_duplicate_transaction(session, date, amount, merchant, account_name, *_, **__):
    """
    Return True if a transaction with the same date, amount, merchant,
    and account already exists. Extra args are accepted and ignored so we can
    call this helper with (date, amount, merchant, description, account, source).
    """
    q = session.query(Transaction).filter(
        Transaction.date == date,
        Transaction.amount == amount,
        Transaction.merchant == merchant,
        Transaction.account_name == account_name,
    )
    return session.query(q.exists()).scalar()


# ======================================================================
# Category Rule Engine
# ======================================================================


def guess_category(db, merchant, account_name, method):
    """
    Try to guess the category based on prior user selections.
    Looks for exact matches first, then partial matches.
    """
    merchant = (merchant or "").strip()
    account_name = (account_name or "").strip()
    method = (method or "").strip()

    # 1) Exact merchant/account/method match
    rule = (
        db.session.query(CategoryRule)
        .filter(CategoryRule.merchant == merchant)
        .filter(CategoryRule.account_name == account_name)
        .filter(CategoryRule.method == method)
        .first()
    )
    if rule:
        rule.use_count += 1
        db.session.commit()
        return rule.category

    # 2) Match merchant only
    rule = (
        db.session.query(CategoryRule)
        .filter(CategoryRule.merchant == merchant)
        .first()
    )
    if rule:
        rule.use_count += 1
        db.session.commit()
        return rule.category

    return None


def learn_category_from_transaction(db, merchant, account_name, method, chosen_category):
    """
    Save or update a CategoryRule when the user changes the category manually.
    (Not yet wired into any route, but ready for future use.)
    """
    merchant = (merchant or "").strip()
    account_name = (account_name or "").strip()
    method = (method or "").strip()

    rule = (
        db.session.query(CategoryRule)
        .filter(CategoryRule.merchant == merchant)
        .filter(CategoryRule.account_name == account_name)
        .filter(CategoryRule.method == method)
        .first()
    )

    if rule:
        rule.category = chosen_category
        rule.use_count += 1
    else:
        rule = CategoryRule(
            merchant=merchant,
            account_name=account_name,
            method=method,
            category=chosen_category,
            use_count=1,
        )
        db.session.add(rule)

    db.session.commit()


# -------------------------------------------------------------------
# Routes

def build_monthly_summary(txs):
    """Return list of {year, month, label, income, spending, net}."""
    monthly_map = OrderedDict()

    for tx in txs:
        if not tx.date:
            continue

        key = tx.date.strftime("%Y-%m")

        bucket = monthly_map.setdefault(
            key,
            {
                "year": tx.date.year,
                "month": tx.date.month,
                "label": tx.date.strftime("%b %Y"),
                "income": 0.0,
                "spending": 0.0,
                "net": 0.0,
            },
        )

        amt = float(tx.amount or 0)
        if amt >= 0:
            bucket["income"] += amt
        else:
            bucket["spending"] += amt

        bucket["net"] += amt

    return list(monthly_map.values())

# -------------------------------------------------------------------


@app.route("/capone_csv_summary")


def get_capone_csv_summary():
    """Return monthly Capital One CSV summary as JSON- and Jinja-friendly dicts."""
    rows = (
        db.session.query(
            db.func.strftime("%Y-%m", Transaction.date).label("ym"),
            Transaction.account_name,
            db.func.sum(Transaction.amount).label("sum_amount"),
            db.func.count().label("count_rows"),
        )
        .filter(
            Transaction.source_system == "Capital One CSV",
            Transaction.date >= date(2025, 1, 1),
        )
        .group_by("ym", Transaction.account_name)
        .order_by("ym", Transaction.account_name)
        .all()
    )

    # Convert SQLAlchemy Row objects into plain dicts with simple types
    summary = []
    for r in rows:
        summary.append(
            {
                "ym": r.ym,
                "account_name": r.account_name,
                "sum_amount": float(r.sum_amount or 0),
                "count_rows": int(r.count_rows or 0),
            }
        )
    return summary



@app.route("/transactions")
def transactions():
    txs = (
        Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    return render_template("transactions.html", transactions=txs)

@app.route("/transactions/<int:txn_id>/update", methods=["POST"])
def update_transaction(txn_id):
    # Update a single transaction row from the Transactions page.
    txn = Transaction.query.get_or_404(txn_id)

    # Date (YYYY-MM-DD)
    date_str = (request.form.get("date") or "").strip()
    if date_str:
        try:
            txn.date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            # Leave old date if parsing fails
            pass

    # Merchant & description
    txn.merchant = (request.form.get("merchant") or "").strip()
    txn.description = (request.form.get("description") or "").strip()

    # Amount (signed float)
    amount_str = (request.form.get("amount") or "").strip()
    if amount_str:
        try:
            txn.amount = float(amount_str)
        except ValueError:
            # Keep existing amount if invalid
            pass

    # Category & notes
    category = (request.form.get("category") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    txn.category = category or None
    txn.notes = notes or None

    db.session.commit()
    return redirect(url_for("transactions"))



@app.route("/import/csv", methods=["GET", "POST"])
def import_csv():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("No file selected", "danger")
            return redirect(request.url)

        # Save temporarily
        upload_folder = app.config.get("UPLOAD_FOLDER", "uploads")
        os.makedirs(upload_folder, exist_ok=True)
        tmp_path = os.path.join(upload_folder, file.filename)
        file.save(tmp_path)

        # Read with pandas
        try:
            df = pd.read_csv(tmp_path)
        except Exception as e:
            flash(f"Error reading CSV: {e}", "danger")
            return redirect(request.url)

        imported = 0
        skipped_invalid_dates = 0

        for _, row in df.iterrows():
            parsed_date, err = parse_date_safe(row.get("Date"))
            if parsed_date is None:
                skipped_invalid_dates += 1
                continue

            direction = normalize_string(row.get("Direction") or "debit")
            amount = coerce_amount(row.get("Amount"), direction)

            tx = Transaction(
                date=parsed_date,
                source_system=normalize_string(row.get("Source")),
                account_name=normalize_string(row.get("Account")),
                direction=direction,
                amount=amount,
                merchant=normalize_string(row.get("Merchant")),
                description=normalize_string(row.get("Description")),
                category=normalize_string(row.get("Category")),
                notes=normalize_string(row.get("Notes")),
            )
            db.session.add(tx)
            imported += 1

        db.session.commit()

        msg = f"Imported {imported} rows from CSV."
        if skipped_invalid_dates:
            msg += (
                f" Skipped {skipped_invalid_dates} rows with invalid dates"
                " (e.g. '2025-11-XX')."
            )
        flash(msg, "success")
        return redirect(url_for("transactions"))

    # GET
    return render_template("import_csv.html")


def scan_inbox_core():
    """
    Scan imports_inbox/ for .txt and .pdf and move/convert them into
    uploads/screenshots and uploads/statements.

    Returns:
        (moved_txt, converted_pdfs)
    """
    base = app.root_path
    inbox = os.path.join(base, "imports_inbox")
    screenshots = os.path.join(base, "uploads", "screenshots")
    statements = os.path.join(base, "uploads", "statements")

    os.makedirs(inbox, exist_ok=True)
    os.makedirs(screenshots, exist_ok=True)
    os.makedirs(statements, exist_ok=True)

    moved_txt = 0
    converted_pdfs = 0

    for p in Path(inbox).iterdir():
        if not p.is_file():
            continue

        ext = p.suffix.lower()

        # Treat loose .txt as pre-OCRed text (usually statements)
        if ext == ".txt":
            p.replace(Path(statements) / p.name)
            moved_txt += 1

        elif ext == ".pdf":
            # Convert Chase (and other) PDFs into text statements
            out_txt = Path(statements) / f"{p.stem}_ocr.txt"
            try:
                import subprocess
                subprocess.run(
                    ["pdftotext", "-layout", str(p), str(out_txt)],
                    check=True,
                )
                converted_pdfs += 1
            except Exception as e:
                print(f"[SCAN] Failed to convert {p}: {e}")
            # Keep the original PDF in the inbox for now
            continue

    return moved_txt, converted_pdfs





@app.route("/import/ocr", methods=["GET", "POST"])
def import_ocr():
    """
    Web import for statements / screenshots.

    Flow:
      - User uploads screenshots (PNG/JPG/PDF) or *_ocr.txt.
      - We clear the uploads + statements dirs so we ONLY process this batch.
      - We save the selected files into `uploads/`.
      - ocr_pipeline.process_uploaded_statement_files(uploads_dir, statements_dir)
        runs OCR as needed and parses into the DB.
    """
    from pathlib import Path
    from flask import request, flash, redirect, url_for, render_template

    # Where raw uploads (PNGs/PDFs/etc.) go
    uploads_dir = Path("uploads")
    # Where generated *_ocr.txt live
    statements_dir = Path("uploads/statements")

    uploads_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    if request.method == "POST":
        # Accept multiple possible field names, old and new
        uploaded_files = (
            request.files.getlist("ocr_files")
            or request.files.getlist("statement_files")
            or request.files.getlist("files")
        )

        # 1) Clear OLD uploads so we only handle what was just selected
        for p in uploads_dir.iterdir():
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass

        # 2) Optionally clear old *_ocr.txt so we only import this batch
        for p in statements_dir.glob("*.txt"):
            try:
                p.unlink()
            except OSError:
                pass

        saved_any = False
        for f in uploaded_files:
            if not f or not f.filename:
                continue
            dest = uploads_dir / f.filename
            f.save(dest)
            saved_any = True

        if not saved_any:
            flash("No files were selected for import.", "warning")
            return redirect(url_for("import_ocr"))

        # ✅ Use your existing PNG/PDF → OCR → *_ocr.txt → DB pipeline
        stats = process_uploaded_statement_files(uploads_dir, statements_dir)

        return render_template("import_report.html", stats=stats, report=stats)

    # GET: show upload form
    return render_template("import_ocr.html")

@app.route("/")
def home():
    from flask import redirect
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    from datetime import date
    from dateutil.relativedelta import relativedelta
    from sqlalchemy import func, extract

    today = date.today()

    # Net worth = sum of all transactions
    net_worth = db.session.query(
        func.coalesce(func.sum(Transaction.amount), 0)
    ).scalar() or 0

    # Current-month income (positive amounts)
    monthly_income = db.session.query(
        func.coalesce(func.sum(Transaction.amount), 0)
    ).filter(
        Transaction.amount > 0,
        extract('month', Transaction.date) == today.month,
        extract('year', Transaction.date) == today.year,
    ).scalar() or 0

    # Current-month spending (negative amounts, absolute)
    monthly_spending = abs(
        db.session.query(
            func.coalesce(func.sum(Transaction.amount), 0)
        ).filter(
            Transaction.amount < 0,
            extract('month', Transaction.date) == today.month,
            extract('year', Transaction.date) == today.year,
        ).scalar() or 0
    )

    # Category chart: spending only
    cat = db.session.query(
        Transaction.category,
        func.sum(func.abs(Transaction.amount))
    ).filter(
        Transaction.amount < 0
    ).group_by(
        Transaction.category
    ).all()

    category_data = {
        "labels": [r[0] for r in cat],
        "datasets": [{
            "data": [float(r[1]) for r in cat],
            "backgroundColor": [
                "#FF6384", "#36A2EB", "#FFCE56",
                "#4BC0C0", "#9966FF", "#FF9F40", "#C9CBCF"
            ],
        }],
    }

    # 12-month trend (income vs spending)
    months, income, spending = [], [], []
    for i in range(11, -1, -1):
        m = date(today.year, today.month, 1) - relativedelta(months=i)

        inc = db.session.query(
            func.coalesce(func.sum(Transaction.amount), 0)
        ).filter(
            extract('month', Transaction.date) == m.month,
            extract('year', Transaction.date) == m.year,
            Transaction.amount > 0,
        ).scalar() or 0

        spn = abs(
            db.session.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            ).filter(
                extract('month', Transaction.date) == m.month,
                extract('year', Transaction.date) == m.year,
                Transaction.amount < 0,
            ).scalar() or 0
        )

        months.append(m.strftime("%b %Y"))
        income.append(float(inc))
        spending.append(float(spn))

    trend_data = {
        "labels": months,
        "datasets": [
            {
                "label": "Income",
                "data": income,
                "borderColor": "#28a745",
                "tension": 0.3,
            },
            {
                "label": "Spending",
                "data": spending,
                "borderColor": "#dc3545",
                "tension": 0.3,
            },
        ],
    }

    return render_template(
        "dashboard.html",
        net_worth=net_worth,
        monthly_income=monthly_income,
        monthly_spending=monthly_spending,
        category_data=category_data,
        trend_data=trend_data,
    )
if __name__ == "__main__":
    # Dev server: change host/port as needed
    app.run(debug=True)


@app.route("/add_manual")
def add_manual():
    from flask import redirect, url_for
    return redirect(url_for("dashboard"))


@app.route("/reports")
def reports():
    from flask import redirect
    return redirect("/dashboard")


@app.route("/api/transactions/<int:txn_id>", methods=["PUT"])
def update_transaction_json(txn_id):
    txn = Transaction.query.get_or_404(txn_id)
    data = request.get_json(force=True) or {}

    # Date: expect 'YYYY-MM-DD'
    if "date" in data and data["date"]:
        from datetime import datetime as _dt
        try:
            txn.date = _dt.strptime(data["date"], "%Y-%m-%d").date()
        except ValueError:
            pass

    if "merchant" in data:
        txn.merchant = (data["merchant"] or "").strip()

    if "amount" in data:
        try:
            txn.amount = float(data["amount"])
        except (TypeError, ValueError):
            pass

    if "category" in data:
        cat = (data["category"] or "").strip()
        txn.category = cat or None

    if "notes" in data:
        notes = (data["notes"] or "").strip()
        txn.notes = notes or None

    db.session.commit()

    return {
        "status": "ok",
        "transaction": {
            "id": txn.id,
            "date": txn.date.isoformat() if txn.date else None,
            "merchant": txn.merchant,
            "amount": float(txn.amount) if txn.amount is not None else None,
            "category": txn.category,
            "notes": txn.notes or "",
        },
    }
