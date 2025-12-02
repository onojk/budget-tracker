#!/usr/bin/env python
"""
per_pdf_txn_counts.py

For a directory of statement PDFs, run your full OCR → DB pipeline
ONE FILE AT A TIME, and report per-PDF transaction counts.

For each PDF, this script:
  - drops & recreates all DB tables
  - cleans imports_inbox/ and uploads/statements/
  - copies just that PDF into imports_inbox/
  - runs process_uploaded_statement_files() on that one file
  - records added_transactions, candidate_lines, statement_rows

At the end it prints a summary table and the grand total of all
added_transactions across all PDFs.

Usage:

    cd ~/budget_app
    source budget-env/bin/activate
    python per_pdf_txn_counts.py --pdf-dir ~/statement_23_pdfs
"""

import argparse
import shutil
from pathlib import Path

from sqlalchemy import func

from app import app, db, Transaction
from ocr_pipeline import process_uploaded_statement_files


def clean_dir_files(path: Path) -> None:
    """Delete all files under a directory tree, keep directories."""
    if not path.exists():
        return
    for p in path.rglob("*"):
        if p.is_file():
            p.unlink()


def main():
    parser = argparse.ArgumentParser(
        description="Per-PDF OCR import counts using the real pipeline."
    )
    parser.add_argument(
        "--pdf-dir",
        required=True,
        help="Directory containing the statement PDFs (e.g. ~/statement_23_pdfs).",
    )
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_dir.is_dir():
        raise SystemExit(f"--pdf-dir {pdf_dir} is not a directory")

    pdf_paths = sorted(p for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf")
    if not pdf_paths:
        raise SystemExit(f"No .pdf files found in {pdf_dir}")

    base = Path(app.root_path)
    inbox_dir = base / "imports_inbox"
    statements_dir = base / "uploads" / "statements"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    results = []
    grand_total = 0

    with app.app_context():
        for pdf in pdf_paths:
            print(f"\n=== Processing single PDF: {pdf.name} ===")

            # 1) Reset DB for this one file
            print("  -> Dropping and recreating all DB tables for this run...")
            db.drop_all()
            db.create_all()

            # 2) Clean input/statement text dirs
            print(f"  -> Cleaning files in {inbox_dir} and {statements_dir} ...")
            clean_dir_files(inbox_dir)
            clean_dir_files(statements_dir)

            # 3) Copy this PDF into imports_inbox/
            dest = inbox_dir / pdf.name
            shutil.copy2(pdf, dest)
            print(f"  -> Copied {pdf.name} -> {dest}")

            # 4) Run your real OCR+import pipeline on this single file
            print("  -> Running process_uploaded_statement_files() ...")
            stats = process_uploaded_statement_files(
                uploads_dir=inbox_dir,
                statements_dir=statements_dir,
                db_session=db.session,
                Transaction=Transaction,
            )

            added = int(stats.get("added_transactions", 0))
            cand = int(stats.get("candidate_lines", 0))
            stmt_rows = int(stats.get("statement_rows", 0))

            # 5) Double-check via DB query
            db_count = db.session.query(func.count(Transaction.id)).scalar() or 0

            print("  -> Stats for this file:")
            print(f"     candidate_lines  : {cand}")
            print(f"     statement_rows   : {stmt_rows}")
            print(f"     added_transactions (stats): {added}")
            print(f"     DB row count after import : {db_count}")

            results.append(
                {
                    "filename": pdf.name,
                    "candidate_lines": cand,
                    "statement_rows": stmt_rows,
                    "added_transactions": added,
                    "db_rows": db_count,
                }
            )
            grand_total += added

    # 6) Final summary
    print("\n================= PER-FILE SUMMARY =================")
    print(
        f"{'Filename':45s}  {'cand_lines':>10s}  {'stmt_rows':>10s}  "
        f"{'added_tx':>9s}  {'db_rows':>7s}"
    )
    print("-" * 90)
    for r in results:
        print(
            f"{r['filename'][:45]:45s}  "
            f"{r['candidate_lines']:10d}  "
            f"{r['statement_rows']:10d}  "
            f"{r['added_transactions']:9d}  "
            f"{r['db_rows']:7d}"
        )

    print("\n================= GRAND TOTAL =================")
    print(f"Sum of added_transactions across all PDFs: {grand_total}")


if __name__ == "__main__":
    main()
