#!/usr/bin/env python3
"""
hard_reset_budget_data.py

Deep wipe of budget data:

- Drops ALL tables and recreates them (including OcrRejected).
- Clears imported OCR artifacts (text / screenshots / temp dirs) under uploads/.
"""

from pathlib import Path
import shutil

from app import app, db

# Adjust these paths as needed based on your project layout
ARTIFACT_DIRS = [
    Path("uploads/statements"),
    Path("uploads/screenshots"),
    Path("uploads/ocr_text"),
    Path("uploads/tmp"),
    Path("uploads/processed"),
]

def wipe_db():
    with app.app_context():
        print("⚠️  Dropping ALL tables...")
        db.drop_all()
        print("✅ Tables dropped.")
        print("🧱 Recreating tables...")
        db.create_all()
        db.session.commit()
        print("✅ Tables recreated.")

def wipe_artifacts():
    base = Path(__file__).parent
    for rel in ARTIFACT_DIRS:
        p = (base / rel).resolve()
        if p.exists():
            print(f"🗑  Removing {p} ...")
            shutil.rmtree(p)
        # Recreate empty directory so import UI doesn’t break
        p.mkdir(parents=True, exist_ok=True)
        print(f"📂 Recreated empty {p}")

def main():
    print("=== HARD RESET: DB + OCR ARTIFACTS ===")
    wipe_db()
    wipe_artifacts()
    print("✨ Hard reset complete. Ready for a fresh import.")

if __name__ == "__main__":
    main()
