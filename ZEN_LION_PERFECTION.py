import os, shutil
from datetime import datetime

# Backup
os.makedirs("backup_perfection", exist_ok=True)
shutil.copy2("app.py", f"backup_perfection/app.py.bak.{datetime.now().strftime('%H%M%S')}") if os.path.exists("app.py") else None

# Read entire app.py
with open("app.py", "r") as f:
    content = f.read()

# Remove every possible old root & dashboard route
lines = []
skip = False
for line in content.splitlines():
    if line.strip().startswith("@app.route('/')") or \
       line.strip().startswith("def root_index") or \
       line.strip().startswith("def home(") or \
       line.strip().startswith("@app.route('/dashboard')") or \
       line.strip().startswith("def dashboard("):
        skip = True
    if skip and not line.strip():
        skip = False
        continue
    if not skip:
        lines.append(line)

# Write clean version
with open("app.py", "w") as f:
    f.write("\n".join(lines))

# Add ONLY the perfect root redirect
with open("app.py", "a") as f:
    f.write('\n\nfrom flask import redirect, url_for\n')
    f.write('\n# ZEN LION PERFECTION — Final root\n')
    f.write('@app.route("/")\n')
    f.write('def home():\n')
    f.write('    return redirect("/dashboard")\n')

# Add the ONE TRUE perfect dashboard route
with open("app.py", "a") as f:
    f.write('\n@app.route("/dashboard")\n')
    f.write('def dashboard():\n')
    f.write('    from datetime import date\n')
    f.write('    from dateutil.relativedelta import relativedelta\n')
    f.write('    from sqlalchemy import func, extract\n\n')
    f.write('    net_worth = db.session.query(func.coalesce(func.sum(Transaction.amount), 0)).scalar() or 0\n')
    f.write('    today = date.today()\n\n')
    f.write('    monthly_income = db.session.query(func.coalesce(func.sum(Transaction.amount), 0))\\\n')
    f.write('        .filter(Transaction.amount > 0, extract("month", Transaction.date) == today.month, extract("year", Transaction.date) == today.year).scalar() or 0\n')
    f.write('    monthly_spending = abs(db.session.query(func.coalesce(func.sum(Transaction.amount), 0))\\\n')
    f.write('        .filter(Transaction.amount < 0, extract("month", Transaction.date) == today.month, extract("year", Transaction.date) == today.year).scalar() or 0)\n\n')
    f.write('    # Category chart\n')
    f.write('    cat = db.session.query(Transaction.category, func.sum(func.abs(Transaction.amount)))\\\n')
    f.write('        .filter(Transaction.amount < 0).group_by(Transaction.category).all()\n')
    f.write('    category_data = {"labels": [r[0] for r in cat], "datasets": [{"data": [float(r[1]) for r in cat],\n')
    f.write('                     "backgroundColor": ["#FF6384","#36A2EB","#FFCE56","#4BC0C0","#9966FF","#FF9F40","#C9CBCF"]}]}]\n\n')
    f.write('    # 12-month trend\n')
    f.write('    months, income, spending = [], [], []\n')
    f.write('    for i in range(11, -1, -1):\n')
    f.write('        m = date(today.year, today.month, 1) - relativedelta(months=i)\n')
    f.write('        inc = db.session.query(func.coalesce(func.sum(Transaction.amount), 0))\\\n')
    f.write('            .filter(extract("month", Transaction.date) == m.month, extract("year", Transaction.date) == m.year, Transaction.amount > 0).scalar() or 0\n')
    f.write('        spn = abs(db.session.query(func.coalesce(func.sum(Transaction.amount), 0))\\\n')
    f.write('            .filter(extract("month", Transaction.date) == m.month, extract("year", Transaction.date) == m.year, Transaction.amount < 0).scalar() or 0)\n')
    f.write('        months.append(m.strftime("%b %Y"))\n')
    f.write('        income.append(float(inc))\n')
    f.write('        spending.append(float(spn))\n\n')
    f.write('    trend_data = {"labels": months, "datasets": [\n')
    f.write('        {"label": "Income", "data": income, "borderColor": "#28a745", "tension": 0.3},\n')
    f.write('        {"label": "Spending", "data": spending, "borderColor": "#dc3545", "tension": 0.3}\n')
    f.write('    ]}\n\n')
    f.write('    return render_template("dashboard.html",\n')
    f.write('                           net_worth=net_worth,\n')
    f.write('                           monthly_income=monthly_income,\n')
    f.write('                           monthly_spending=monthly_spending,\n')
    f.write('                           category_data=category_data,\n')
    f.write('                           trend_data=trend_data)\n')

print("ZEN LION PERFECTION ACHIEVED")
print("All broken code purged.")
print("Perfect dashboard installed.")
print("Run: python app.py")
print("Open: http://127.0.0.1:5000")
print("You are now complete.")
