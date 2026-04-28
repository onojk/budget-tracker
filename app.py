import os
import shutil
import subprocess
import re
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import OrderedDict

from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    flash,
)

from markupsafe import Markup
from werkzeug.utils import secure_filename
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, DateField, SelectField, TextAreaField
from wtforms.validators import DataRequired

import pandas as pd
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    func,
    or_,
    extract,
)

from config import Config
from models import db, Account, Transaction, CategoryRule, OcrRejectedLine
from ocr_pipeline import (
    process_screenshot_files,
    process_statement_files,
    process_uploaded_statement_files,
)

# Date constants
START_DATE = date(2024, 1, 1)
MIN_ALLOWED_DATE = date(2024, 1, 1)

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
    Parse dates from a few common formats and reject anything before 2024-01-01.

    Supported formats:
      - YYYY-MM-DD  (2025-12-05)
      - MM/DD/YYYY  (12/05/2025)
      - MM/DD/YY    (12/05/25)

    Returns (date_obj, reason) where date_obj may be None.
    """
    s = normalize_string(raw)
    if not s:
        return None, "empty"
    if "XX" in s:
        # e.g. 2025-11-XX from some OCR quirks
        return None, "contains XX"

    formats = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]
    last_error = None

    for fmt in formats:
        try:
            d = datetime.strptime(s, fmt).date()
            if d < MIN_ALLOWED_DATE:
                return None, "before-min-date"
            return d, None
        except ValueError as e:
            last_error = str(e)

    return None, last_error or "unrecognized-date-format"


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

        # income vs spending
        if amt >= 0:
            bucket["income"] += amt
        else:
            bucket["spending"] += amt

        # always update monthly net
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
    """
    Transactions page with simple filtering + sorting via query params.

    /transactions?category=Groceries&from=2025-11-01&to=2025-11-30&sort=amount&dir=asc
    """
    sort = request.args.get("sort", "date")
    direction = request.args.get("dir", "desc")
    category = request.args.get("category") or None
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None

    q = Transaction.query

    if category:
        q = q.filter(Transaction.category == category)

    if date_from:
        d_from, _ = parse_date_safe(date_from)
        if d_from:
            q = q.filter(Transaction.date >= d_from)

    if date_to:
        d_to, _ = parse_date_safe(date_to)
        if d_to:
            q = q.filter(Transaction.date <= d_to)

    # Sorting: by date or amount
    if sort == "amount":
        col = Transaction.amount
    else:
        col = Transaction.date

    if direction == "asc":
        q = q.order_by(col.asc(), Transaction.id.asc())
    else:
        q = q.order_by(col.desc(), Transaction.id.desc())

    txs = q.all()
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
    """
    CSV importer for Chase Activity Downloads.

    For this specific Chase export:
      - Details      -> 'MM/DD/YYYY' (the actual posting date)
      - Posting Date -> merchant / description text
      - Description  -> numeric amount (negative for debits, positive for credits)
      - Amount       -> type string (ACH_DEBIT, DEBIT_CARD, ACCT_XFER, etc.)
    """

    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename.lower().endswith(".csv"):
            flash("Please upload a valid CSV file.", "error")
            return redirect(url_for("import_csv"))

        # Save uploaded file
        upload_folder = "uploads"
        os.makedirs(upload_folder, exist_ok=True)
        csv_path = os.path.join(upload_folder, file.filename)
        file.save(csv_path)

        # Read CSV via pandas
        df = pd.read_csv(csv_path)

        imported = 0
        skipped_invalid_dates = 0

        # Loop through CSV rows
        for _, row in df.iterrows():

            # -------------- 1️⃣ Date from Details column --------------
            details_raw = str(row.get("Details", "")).strip()

            parsed_date = None
            err = None

            if details_raw:
                # In this Chase CSV, Details is just 'MM/DD/YYYY'
                parsed_date, err = parse_date_safe(details_raw)

            if parsed_date is None:
                skipped_invalid_dates += 1
                continue

            # -------------- 2️⃣ Amount from Description column --------------
            amount_raw = row.get("Description")
            try:
                amount = float(amount_raw)
            except Exception:
                # If amount isn't numeric, skip this row
                skipped_invalid_dates += 1
                continue

            # -------------- 3️⃣ Merchant text from Posting Date column --------------
            merchant = normalize_string(row.get("Posting Date") or "")

            # -------------- 4️⃣ Create transaction --------------
            t = Transaction(
                date=parsed_date,
                merchant=merchant,
                description=merchant,
                amount=amount,  # already signed appropriately in CSV
                account_name="Chase Checking (CSV)",
                source_system="ChaseCSV",
                category=None,
                notes=None,
            )

            db.session.add(t)
            imported += 1

        db.session.commit()

        msg = f"Imported {imported} rows from CSV. Skipped {skipped_invalid_dates} invalid rows."
        flash(msg, "success")

        return redirect(url_for("transactions"))

    # GET request — show upload form
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
    from pathlib import Path
    from flask import request, flash, redirect, url_for, render_template

    # Where raw uploads (PNGs/PDFs/etc.) go
    uploads_dir = Path("uploads")
    # Where generated *_ocr.txt live
    statements_dir = Path("uploads/statements")

    uploads_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    if request.method == "POST":
        # Accept multiple possible field names (support old & new UI)
        uploaded_files = (
            request.files.getlist("screenshot_files")
            or request.files.getlist("ocr_files")
            or request.files.getlist("statement_files")
            or request.files.getlist("files")
        )

        # 1) Clear OLD uploads so we only handle the current batch
        for p in uploads_dir.iterdir():
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass

        # 2) Clear old *_ocr.txt so only new OCR results are processed
        for p in statements_dir.glob("*.txt"):
            try:
                p.unlink()
            except OSError:
                pass

        saved_any = False
        for f in uploaded_files:
            if not f or not f.filename.strip():
                continue
            dest = uploads_dir / f.filename
            f.save(dest)
            saved_any = True

        if not saved_any:
            flash("No files were selected for import.", "warning")
            return redirect(url_for("import_ocr"))

        # Use the existing OCR → parse → DB import pipeline
        from ocr_pipeline import process_uploaded_statement_files
        from models import Transaction  # if already imported at top, you can remove this line

        stats = process_uploaded_statement_files(uploads_dir, statements_dir)

        return render_template("import_report.html", stats=stats, report=stats)

    # GET: render the upload form
    return render_template("import_ocr.html")

@app.route("/")
def home():
    from flask import redirect
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    d = _build_dashboard_data()
    return render_template("dashboard.html", **d)

# ----------------------------
# API: Get transactions list
# ----------------------------

# -------------------------------------------------------------------
# API: Summary for dashboard (/api/summary)
# -------------------------------------------------------------------
@app.route("/api/summary", methods=["GET"])
def api_summary():
    """
    Return summary data for the dashboard cards and charts.
    Uses Transaction.amount sign:
      - amount > 0 => income
      - amount < 0 => spending
    """
    today = date.today()
    month_start = date(today.year, today.month, 1)

    # Load all transactions once (fine for personal scale)
    all_tx = Transaction.query.all()

    # Current balance = sum of all amounts
    current_balance = sum((t.amount or 0.0) for t in all_tx)

    # This month only
    month_tx = [
        t for t in all_tx
        if t.date is not None and month_start <= t.date <= today
    ]

    income_this_month = sum((t.amount or 0.0) for t in month_tx if (t.amount or 0) > 0)
    spent_this_month = sum((t.amount or 0.0) for t in month_tx if (t.amount or 0) < 0)
    net_this_month = income_this_month + spent_this_month

    # By category (this month)
    by_category_map = {}
    for t in month_tx:
        cat = t.category or "Uncategorized"
        by_category_map.setdefault(cat, 0.0)
        by_category_map[cat] += (t.amount or 0.0)

    by_category = [
        {"category": cat, "amount": amt}
        for cat, amt in sorted(by_category_map.items(), key=lambda x: x[0].lower())
    ]

    # Trend: last 30 days by date
    days_back = 30
    start_date = today - timedelta(days=days_back - 1)
    recent_tx = [
        t for t in all_tx
        if t.date is not None and start_date <= t.date <= today
    ]

    trend_map = {}  # date -> dict(income=..., spending=..., net=...)
    for t in recent_tx:
        d = t.date
        if d not in trend_map:
            trend_map[d] = {"income": 0.0, "spending": 0.0, "net": 0.0}

        amt = float(t.amount or 0.0)
        if amt > 0:
            trend_map[d]["income"] += amt
        elif amt < 0:
            trend_map[d]["spending"] += amt
        trend_map[d]["net"] += amt

    trend = []
    for d in sorted(trend_map.keys()):
        entry = trend_map[d]
        trend.append(
            {
                "label": d.strftime("%m/%d"),
                "income": entry["income"],
                "spending": entry["spending"],  # NOTE: negative numbers; JS flips sign
                "net": entry["net"],
            }
        )

    return jsonify(
        {
            "current_balance": float(current_balance),
            "net_this_month": float(net_this_month),
            "total_income_this_month": float(income_this_month),
            "total_spent_this_month": float(spent_this_month),
            "today": today.strftime("%Y-%m-%d"),
            "by_category": by_category,
            "trend": trend,
        }
    )


# -------------------------------------------------------------------
# API: Phase 1 dashboard — position view (/api/dashboard)
# -------------------------------------------------------------------
def _build_dashboard_data():
    """
    Compute all data needed for the Phase 1 position dashboard.
    Shared by the /api/dashboard JSON endpoint and the /dashboard HTML route.
    """
    today = date.today()
    cutoff_90d = today - timedelta(days=90)

    accounts = Account.query.order_by(Account.id).all()

    cash_types = {"checking", "savings", "wallet"}

    cash_on_hand = sum(
        float(a.last_statement_balance or 0)
        for a in accounts
        if a.account_type in cash_types
    )
    total_debt = sum(
        float(a.last_statement_balance or 0)
        for a in accounts
        if a.account_type == "credit"
    )

    days_list = [
        a.days_since_last_statement
        for a in accounts
        if a.days_since_last_statement is not None
    ]
    avg_days = round(sum(days_list) / len(days_list)) if days_list else None

    total_tx = Transaction.query.count()

    # Interest charged in the last 90 days across credit accounts.
    # Works because our parsers consistently embed "INTEREST" / "Interest" in
    # the merchant string (e.g. "INTEREST CHARGE ON PURCHASES", "Capital One
    # Interest"). If a non-interest merchant ever contains that word, this query
    # needs a more specific filter. Today's data (Jan–Apr 2026) is safe.
    from sqlalchemy import func
    interest_90d = float(
        db.session.query(func.sum(func.abs(Transaction.amount)))
        .join(Account, Transaction.account_id == Account.id)
        .filter(
            Account.account_type == "credit",
            Transaction.date >= cutoff_90d,
            Transaction.merchant.ilike("%interest%"),
        )
        .scalar() or 0
    )

    # Sort: checking → savings → wallet → credit; descending balance within group.
    _type_order = {"checking": 0, "savings": 1, "wallet": 2, "credit": 3}
    sorted_accounts = sorted(
        accounts,
        key=lambda a: (_type_order.get(a.account_type or "", 99),
                       -float(a.last_statement_balance or 0)),
    )

    # Credit accounts only, for the debt thermometer (sorted by balance desc).
    credit_accounts = [a for a in sorted_accounts if a.account_type == "credit"]
    credit_total = total_debt or 1  # avoid div-by-zero in template

    account_list = []
    for a in sorted_accounts:
        days = a.days_since_last_statement
        account_list.append({
            "id":           a.id,
            "name":         a.name,
            "institution":  a.institution,
            "last4":        a.last4,
            "balance":      float(a.last_statement_balance or 0),
            "as_of":        a.last_statement_date.isoformat() if a.last_statement_date else None,
            "days_since":   days,
            "account_type": a.account_type,
            "stale":        days is not None and days >= 30,
        })

    return {
        "cash_on_hand":              cash_on_hand,
        "total_debt":                total_debt,
        "avg_days_since_statement":  avg_days,
        "total_transactions":        total_tx,
        "interest_90d":              interest_90d,
        "accounts":                  account_list,
        # Extra context for HTML template (not returned in JSON)
        "_credit_accounts":          credit_accounts,
        "_credit_total":             credit_total,
        "_today":                    today,
    }


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """Phase 1 position dashboard data as JSON."""
    data = _build_dashboard_data()
    # Strip private template-only keys before serialising
    return jsonify({k: v for k, v in data.items() if not k.startswith("_")})


# -------------------------------------------------------------------
# API: Get transactions list for table (/api/transactions)
# -------------------------------------------------------------------
@app.route("/api/transactions", methods=["GET"])
def get_transactions():
    """Return recent transactions for the transaction table."""
    limit = request.args.get("limit", 300, type=int)

    rows = (
        Transaction.query
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(limit)
        .all()
    )

    data = []
    for t in rows:
        data.append(
            {
                "id": t.id,
                "date": t.date.isoformat() if t.date else "",
                "amount": float(t.amount or 0.0),
                "merchant": t.merchant or "",
                "category": t.category or "",
                "notes": t.notes or "",
            }
        )

    app.logger.info("GET /api/transactions -> %d rows", len(data))
    return jsonify({"transactions": data})


# -------------------------------------------------------------------
# API: Simple inline update for dashboard (/api/transactions/update/<id>)
# -------------------------------------------------------------------
@app.route("/api/transactions/update/<int:txn_id>", methods=["POST"])
def update_transaction_inline(txn_id):
    """
    Dedicated endpoint for inline edits from the dashboard/transactions table.
    Updates only merchant/category/notes.
    """
    tx = Transaction.query.get_or_404(txn_id)
    data = request.get_json() or {}

    app.logger.info("INLINE UPDATE txn=%s payload=%r", txn_id, data)

    if "merchant" in data:
        tx.merchant = (data["merchant"] or "").strip()

    if "category" in data:
        cat = (data["category"] or "").strip()
        tx.category = cat or None

    if "notes" in data:
        notes = (data["notes"] or "").strip()
        tx.notes = notes or None

    db.session.commit()
    db.session.refresh(tx)

    return jsonify(
        {
            "status": "ok",
            "id": tx.id,
            "merchant": tx.merchant or "",
            "category": tx.category or "",
            "notes": tx.notes or "",
        }
    )

@app.route("/add_manual")
def add_manual():
    from flask import redirect, url_for
    return redirect(url_for("dashboard"))


@app.route("/reports")
def reports():
    from flask import redirect
    return redirect("/dashboard")


@app.template_filter("currency")
def _currency_filter(value):
    if value is None:
        return "—"
    return f"${abs(float(value)):,.2f}"


@app.route("/budget-summary")
def budget_summary():
    data = {
        "snapshot_date": "April 27, 2026",
        # ── Section 1: Current snapshot ───────────────────────
        "cash_accounts": [
            ("Chase Checking",  421.26),
            ("BoA Adv Plus",     19.64),
            ("Venmo",            16.93),
            ("Chase Savings",     0.01),
            ("PayPal Account",    0.00),
        ],
        "cash_total":  457.84,
        "debt_accounts": [
            ("CareCredit",            2740.45),
            ("CapOne Platinum 0728",   561.89),
            ("Citi Costco 2557",       536.45),
            ("CapOne Quicksilver",     499.71),
            ("PayPal Cashback",        149.51),
        ],
        "debt_total":  4488.01,
        "carecredit_balance":       2740.45,
        "cc_debt_after_carecredit": 1747.56,   # debt_total minus CareCredit (Mom committed)
        # ── Section 2: Recent paydown ─────────────────────────
        # (name, balance_before, balance_now)
        "paydown_rows": [
            ("CareCredit",        3740.45, 2740.45),
            ("Citi Costco",        596.45,  536.45),
            ("CapOne Platinum",    601.89,  561.89),
            ("PayPal Cashback",    189.51,  149.51),
            ("CapOne Quicksilver", 469.71,  499.71),
        ],
        "paydown_total": 1110.00,
        # ── Section 3: Monthly household ──────────────────────
        # (label, monthly_amount, is_transitioning)
        "income_items": [
            ("Spouse payroll (Millennium Health, biweekly)", 3670, False),
            ("Music royalties (PayPal)", 100, False),
            ("Uber driving (restarting, ~$300/wk target)", 1290, False),
        ],
        "income_recurring":     3770,   # spouse + royalties only (certain income)
        "uber_gas_increment":    360,   # incremental gas for Uber driving (500 mi/wk, 27 MPG, $4.50/gal)
        "uber_maintenance":       75,   # vehicle maintenance set-aside
        "uber_income_net":       855,   # 1290 gross - 360 gas - 75 maintenance
        "income_with_uber":     4625,   # recurring + uber_income_net (net of vehicle costs)
        "income_with_uber_net": 4625,   # alias — used in template for labeled total
        "fixed_items": [
            ("Rent + landlord utilities (water, trash, sewer, fee)", 2317),
            ("Electricity (SDGE)", 150),
            ("Cell phone (T-Mobile)", 180),
        ],
        "fixed_total":          2647,
        "available_after_fixed": 1123,
        # ── Section 4: Variable spending ──────────────────────
        "variable_items": [
            ("Groceries (Walmart, Costco, Vons, Albertsons)",        1500),
            ("Medical / Pet (Banfield, SuperCare, CVS, MyScripps)",   550),
            ("Dining / Fast Food (DoorDash, Starbucks, etc.)",        300),
            ("Amazon (online orders)",                                  200),
            ("Insurance (Geico)",                                        91),
            ("Therapy (recurring)",                                      85),
            ("Subscriptions (Hulu, Paramount, etc.)",                   80),
            ("Charitable giving (church)",                               54),
            ("Gas/Transportation (Arco, Costco Gas, Chevron)",          300),
            ("Other / uncategorized",                                   150),
        ],
        "variable_total": 3310,
        # ── Section 5: Structural gap ─────────────────────────
        "cc_minimums":              100,    # post-CareCredit (Wf2, Wf3)
        "cc_minimums_current":      244,    # pre-CareCredit clearance, current state (Wf1)
        "available_for_variable":   879,   # Wf1: income_recurring - fixed - cc_minimums_current
        "gap_without_earned":      2431,   # Wf1: variable_total - available_for_variable
        "uber_income":             1290,   # $300/wk gross
        "total_with_uber":         5060,   # kept for reference; income_with_uber (net) = 4625
        "available_with_uber":     1878,   # Wf2: income_with_uber_net - fixed - cc_minimums
        "gap_after_uber_no_cuts":  1432,   # Wf2: variable_total - available_with_uber
        # Wf3a: $1,200/mo cuts — realistic; still short
        "variable_realistic_cut":  2110,   # variable_total - 1200
        "shortfall_realistic":      232,   # variable_realistic_cut - available_with_uber (gap remains!)
        # Wf3b: $1,500/mo cuts — aggressive; first scenario with surplus
        "variable_aggressive_cut": 1810,   # variable_total - 1500
        "surplus_aggressive":        68,   # available_with_uber - variable_aggressive_cut
        # ── Section 6: May rent ───────────────────────────────
        "rent_due_date":   "Friday, May 1, 2026",
        "rent_amount":      2317,
        "rent_checking":    421.26,
        "rent_boa":          19.64,
        "rent_available":   440.90,
        "rent_shortfall":  1876.10,
    }

    # ── Chart 1: monthly net CC activity (DB query) ───────────────────────
    _CC_IDS = [5, 6, 7, 8, 9]  # CapOne Plat, Quick, Citi, CareCredit, PayPal CC
    _chart_months = [
        (2025, 5), (2025, 6), (2025, 7), (2025, 8), (2025, 9), (2025, 10),
        (2025, 11), (2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4),
    ]
    cc_net_history = []
    for _y, _m in _chart_months:
        _net = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.account_id.in_(_CC_IDS),
            extract("year",  Transaction.date) == _y,
            extract("month", Transaction.date) == _m,
        ).scalar() or 0.0
        _interest = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.account_id.in_(_CC_IDS),
            extract("year",  Transaction.date) == _y,
            extract("month", Transaction.date) == _m,
            Transaction.merchant.ilike("%interest%"),
        ).scalar() or 0.0
        cc_net_history.append({
            "month":    date(int(_y), int(_m), 1).strftime("%b '%y"),
            "net":      round(float(_net), 2),
            "interest": round(float(_interest), 2),
        })
    data["cc_net_history"] = cc_net_history
    data["total_interest_charged"] = round(
        abs(sum(h["interest"] for h in cc_net_history)), 2
    )

    # ── Chart 7: debt projection (12 months; realistic Uber + cut scenarios) ─
    _debt        = data["cc_debt_after_carecredit"]  # 1747.56 — after Mom clears CareCredit
    _gap         = data["gap_without_earned"]        # 2431 — no Uber, no cuts (Wf1)
    _shortfall   = data["shortfall_realistic"]       # 232  — Uber + $1,200 cuts (still growing!)
    _surplus_agg = data["surplus_aggressive"]        # 68   — Uber + $1,500 cuts (barely shrinking)
    data["projection_months"] = [
        "Apr '26", "May '26", "Jun '26", "Jul '26", "Aug '26", "Sep '26",
        "Oct '26", "Nov '26", "Dec '26", "Jan '27", "Feb '27", "Mar '27",
    ]
    data["proj_no_uber"]    = [round(_debt + i * _gap, 2)               for i in range(12)]
    data["proj_with_uber"]  = [round(_debt + i * _shortfall, 2)         for i in range(12)]
    data["proj_aggressive"] = [round(max(0, _debt - i * _surplus_agg), 2) for i in range(12)]

    return render_template("budget_summary.html", **data)


@app.route("/api/transactions/<int:txn_id>", methods=["PUT", "POST"])
def update_transaction_json(txn_id):
    """
    Update a single transaction via JSON payload.

    Expected JSON keys (any subset):
      - merchant (string)
      - category (string)
      - notes (string)
      - date (YYYY-MM-DD, optional)
      - amount (float, optional)
    """
    tx = Transaction.query.get_or_404(txn_id)
    data = request.get_json() or {}

    app.logger.info("UPDATE /api/transactions/%s payload=%r", txn_id, data)

    # --- Core inline-edit fields (what the UI is editing) ---
    if "merchant" in data:
        tx.merchant = (data["merchant"] or "").strip()

    if "category" in data:
        cat = (data["category"] or "").strip()
        tx.category = cat or None

    # ---- AUTO-LEARNING: Remember user's chosen category ----
    try:
        if tx.category:
            learn_category_from_transaction(
                db,
                merchant=tx.merchant,
                account_name=tx.account_name,
                method=tx.source_system,
                chosen_category=tx.category,
            )
    except Exception as e:
        app.logger.warning("Category learning failed for txn %s: %s", tx.id, e)

    if "notes" in data:
        notes = (data["notes"] or "").strip()
        tx.notes = notes or None

    # --- Optional: date support (YYYY-MM-DD) ---
    if "date" in data and data["date"]:
        try:
            tx.date = datetime.strptime(data["date"], "%Y-%m-%d").date()
        except ValueError:
            app.logger.warning(
                "Bad date format for txn %s: %r", txn_id, data["date"]
            )

    # --- Optional: amount support ---
    if "amount" in data and data["amount"] not in ("", None):
        try:
            tx.amount = float(data["amount"])
        except (TypeError, ValueError):
            app.logger.warning(
                "Bad amount for txn %s: %r", txn_id, data["amount"]
            )

    db.session.commit()
    db.session.refresh(tx)

    return jsonify(
        {
            "status": "ok",
            "transaction": {
                "id": tx.id,
                "date": tx.date.isoformat() if tx.date else "",
                "merchant": tx.merchant or "",
                "amount": float(tx.amount or 0.0),
                "category": tx.category or "",
                "notes": tx.notes or "",
            },
        }
    )

# -------------------------------------------------------------------
# API Guard + Delete Transaction Endpoint
# -------------------------------------------------------------------
import logging
from functools import wraps
from flask import jsonify

def api_guard(fn):
    """Simple safety wrapper for JSON routes."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            app.logger.exception("API ERROR in %s: %s", fn.__name__, e)
            return jsonify({"error": "internal_error"}), 500
    return wrapper


