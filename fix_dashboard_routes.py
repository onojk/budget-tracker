#!/usr/bin/env python3
import os
import shutil
from datetime import datetime

APP_FILE = "app.py"
BACKUP_DIR = "backup_fix_dashboard"


def backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"app.py.{ts}.bak")
    shutil.copy2(APP_FILE, backup_path)
    print(f"[backup] Saved {backup_path}")


def strip_root_and_dashboard_routes(lines):
    """
    Remove any existing @app.route('/') / @app.route("/") /
    @app.route('/dashboard') / @app.route("/dashboard") blocks.
    """
    new_lines = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith("@app.route") and (
            "'/'" in stripped
            or '"/"' in stripped
            or "'/dashboard'" in stripped
            or '"/dashboard"' in stripped
        ):
            # Skip all consecutive decorators for this view
            i += 1
            while i < n and lines[i].lstrip().startswith("@app.route"):
                i += 1

            # Skip the def line (function signature) if present
            if i < n and lines[i].lstrip().startswith("def "):
                func_indent = len(lines[i]) - len(lines[i].lstrip())
                i += 1

                # Skip function body (indented more than def)
                while i < n:
                    if lines[i].strip() == "":
                        i += 1
                        continue
                    indent = len(lines[i]) - len(lines[i].lstrip())
                    if indent > func_indent:
                        i += 1
                        continue
                    # Dedented back to top-level: stop skipping
                    break
            continue
        else:
            new_lines.append(line)
            i += 1

    return new_lines


def strip_orphan_analysis_block(lines):
    """
    Remove stray top-level analysis block that starts with base_q/income_total/spending_total.
    """
    patterns = (
        "base_q = Transaction.query.filter",
        "income_total =",
        "spending_total =",
    )

    new_lines = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if any(stripped.startswith(p) for p in patterns):
            # Skip this line and subsequent non-blank lines (the block)
            i += 1
            while i < n and lines[i].strip() != "":
                i += 1
            # Optionally skip the following blank line too
            if i < n and lines[i].strip() == "":
                i += 1
            continue
        else:
            new_lines.append(line)
            i += 1

    return new_lines


def append_clean_routes(lines):
    """
    Append the clean home() and dashboard() routes at the end of the file.
    """
    block = '''
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
'''.lstrip("\n")

    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.extend(block.splitlines())
    return lines


def main():
    if not os.path.exists(APP_FILE):
        raise SystemExit("app.py not found in current directory.")

    backup()

    with open(APP_FILE, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    lines = strip_root_and_dashboard_routes(lines)
    lines = strip_orphan_analysis_block(lines)
    lines = append_clean_routes(lines)

    with open(APP_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("[done] app.py updated with clean home() and dashboard().")


if __name__ == "__main__":
    main()
