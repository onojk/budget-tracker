#!/usr/bin/env python3
"""
import_everything_from_downloads.py – FINAL VERSION
Works with your current app.py (no Flask factory)
"""

import sys
from pathlib import Path
import shutil
import tempfile
import hashlib
from datetime import datetime

# Add project root so imports work
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Your app.py creates the Flask app instance called "app" and the db object
from app import app, db
from models import Transaction
from app import is_duplicate_transaction
from ocr_pipeline import process_uploaded_statement_files

# Venmo parser – safe fallback if missing
try:
    from parsers.venmo_parser import parse_venmo_csv_file
except ImportError:
    def parse_venmo_csv_file(filepath, session):
        print("[WARN] parsers/venmo_parser.py not found – skipping Venmo CSVs")
        return 0


# ———————— Checksum helpers ————————
def get_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_existing_checksums(statements_dir: Path) -> set:
    chk_file = statements_dir / "checksums_all.txt"
    if not chk_file.exists():
        return set()
    return {line.split()[0] for line in chk_file.read_text().splitlines() if line.strip()}


def append_checksum(statements_dir: Path, path: Path, checksum: str):
    with open(statements_dir / "checksums_all.txt", "a") as f:
        f.write(f"{checksum}  {path.name}\n")


# ———————— Main ————————
def main():
    accounts_root = Path.home() / "Downloads" / "accounts"
    if not accounts_root.exists():
        print(f"[ERROR] {accounts_root} not found")
        return

    statements_dir = PROJECT_ROOT / "uploads" / "statements"
    statements_dir.mkdir(parents=True, exist_ok=True)

    existing_checksums = load_existing_checksums(statements_dir)

    with tempfile.TemporaryDirectory(prefix="budget_import_") as tmp:
        temp_uploads = Path(tmp)
        copied_pdfs = 0
        venmo_added = 0

        # 1. PDFs
        for pdf_path in sorted(accounts_root.rglob("*.pdf")):
            if pdf_path.name.endswith((".tar", ".csv")):
                continue
            checksum = get_sha256(pdf_path)
            if checksum in existing_checksums:
                continue
            dest = temp_uploads / f"{pdf_path.parent.name}__{pdf_path.name}"
            shutil.copy2(pdf_path, dest)
            copied_pdfs += 1
            append_checksum(statements_dir, pdf_path, checksum)

        print(f"[INFO] Copied {copied_pdfs} new PDFs")

        # 2. Venmo CSVs
        venmo_dir = accounts_root / "venmo"
        if venmo_dir.exists():
            for csv_path in sorted(venmo_dir.glob("*.csv")):
                checksum = get_sha256(csv_path)
                if checksum in existing_checksums:
                    continue
                print(f"[INFO] Importing Venmo CSV: {csv_path.name}")
                count = parse_venmo_csv_file(csv_path, db.session)
                venmo_added += count
                append_checksum(statements_dir, csv_path, checksum)

        # 3. Run OCR + normal pipeline inside Flask context
        if copied_pdfs or venmo_added:
            with app.app_context():          # ← this is the correct way for your app.py
                if copied_pdfs:
                    print(f"[INFO] Running OCR + parsers on {copied_pdfs} PDFs...")
                    result = process_uploaded_statement_files(
                        uploads_dir=temp_uploads,
                        statements_dir=statements_dir,
                        db_session=db.session,
                        Transaction=Transaction,
                        is_duplicate_transaction=is_duplicate_transaction,
                    )
                    print(f"[INFO] OCR pipeline finished: {result}")

                # Final count
                total = Transaction.query.count()
                print("\n" + "="*60)
                print(f"IMPORT SUCCESSFUL — {datetime.now():%Y-%m-%d %H:%M}")
                print(f"   New PDFs processed   : {copied_pdfs}")
                print(f"   Venmo transactions   : {venmo_added}")
                print(f"   TOTAL IN DATABASE    : {total}")
                print("="*60)
        else:
            print("[INFO] Nothing new to import — everything already processed!")


if __name__ == "__main__":
    main()
