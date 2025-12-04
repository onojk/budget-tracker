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
    Import via OCR:

    - User uploads PDF / PNG / JPG / JPEG files from anywhere
    - We save them to imports_inbox/
    - We run checksum-based dedupe + OCR + statement parsing
    - We flash a detailed status message and send user to /transactions
    """
    from pathlib import Path
    from flask import current_app

    if request.method == "GET":
        return render_template("import_ocr.html")

    uploaded_files = request.files.getlist("screenshot_files")
    if not uploaded_files:
        flash("Please choose at least one PDF or image file.", "danger")
        return redirect(url_for("import_ocr"))

    ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}

    base = Path(current_app.root_path)
    inbox_dir = base / "imports_inbox"
    statements_dir = base / "uploads" / "statements"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    saved_to_inbox = 0
    invalid = 0

    for f in uploaded_files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            invalid += 1
            continue

        safe_name = secure_filename(f.filename)
        dest = inbox_dir / safe_name
        # Avoid overwriting by bumping suffix
        i = 1
        while dest.exists():
            dest = inbox_dir / f"{dest.stem}_{i}{dest.suffix}"
            i += 1
        f.save(dest)
        saved_to_inbox += 1

    if invalid and not saved_to_inbox:
        flash("All files had unsupported extensions; only PDF, PNG, JPG, JPEG are allowed.", "danger")
        return redirect(url_for("import_ocr"))

    # Now run checksum-based OCR → statements + parse
    from ocr_pipeline import process_uploaded_statement_files

    stats = process_uploaded_statement_files(
        uploads_dir=inbox_dir,
        statements_dir=statements_dir,
        db_session=db.session,
        Transaction=Transaction,
    )

    saved_files = stats.get("saved_files", 0)
    skipped_files = stats.get("skipped_duplicates", 0)
    added_txs = stats.get("added_transactions", 0)

    msg_parts = [
        f"saved {saved_files} new file(s)",
        f"skipped {skipped_files} exact duplicates (based on checksum)",
        f"added {added_txs} new transactions",
    ]
    if invalid:
        msg_parts.append(f"ignored {invalid} unsupported file(s)")

    flash("OCR import finished: " + ", ".join(msg_parts) + ".", "success")
    return redirect(url_for("transactions"))

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





@app.route("/capone_csv_summary")
def capone_csv_summary():
    capone_summary = get_capone_csv_summary()
    rows = [
        {
            "ym": r.ym,
            "account_name": r.account_name,
            "sum_amount": float(r.sum_amount or 0),
            "count_rows": int(r.count_rows or 0),
        }
        for r in capone_summary
    ]
    return rows



@app.route("/", methods=["GET"])
# ORPHANED DEF REMOVED: def root_index():
    # Main entry: send / to the reports/dashboard page
        
@app.route("/")
def root_index():
    """Root URL = dashboard page."""
    return dashboard()


@app.route("/dashboard")
def dashboard():
    """
    Main dashboard with high-level summary and recent transactions.
    Uses all non-transfer transactions.
    """
    from sqlalchemy import func

    # Base query: ignore transfers where possible
    base_q = Transaction.query.filter(
        (Transaction.is_transfer == False) | (Transaction.is_transfer.is_(None))
    )

    # Recent transactions (newest first)
    transactions = (
        base_q.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    )

    num_transactions = len(transactions)
    first_date = transactions[-1].date if transactions else None
    last_date = transactions[0].date if transactions else None

    # Totals across ALL non-transfer transactions
    income_total = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            (Transaction.amount > 0),
            (Transaction.is_transfer == False) | (Transaction.is_transfer.is_(None)),
        )
        .scalar()
    )

    expense_total = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            (Transaction.amount < 0),
            (Transaction.is_transfer == False) | (Transaction.is_transfer.is_(None)),
        )
        .scalar()
    )

    net_total = (income_total or 0) + (expense_total or 0)

    return render_template(
        "dashboard.html",
        transactions=transactions,
        num_transactions=num_transactions,
        first_date=first_date,
        last_date=last_date,
        income_total=income_total,
        expense_total=expense_total,
        net_total=net_total,
    )

@app.route("/reports")
def reports():
    """
    Simple reports page stub so url_for('reports') works.
    You can wire real monthly/category data later.
    """
    monthly = []
    categories = []
    return render_template("reports.html", monthly=monthly, categories=categories)


if __name__ == "__main__":
    app.run(debug=True)
