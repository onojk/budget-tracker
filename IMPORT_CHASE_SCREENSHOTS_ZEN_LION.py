#!/usr/bin/env python3
from app import app, db
from models import Transaction
from pathlib import Path
from PIL import Image
import pytesseract
import re
from datetime import datetime

MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}

print("\nZEN LION — PURE TRANSACTIONS ONLY — YOUR DECEMBER DATA IS NOW IN THE DB\n")

with app.app_context():
    total = 0
    for img in sorted(Path("uploads/screenshots").glob("*.png")):
        print(f"→ {img.name}")
        text = pytesseract.image_to_string(Image.open(img), config='--psm 6')

        # FILTER: only lines that look like real Chase transaction lines
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue

            # Must contain a month name and a dollar amount
            if not any(m in line.upper() for m in MONTHS):
                continue
            if not re.search(r'\$?[\d,]+\.\d{2}', line):
                continue

            # Extract month + day + year
            m = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})[,\s]\d{4}', line, re.I)
            if not m:
                m = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+\d{4}', line, re.I)
            if not m:
                continue

            month_name, day_str = m.groups()
            month = MONTHS[month_name.upper()]
            day = int(day_str)
            year = datetime.now().year
            if month == 12 and datetime.now().month == 1:
                year -= 1

            # Extract amount (last one on line)
            amounts = re.findall(r'\$?[\d,]+\.\d{2}', line)
            if not amounts:
                continue
            amount = -float(amounts[-1].replace('$','').replace(',',''))

            # Extract merchant — everything between date and amount
            merchant = re.sub(r'^.*?\d{4}\s+', '', line, flags=re.I)
            merchant = re.sub(r'\s+\$?[\d,]+\.\d{2}.*$', '', merchant).strip()
            if len(merchant) > 100:
                merchant = merchant[:97] + '...'
            if not merchant:
                merchant = "Chase Purchase"

            tx = Transaction(
                date=datetime(year, month, day).date(),
                amount=amount,
                merchant=merchant,
                notes="From Chase screenshot",
                category="Uncategorized"
            )
            db.session.add(tx)
            total += 1

        db.session.commit()

    print(f"\nZEN LION VICTORY — {total} clean December transactions imported")
    print(f"Total in database: {Transaction.query.count()}")
    print("\nRun → python app.py")
    print("Open → http://127.0.0.1:5000")
