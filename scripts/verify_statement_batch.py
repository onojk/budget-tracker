#!/usr/bin/env python
"""
verify_statement_batch.py

Run your full OCR → DB pipeline on a folder of statement PDFs
using the same logic as /import/ocr, and print:

- Stats from process_uploaded_statement_files()
- Total transactions in DB
- Date range
- Per-file candidate_lines vs db_rows (using build_import_report)

Usage:

    cd ~/budget_app
    python verify_statement_batch.py --pdf-dir /path/to/23_pdfs

By default this script:
    - DROPS and recreates all DB tables (fresh test)
    - CLEARS imports_inbox/ and uploads/statements/ files

You can disable DB reset with --no-reset-db if you want to run on top
of an existing DB state.
"""

import argparse
import shutil
from pathlib import Path

from sqlalchemy import func

from app import app, db, Transaction  # uses your existing config & models
from ocr_pipeline import process_uploaded_statement_files, build_import_report


def clean_dir_files(path: Path) -> None:
    """Delete all files under a directory tree, keep directories."""
    if not path.exists():
        return
    for p in path.rglob("*"):
        if p.is_file():
            p.unlink()


def main():
    parser = argparse.ArgumentParser(description="Verify OCR import from a batch of PDFs.")
    parser.add_argument(
        "--pdf-dir",
        required=True,
        help="Directory containing the statement PDFs (your 23 files).",
    )
    parser.add_argument(
        "--no-reset-db",
        action="store_true",
        help="Do NOT drop/recreate tables; import on top of existing DB.",
    )
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_dir.is_dir():
        raise SystemExit(f"--pdf-dir {pdf_dir} is not a directory")

    base = Path(app.root_path)
    inbox_dir = base / "imports_inbox"
    statements_dir = base / "uploads" / "statements"

    inbox_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        # 1) Reset DB if requested (default = reset)
        if not args.no_reset_db:
            print("==> Dropping and recreating all DB tables...")
            db.drop_all()
            db.create_all()
            print("    DB reset complete.")
        else:
            print("==> Skipping DB reset (--no-reset-db).")

        # 2) Clean imports_inbox/ and uploads/statements/ files
        print(f"==> Cleaning files in {inbox_dir} and {statements_dir} ...")
        clean_dir_files(inbox_dir)
        clean_dir_files(statements_dir)

        # 3) Copy PDFs into imports_inbox/
        pdf_paths = sorted(p for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf")
        if not pdf_paths:
            raise SystemExit(f"No .pdf files found in {pdf_dir}")

        print(f"==> Copying {len(pdf_paths)} PDFs into {inbox_dir} ...")
        for pdf in pdf_paths:
            dest = inbox_dir / pdf.name
            shutil.copy2(pdf, dest)
            print(f"    {pdf.name} -> {dest}")

        # 4) Run your real OCR+import pipeline
        print("==> Running process_uploaded_statement_files() ...")
        stats = process_uploaded_statement_files(
            uploads_dir=inbox_dir,
            statements_dir=statements_dir,
            db_session=db.session,
            Transaction=Transaction,
        )

        print("\n=== OCR Import Stats ===")
        for k in sorted(stats.keys()):
            print(f"{k}: {stats[k]}")

        # 5) Aggregate DB stats: total rows + date range
        total = db.session.query(func.count(Transaction.id)).scalar() or 0
        min_date = db.session.query(func.min(Transaction.date)).scalar()
        max_date = db.session.query(func.max(Transaction.date)).scalar()

        print("\n=== DB Transaction Summary ===")
        print(f"Total transactions: {total}")
        print(f"Date range: {min_date} to {max_date}")

        # 6) Detailed per-file report (candidate_lines vs db_rows)
        print("\n=== Per-file OCR Coverage (build_import_report) ===")
        report = build_import_report(statements_dir, db.session, Transaction)
        totals = report.get("totals", {})
        print(
            f"Totals: candidate_lines={totals.get('candidate_lines')}, "
            f"db_rows={totals.get('db_rows')}"
        )

        for f in report.get("files", []):
            fname = f.get("filename")
            cand = f.get("candidate_lines")
            db_rows = f.get("db_rows")
            print(f"  {fname}: candidate_lines={cand}, db_rows={db_rows}")


if __name__ == "__main__":
    main()
