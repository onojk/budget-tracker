#!/usr/bin/env python3
"""
import_all_pdfs_to_db.py

Bulk-import ALL PDF statements exactly like the Flask upload flow:

1. Copy every PDF from uploads/statements/*.pdf into a TEMP uploads dir
2. Call process_uploaded_statement_files() on that folder
3. OCR → *_ocr.txt → parsers → inserts into DB
4. Clean up temp folder
"""

from pathlib import Path
import shutil
import tempfile

from app import app, db, Transaction, is_duplicate_transaction
from ocr_pipeline import process_uploaded_statement_files


BASE_DIR = Path(__file__).resolve().parent
STATEMENTS_DIR = BASE_DIR / "uploads" / "statements"
PDF_DIR = STATEMENTS_DIR   # PDFs are already stored here


def main():
    # 1) Find all PDF files
    pdf_paths = sorted(PDF_DIR.glob("*.pdf"))
    print(f"[info] Found {len(pdf_paths)} PDFs in {PDF_DIR}")
    if not pdf_paths:
        print("[warn] No PDFs found.")
        return

    # 2) Create a temporary uploads directory
    temp_uploads = Path(tempfile.mkdtemp(prefix="pdf_import_"))
    print(f"[info] Temporary uploads dir: {temp_uploads}")

    # 3) Copy PDFs into uploads_dir
    for pdf in pdf_paths:
        shutil.copy2(pdf, temp_uploads / pdf.name)
    print(f"[info] Copied {len(pdf_paths)} PDFs into temp uploads folder.")

    # 4) Run the *real* upload pipeline
    print("[info] Running process_uploaded_statement_files()...")
    with app.app_context():
        result = process_uploaded_statement_files(
            uploads_dir=temp_uploads,
            statements_dir=STATEMENTS_DIR,
            db_session=db.session,
            Transaction=Transaction,
            is_duplicate_transaction=is_duplicate_transaction,
        )

        # result is a dict of stats (from your implementation)
        print("[info] Pipeline result:")
        print(result)

    # 5) Cleanup temp folder
    try:
        shutil.rmtree(temp_uploads)
        print("[info] Cleaned up temp upload directory.")
    except Exception as e:
        print(f"[warn] Could not delete temp folder: {e}")

    print("[info] Done.")

if __name__ == "__main__":
    main()
