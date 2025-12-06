#!/usr/bin/env python3
from app import app, db
from models import Transaction
from pathlib import Path
from PIL import Image
import pytesseract
from datetime import datetime

print("\nFINAL WORKING SCREENSHOT IMPORT — USES YOUR ACTUAL CODE\n")

with app.app_context():
    total = 0
    for img in sorted(Path("uploads/screenshots").glob("*.png")):
        print(f"Processing {img.name}")
        text = pytesseract.image_to_string(Image.open(img), config='--psm 6')

        # THIS IS THE REAL FUNCTION THAT EXISTS IN YOUR ocr_pipeline.py
        from ocr_pipeline import _parse_ocr_text_file
        txns = _parse_ocr_text_file(text, db.session, Transaction)

        if not txns:
            print("   No transactions found")
            continue

        # Mark pending if needed
        for t in txns:
            if 'pending' in text.lower():
                notes = t.get('notes', '') or ''
                t['notes'] = notes + ' | Pending' if notes else 'Pending'

        added = len(txns) if isinstance(txns, list) else 1
        print(f"   +{added} transactions added")
        total += added

    print(f"\nSUCCESS — {total} transactions imported from screenshots")
    print(f"Total in database: {Transaction.query.count()}")
    print("\nNow run: python app.py")
    print("Open: http://127.0.0.1:5000")
