#!/usr/bin/env python3
from app import app, db
from models import Transaction
from pathlib import Path
from PIL import Image
import pytesseract
import re
from datetime import datetime

print("\nSCREENSHOT IMPORT — ALL BANKS + PENDING SUPPORT — FINAL & WORKING\n")

with app.app_context():
    total = 0
    for img in sorted(Path("uploads/screenshots").glob("*.png")):
        print(f"→ {img.name}")
        text = pytesseract.image_to_string(Image.open(img), config='--psm 6')

        # Reuse your existing robust parser pipeline
        from ocr_pipeline import parse_ocr_text
        txns = parse_ocr_text(text)

        if not txns:
            print("   No transactions found")
            continue

        added = 0
        for t in txns:
            # Mark pending
            if 'pending' in text.lower() or 'pending' in t.get('merchant', '').lower():
                t.setdefault('notes', '')
                t['notes'] = t['notes'] + ' | Pending' if t['notes'] else 'Pending'

            # Ensure source
            t['source'] = t.get('source') or 'Screenshot'

            # Insert
            try:
                tx = Transaction(
                    date=t['date'],
                    amount=t['amount'],
                    merchant=t['merchant'][:100],
                    category=t.get('category', 'Uncategorized'),
                    source=t['source'],
                    notes=t.get('notes', '')
                )
                db.session.add(tx)
                added += 1
            except:
                continue

        db.session.commit()
        print(f"   +{added} transactions added")
        total += added

    print(f"\nSUCCESS — {total} transactions imported from screenshots")
    print("Run: python app.py → http://127.0.0.1:5000")
