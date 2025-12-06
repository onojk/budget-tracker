#!/usr/bin/env python3
from app import app, db
from models import Transaction
from pathlib import Path
from PIL import Image
import pytesseract
import re
from datetime import datetime

MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}

print("\nTHE LION HAS SPOKEN — YOUR DECEMBER CHASE DATA IS NOW IN YOUR DB\n")

with app.app_context():
    total = 0
    for img in sorted(Path("uploads/screenshots").glob("*.png")):
        print(f"Processing {img.name}")
        text = pytesseract.image_to_string(Image.open(img), config='--psm 6')

        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue

            upper = line.upper()
            if not any(m in upper for m in MONTHS):
                continue
            if not re.search(r'\d{1,2}[,\s]\d{4}', upper) or re.search(r'\d{1,2}\s+\d{4}', upper):
                pass
            else:
                continue
            if not re.search(r'\$?[\d,]*\d+\.\d{2}', upper):
                continue

            # Extract month/day/year
            m = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*(\d{1,2})[,\s]\d{4}', upper)
            if not m:
                m = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*(\d{1,2})\s+\d{4}', upper)
            if not m:
                continue

            month_name, day_str = m.groups()
            month = MONTHS[month_name]
            day = int(day_str)
            year = datetime.now().year
            if month == 12 and datetime.now().month == 1:
                year -= 1

            # Extract amount
            amounts = re.findall(r'\$?[\d,]+\.\d{2}', line)
            if not amounts:
                continue
            amount = -float(amounts[-1].replace('$','').replace(',',''))

            # Extract merchant
            merchant = re.sub(rf'.*?{month_name}.*?\d{{4}}\s*', '', line, flags=re.I)
            merchant = re.sub(rf'\s+{re.escape(amounts[-1])}.+$', '', merchant).strip()
            if len(merchant) > 100:
                merchant = merchant[:97] + '...'
            if not merchant:
                merchant = merchant or "Chase Purchase"

            # YOUR MODEL HAS NO 'source' — we use 'notes' instead
            tx = Transaction(
                date=datetime(year, month, day).date(),
                amount=amount,
                merchant=merchant,
                notes="Imported from Chase screenshot",
                category="Uncategorized"
            )
            db.session.add(tx)
            total += 1

        db.session.commit()

    print(f"\nLION VICTORY — {total} December Chase transactions imported")
    print(f"Total in database: {Transaction.query.count()}")
    print("\nRun: python app.py")
    print("Open: http://127.0.0.1:5000")
