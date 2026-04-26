#!/usr/bin/env python3
"""
regenerate_ocr_and_import.py

Step 1: For every PDF in uploads/statements, generate a matching
        *_ocr.txt using pdftotext (layout-preserving).

Step 2: Run ocr_pipeline.process_statement_files() on ALL *_ocr.txt
        files to parse them into Transactions, etc.
"""

from pathlib import Path
import subprocess
import inspect

from app import app, db, Transaction
from ocr_pipeline import process_statement_files


BASE_DIR = Path(__file__).resolve().parent
STATEMENTS_DIR = BASE_DIR / "uploads" / "statements"


def ensure_ocr_for_all_pdfs():
    if not STATEMENTS_DIR.exists():
        raise SystemExit(f"Statements dir not found: {STATEMENTS_DIR}")

    print(f"Scanning PDFs in: {STATEMENTS_DIR}")
    pdfs = sorted(STATEMENTS_DIR.glob("*.pdf"))

    if not pdfs:
        print("No PDFs found – nothing to OCR.")
        return

    for pdf in pdfs:
        ocr_txt = pdf.with_name(pdf.stem + "_ocr.txt")
        if ocr_txt.exists():
            print(f"[skip] {ocr_txt.name} already exists.")
            continue

        print(f"[ocr]  {pdf.name}  ->  {ocr_txt.name}")
        # Uses system pdftotext; install via: sudo apt install poppler-utils
        subprocess.run(
            ["pdftotext", "-layout", str(pdf), str(ocr_txt)],
            check=True,
        )


def run_pipeline_on_all_ocr():
    """
    Collect all *_ocr.txt files and call process_statement_files(file_paths=[...]).
    """
    sig = inspect.signature(process_statement_files)
    print(f"[debug] process_statement_files signature: {sig}")

    # Collect all *_ocr.txt files
    ocr_files = sorted(STATEMENTS_DIR.glob("*_ocr.txt"))
    if not ocr_files:
        print(f"[warn] No *_ocr.txt files found in {STATEMENTS_DIR}")
        return

    print(f"[debug] Found {len(ocr_files)} OCR text files to import.")
    for p in ocr_files[:5]:
        print(f"    sample file: {p.name}")
    if len(ocr_files) > 5:
        print("    ...")

    # Convert Paths to strings for the pipeline
    file_paths = [str(p) for p in ocr_files]

    print("[debug] Calling process_statement_files(...) with all OCR files…")

    # Prefer keyword if available
    if "file_paths" in sig.parameters:
        process_statement_files(file_paths=file_paths)
    else:
        # Fallback: assume the function accepts an iterable as first arg
        process_statement_files(file_paths)


def main():
    print("========================================")
    print("  Regenerate *_ocr.txt and import       ")
    print("========================================")
    print(f"STATEMENTS_DIR: {STATEMENTS_DIR}")

    with app.app_context():
        start_count = db.session.query(Transaction).count()
        print(f"[debug] Transactions BEFORE: {start_count}")

    # Step 1: make sure OCR exists
    ensure_ocr_for_all_pdfs()

    # Step 2: run pipeline on all *_ocr.txt
    with app.app_context():
        run_pipeline_on_all_ocr()
        db.session.commit()
        end_count = db.session.query(Transaction).count()
        print(f"[debug] Transactions AFTER:  {end_count}")


if __name__ == "__main__":
    main()