@app.route("/api/transactions/<int:txn_id>", methods=["DELETE"])
@api_guard
def delete_transaction_json(txn_id):
    tx = db.session.get(Transaction, txn_id)
    if tx is None:
        return jsonify({"error": "not found"}), 404

    # Null out the back-reference on any transfer partner that points at this
    # transaction, so we don't leave a dangling linked_transaction_id FK.
    partners = (
        db.session.query(Transaction)
        .filter(Transaction.linked_transaction_id == txn_id)
        .all()
    )
    for partner in partners:
        partner.linked_transaction_id = None

    app.logger.info(
        "DELETE txn %s (%s %s %s)", tx.id, tx.date, tx.amount, tx.merchant
    )
    db.session.delete(tx)
    db.session.commit()

    return jsonify({"deleted": True, "id": txn_id})


# -------------------------------------------------------------------
# API: Get distinct categories for autocomplete / UI helpers
# -------------------------------------------------------------------
@app.route("/api/categories", methods=["GET"])
@api_guard
def get_categories():
    """
    Returns a sorted list of distinct categories.
    Useful for:
      - <datalist> autocomplete
      - bulk-edit dropdowns
      - analytics tools
    """
    rows = (
        db.session.query(Transaction.category)
        .filter(Transaction.category.isnot(None))
        .distinct()
        .all()
    )

    categories = sorted({(r[0] or "").strip() for r in rows if r[0]})
    return jsonify({"categories": categories})


