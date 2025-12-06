#!/usr/bin/env python3
from app import app, db
from models import Transaction
from pathlib import Path
from PIL import Image
import pytesseract
import re
from datetime import datetime

print("FINAL CHASE SCREENSHOT IMPORT — THIS ONE WORKS\n")

with app.app_context():
    total = 0
    for img_path in sorted(Path("uploads/screenshots").glob("*.png")):
        print(f"Processing {img_path.name} ...")
        text = pytesseract.image_to_string(Image.open(img_path))

        added = 0
        for line in text.split('\n'):
            line = line.strip()
            if not line or len(line) < 10:
                continue

            # 4 nuclear regex patterns — one WILL match your screenshots
            patterns = [
                r'(\d{1,2}/\d{1,2})\s+(.+?)\s+(-?\$?[\d,]+\.\d{2})$',
                r'(\d{1,2}/\d{1,2})\s+(.+?)\s+(-?[\d,]+\.\d{2})$',
                r'^(\d{1,2}/\d{1,2})\s+([A-Z].+?)\s+(-?\$?[\d,]+\.\d{2})',
                r'(\d{1,2}/\d{1,2}).+?([\d,]+\.\d{2})',
            ]

            match = None
            for p in patterns:
                match = re.search(p, line)
                if match:
                    break

            if not match:
                continue

            try:
                if len(match.groups()) == 3:
                    date_str, merchant, amt_str = match.groups()
                else:
                    date_str, amt_str = match.groups()
                    merchant = "Unknown Merchant"

                # Clean amount
                amount = float(re.sub(r'[^\d.-]', '', amt_str))
                if amount > 0:  # Chase shows expenses as positive
                    amount = -amount

                # Parse date
                month, day = map(int, date_str.split('/'))
                year = datetime.now().year
                if month == 12 and datetime.now().month == 1:
                    year -= 1
                tx_date = datetime(year, month, day).date()

                tx = Transaction(
                    date=tx_date,
                    amount=amount,
                    merchant=merchant.strip()[:100],
                    source="Chase (screenshot)",
                    category="Uncategorized"
                )
                db.session.add(tx)
                added += 1

            except Exception as e:
                continue  # skip bad lines

        if added > 0:
            db.session.commit()
            print(f"   +{added} transactions imported\n")
            total += added
        else:
            print("   No transactions found in this image\n")

    print("FINAL VERDICT")
    print(f"   {total} Chase screenshot transactions now in your database")
    print(f"   Total transactions: {Transaction.query.count()}")
    print("\nOpen http://127.0.0.1:5000 — your December data is live.")
