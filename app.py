import os
from datetime import datetime, date

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    FloatField,
    DateField,
    SelectField,
    TextAreaField,
)
from wtforms.validators import DataRequired
from sqlalchemy import func
import pandas as pd

from config import Config
from models import db, Transaction

# Import the module only; we'll check for functions with hasattr
import ocr_pipeline


# -------------------------------------------------------------------
# Forms
# -------------------------------------------------------------------


class ManualTransactionForm(FlaskForm):
    date = DateField("Date", validators=[DataRequired()], default=date.today)
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
    Parse YYYY-MM-DD dates; return (date_obj, reason) where date_obj can be None.
    Used so we can skip things like '2025-11-XX'.
    """
    s = normalize_string(raw)
    if not s:
        return None, "empty"
    if "XX" in s:
        return None, "contains XX"
    try:
        return datetime.strptime(s, "%Y-%m-%d").date(), None
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


# -------------------------------------------------------------------
# Routes
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

    # Chart-ready structure: works with most Chart.js setups
    by_category = {
        "labels": cat_labels,
        "data": cat_values,    # if your JS uses .data
        "values": cat_values,  # if your JS uses .values
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

    return render_template(
        "dashboard.html",
        net=round(net, 2),
        total_spending=round(total_spending, 2),
        total_income=round(total_income, 2),
        cat_labels=cat_labels,
        cat_values=cat_values,
        by_category=by_category,
        daily_net=daily_net,
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
            msg += f" Skipped {skipped_invalid_dates} rows with invalid dates (e.g. '2025-11-XX')."
        flash(msg, "success")
        return redirect(url_for("transactions"))

    # GET
    return render_template("import_csv.html")


@app.route("/import/ocr", methods=["GET", "POST"])
def import_ocr():
    """
    Use OCR outputs if ocr_pipeline exposes helpers.
    This will NOT crash if those functions are missing.
    """
    if request.method == "POST":
        screenshot_folder = app.config.get("SCREENSHOT_FOLDER", "uploads/screenshots")
        statement_folder = app.config.get("STATEMENT_FOLDER", "uploads/statements")

        screenshot_paths = []
        if os.path.isdir(screenshot_folder):
            screenshot_paths = [
                os.path.join(screenshot_folder, f)
                for f in sorted(os.listdir(screenshot_folder))
                if f.lower().endswith(".txt")
            ]

        statement_paths = []
        if os.path.isdir(statement_folder):
            statement_paths = [
                os.path.join(statement_folder, f)
                for f in sorted(os.listdir(statement_folder))
                if f.lower().endswith(".txt")
            ]

        rows = []

        # Screenshots
        if screenshot_paths:
            if hasattr(ocr_pipeline, "process_screenshot_files"):
                rows.extend(
                    ocr_pipeline.process_screenshot_files(screenshot_paths)
                )
            else:
                flash(
                    "OCR: process_screenshot_files() not found in ocr_pipeline.py; "
                    "screenshots were skipped.",
                    "warning",
                )

        # Statements
        if statement_paths:
            if hasattr(ocr_pipeline, "process_statement_files"):
                rows.extend(
                    ocr_pipeline.process_statement_files(statement_paths)
                )
            else:
                flash(
                    "OCR: process_statement_files() not found in ocr_pipeline.py; "
                    "statements were skipped.",
                    "warning",
                )

        imported = 0
        for r in rows:
            parsed_date, err = parse_date_safe(r.get("Date"))
            if parsed_date is None:
                continue

            direction = normalize_string(r.get("Direction") or "debit")
            amount = coerce_amount(r.get("Amount"), direction)

            tx = Transaction(
                date=parsed_date,
                source_system=normalize_string(r.get("Source")),
                account_name=normalize_string(r.get("Account")),
                direction=direction,
                amount=amount,
                merchant=normalize_string(r.get("Merchant")),
                description=normalize_string(r.get("Description")),
                category=normalize_string(r.get("Category")),
                notes=normalize_string(r.get("Notes")),
            )
            db.session.add(tx)
            imported += 1

        db.session.commit()
        flash(
            f"OCR import complete. Imported {imported} transactions "
            f"(screenshots + statements combined).",
            "success",
        )
        return redirect(url_for("transactions"))

    # GET
    return render_template("import_ocr.html")


@app.route("/add/manual", methods=["GET", "POST"])
def add_manual():
    form = ManualTransactionForm()
    if form.validate_on_submit():
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
