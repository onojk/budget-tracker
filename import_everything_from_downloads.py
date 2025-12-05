#!/usr/bin/env python3
"""
import_everything_from_downloads.py

Super-importer for ALL account PDFs under:
    ~/Downloads/accounts

Workflow:
  1) Recursively find all *.pdf under ~/Downloads/accounts
  2) Copy them into a temporary uploads dir with unique names
  3) Call process_uploaded_statement_files(...) using that temp dir
  4) OCR + parse + insert via your existing pipeline
"""

from pathlib import Path
import shutil
import tempfile

from app import app, db, Transaction, is_duplicate_transaction
from ocr_pipeline import process_uploaded_statement_files


def main():
    # Root where all your account folders live
    accounts_root = Path.home() / "Downloads" / "accounts"
    if not accounts_root.exists():
        print(f"[error] Accounts root does not exist: {accounts_root}")
        return

    # All PDFs (capitalone, carecredit, chase, paypal, etc.)
    pdf_paths = sorted(accounts_root.rglob("*.pdf"))
    print(f"[info] Found {len(pdf_paths)} PDFs under {accounts_root}")
    if not pdf_paths:
        return

    # Where OCR text files live (existing statements dir in your app)
    base_dir = Path(__file__).resolve().parent
    statements_dir = base_dir / "uploads" / "statements"
    statements_dir.mkdir(parents=True, exist_ok=True)

    # Temporary uploads dir for this one-shot import
    temp_uploads = Path(tempfile.mkdtemp(prefix="all_accounts_"))
    print(f"[info] Temporary uploads dir: {temp_uploads}")

    # Copy PDFs into temp uploads, prefixing with parent folder to avoid name clashes
    copied = 0
    for src in pdf_paths:
        # Skip backup archives or non-statement junk if needed
        if src.name.endswith(".tar"):
            continue

        dest_name = f"{src.parent.name}__{src.name}"
        dest = temp_uploads / dest_name
        shutil.copy2(src, dest)
        copied += 1

    print(f"[info] Copied {copied} PDFs into temp uploads folder.")

    # Run your standard /import/ocr pipeline.
    # This will:
    #   - OCR any new PDFs into statements_dir as *_ocr.txt (checksum-based dedupe)
    #   - Run generic + Chase + Capital One + PayPal Credit parsers
    #   - Insert rows into the DB via db.session + Transaction
    print("[info] Running process_uploaded_statement_files() pipeline...")
    with app.app_context():
        result = process_uploaded_statement_files(
            uploads_dir=temp_uploads,
            statements_dir=statements_dir,
            db_session=db.session,
            Transaction=Transaction,
            is_duplicate_transaction=is_duplicate_transaction,
        )

        print("[info] Pipeline result:")
        print(result)

    # Clean up the temp uploads directory
    try:
        shutil.rmtree(temp_uploads)
        print("[info] Cleaned up temp upload directory.")
    except Exception as e:
        print(f"[warn] Could not delete temp folder: {e}")

    print("[info] Done super-importing from ~/Downloads/accounts.")

if __name__ == "__main__":
    main()