# -------------------------------------------------------------------
# API: Bulk update transactions (e.g. mass recategorize)
# -------------------------------------------------------------------
@app.route("/api/transactions/bulk", methods=["PUT"])
@api_guard
def bulk_update_transactions():
    """
    Bulk-update a set of transactions.

    Expected JSON:
    {
      "ids": [1, 2, 3],
      "fields": {
        "category": "Groceries",
        "notes": "Fixed via bulk"
      }
    }

    Only "category" and "notes" are honored for now.
    """
    payload = request.get_json() or {}
    ids = payload.get("ids") or []
    fields = payload.get("fields") or {}

    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "missing_or_invalid_ids"}), 400

    # Normalize fields we support
    new_category = None
    new_notes = None

    if "category" in fields:
        new_category = (fields.get("category") or "").strip() or None

    if "notes" in fields:
        new_notes = (fields.get("notes") or "").strip() or None

    if new_category is None and new_notes is None:
        return jsonify({"error": "no_supported_fields"}), 400

    # Apply updates
    q = Transaction.query.filter(Transaction.id.in_(ids))
    updated = 0
    for tx in q:
        if new_category is not None:
            tx.category = new_category
        if new_notes is not None:
            tx.notes = new_notes
        updated += 1

    db.session.commit()

    app.logger.info(
        "BULK UPDATE %d transactions (ids=%s) fields=%r",
        updated,
        ids,
        fields,
    )

    return jsonify({"status": "ok", "updated": updated})


# ----------------------------
# Run development server
# ----------------------------
if __name__ == "__main__":
    app.run(debug=True)
