import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, date
from collections import OrderedDict

from ocr_pipeline import process_screenshot_files, process_statement_files
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
from models import db, Transaction, CategoryRule

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

    by_category = {
        "labels": cat_labels,
        "data": cat_values,
        "values": cat_values,
    }

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

    # --- Monthly aggregation for dashboard "Monthly Net Overview" ---
    txs = Transaction.query.order_by(Transaction.date.asc()).all()
    monthly_map = OrderedDict()

    for tx in txs:
        if not tx.date:
            continue

        key = tx.date.strftime("%Y-%m")  # e.g. "2025-11"
        bucket = monthly_map.setdefault(
            key,
            {
                "year": tx.date.year,
                "month": tx.date.month,
                "label": tx.date.strftime("%b %Y"),  # "Nov 2025"
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

    monthly = list(monthly_map.values())

    if monthly:
        monthly_overview_message = ""
    else:
        monthly_overview_message = (
            "No monthly data yet. Import some transactions to see this overview."
        )

    return render_template(
        "dashboard.html",
        net=net,
        total_spending=total_spending,
        total_income=total_income,
        cat_labels=cat_labels,
        cat_values=cat_values,
        by_category=by_category,
        daily_net=daily_net,
        monthly=monthly,
        monthly_overview_message=monthly_overview_message,
    )


@app.route("/transactions")
def transactions():
    txs = (
        Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    return render_template("transactions.html", transactions=txs)


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


@app.route("/import/ocr", methods=["GET", "POST"])
def import_ocr():
    """Import transactions from OCR text files in uploads/screenshots and uploads/statements."""
    if request.method == "POST":
        base = app.root_path
        screenshot_dir = os.path.join(base, "uploads", "screenshots")
        statement_dir = os.path.join(base, "uploads", "statements")
        os.makedirs(screenshot_dir, exist_ok=True)
        os.makedirs(statement_dir, exist_ok=True)

        screenshot_files = sorted(str(p) for p in Path(screenshot_dir).glob("*.txt"))
        statement_files = sorted(str(p) for p in Path(statement_dir).glob("*.txt"))

        rows = []
        if screenshot_files:
            rows.extend(process_screenshot_files(screenshot_files))
        if statement_files:
            rows.extend(process_statement_files(statement_files))

        imported = 0
        for r in rows:
            date_obj, reason = parse_date_safe(r.get("Date"))
            if date_obj is None:
                continue

            direction = (r.get("Direction") or "debit").lower()
            amount = coerce_amount(r.get("Amount"), direction)

            merchant = normalize_string(r.get("Merchant"))
            description = normalize_string(r.get("Description"))
            account_name = normalize_string(r.get("Account"))
            source_system = normalize_string(r.get("Source"))
            method = normalize_string(r.get("Method"))
            raw_category = normalize_string(r.get("Category"))

            # Auto-category via rule engine
            category = raw_category or "Uncategorized"
            auto_cat = guess_category(
                db,
                merchant=merchant,
                account_name=account_name,
                method=method,
            )
            if auto_cat:
                category = auto_cat

            # Skip duplicates
            if is_duplicate_transaction(
                db.session,
                date_obj,
                amount,
                merchant,
                account_name,
                description,
                source_system,
            ):
                continue

            tx = Transaction(
                date=date_obj,
                source_system=source_system,
                account_name=account_name,
                direction=direction,
                amount=amount,
                merchant=merchant,
                description=description,
                # method is only stored on CategoryRule, not Transaction
                category=category,
                notes=normalize_string(r.get("Notes")),
            )
            db.session.add(tx)
            imported += 1

        db.session.commit()
        flash(f"OCR import complete. Imported {imported} transactions.", "success")
        return redirect(url_for("transactions"))

    # GET
    return render_template("import_ocr.html")


@app.route("/import/scan", methods=["POST"])
def import_scan():
    """Scan imports_inbox for .txt and .pdf, convert PDFs, move them into uploads folders."""
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

        if ext == ".txt":
            p.replace(Path(screenshots) / p.name)
            moved_txt += 1

        elif ext == ".pdf":
            out_txt = Path(statements) / f"{p.stem}_ocr.txt"
            try:
                subprocess.run(
                    ["pdftotext", "-layout", str(p), str(out_txt)],
                    check=True,
                )
                converted_pdfs += 1
            except Exception as e:
                print(f"[SCAN] Failed to convert {p}: {e}")
            continue

    flash(
        f"Scanned inbox: moved {moved_txt} text files, converted {converted_pdfs} PDFs.",
        "success",
    )
    return redirect(url_for("import_ocr"))


@app.route("/add/manual", methods=["GET", "POST"])
def add_manual():
    form = ManualTransactionForm()
    if form.validate_on_submit():
        if form.date.data < MIN_ALLOWED_DATE:
            flash("Date cannot be earlier than January 1, 2024.", "danger")
            return redirect(url_for("add_manual"))

        direction = form.direction.data or "debit"
        amount = coerce_amount(form.amount.data, direction)

        tx = Transaction(
            date=form.date.data,
            source_system=normalize_string(form.source_system.data),
            account_name=normalize_string(form.account_name.data),
            direction=direction,
            amount=amount,
            merchant=normalize_string(form.merchant.data),
            description=normalize_string(form.description.data),
            category=normalize_string(form.category.data),
            notes=normalize_string(form.notes.data),
        )
        db.session.add(tx)
        db.session.commit()
        flash("Manual transaction added.", "success")
        return redirect(url_for("transactions"))

    return render_template("add_manual.html", form=form)


# -------------------------------------------------------------------
# Main entry
# -------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
