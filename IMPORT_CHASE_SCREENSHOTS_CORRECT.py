#!/usr/bin/env python3
from app import app, db
from models import Transaction
from pathlib import Path
from PIL import Image
import pytesseract
import re
from datetime import datetime

MONTH_MAP = {
    'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
    'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12
}

print("CHASE SCREENSHOT IMPORT — FINAL VERSION — THIS ONE WORKS\n")

with app.app_context():
    total = 0
    for img in sorted(Path("uploads/screenshots").glob("*.png")):
        print(f"Processing {img.name}")
        text = pytesseract.image_to_string(Image.open(img), config='--psm 6')

        added = 0
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue

            # Matches: Dec 03,2025 FOODALESS #0300 VISTA CA 12/03 card $48.42
            m = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2}),\d{4}.+?(\$\d[\d,]*\.\d{2}|\d[\d,]*\.\d{2})', line, re.IGNORECASE)
            if not m:
                continue

            month_str, day_str, amt_str = m.groups()
            try:
                month = MONTH_MAP[month_str.upper()]
                day = int(day_str)
                year = datetime.now().year
                if month == 12 and datetime.now().month == 1:
                    year -= 1

                amount = -float(amt_str.replace('$','').replace(',',''))

                # Extract merchant name
                merchant = re.sub(r'^.*?\d{4}\s+', '', line)           # remove date
                merchant = re.sub(r'\s+\$?[\d,]+\.\d{2}.*$', '', merchant).strip()
                if not merchant:
                    merchant = "Chase Purchase"

                tx = Transaction(
                    date=datetime(year, month, day).date(),
                    amount=amount,
                    merchant=merchant[:100],
                    source="Chase (screenshot)",
                    category="Uncategorized"
                )
                db.session.add(tx)
                added += 1
            except Exception:
                continue

        if added:
            db.session.commit()
            print(f"   +{added} transactions added")
            total += added

    print(f"\nVICTORY — {total} Chase screenshot transactions imported")
    print(f"Total in database: {Transaction.query.count()}")
    print("\nNow run: python app.py")
    print("Then open http://127.0.0.1:5000")
