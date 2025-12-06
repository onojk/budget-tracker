from app import app, db
from models import Transaction
from pathlib import Path
from PIL import Image
import pytesseract
import re
from datetime import datetime

def import_chase_screenshots():
    screenshot_dir = Path("uploads/screenshots")
    if not screenshot_dir.exists():
        print("No screenshots folder found")
        return

    files = sorted(screenshot_dir.glob("*.png"))
    print(f"Found {len(files)} Chase screenshots — importing now...")

    total = 0
    with app.app_context():
        for img_path = Path("uploads/screenshots")
        for img_path in sorted(img_path.glob("*.png")):
            print(f"  → OCR {img_path.name}")
            text = pytesseract.image_to_string(Image.open(img_path))

            added = 0
            for line in text.split('\n'):
                line = line.strip()
                # Very permissive Chase browser format
                m = re.search(r'(\d{1,2}/\d{1,2})\s+(.+?)\s+(-?\$?[\d,]+\.\d{2})$', line)
                if not m:
                    m = re.search(r'(\d{1,2}/\d{1,2})\s+(.+?)\s+(-?[\d,]+\.\d{2})$', line)
                if m:
                    date_str, merchant, amt_str = m.groups()
                    try:
                        amount = -float(amt_str.replace('$','').replace(',','').replace('(','').replace(')',''))
                        if '(' in amt_str:  # refund
                            amount = abs(amount)
                        month, day = map(int, date_str.split('/'))
                        year = datetime.now().year
                        if month == 12 and datetime.now().month ==1:
                            year -= 1
                        tx_date = datetime(year, month, day).date()

                        tx = Transaction(
                            date=tx_date,
                            amount=amount,
                            merchant=merchant.strip(),
                            source="Chase (screenshot)",
                            category="Uncategorized"
                        )
                        db.session.add(tx)
                        added += 1
                    except:
                        continue
            if added:
                db.session.commit()
                print(f"     +{added} transactions from {img_path.name}")
                total += added
        print(f"\nSUCCESS — {total} transactions from screenshots imported!")
        print(f"Total in database: {Transaction.query.count()}")

if __name__ == "__main__":
    import_chase_screenshots()
