#!/usr/bin/env python3
"""
rebuild_from_downloads_statements.py

Rebuilds the *statements* working area and runs the OCR+import pipeline
the same way the frontend does:

1) Wipes uploads/statements (in this project only).
2) Copies all canonical account PDFs from:

       ~/Downloads/accounts/**

   into uploads/statements/.
3) Calls ocr_pipeline.process_statement_files(STATEMENTS_DIR).

Does NOT touch:
- ~/Downloads/accounts (read-only canonical base)
- Database schema/tables (use hard_reset_budget_data.py for that)
"""

import shutil
from pathlib import Path

from app import app  # same Flask app
from ocr_pipeline import process_statement_files  # same pipeline entrypoint


BASE_DIR = Path(__file__).resolve().parent
STATEMENTS_DIR = BASE_DIR / "uploads" / "statements"
ACCOUNTS_BASE = Path.home() / "Downloads" / "accounts"


def wipe_statements_dir():
    """Remove and recreate uploads/statements/."""
    if STATEMENTS_DIR.exists():
        print(f"[wipe] Removing old statements dir: {STATEMENTS_DIR}")
        shutil.rmtree(STATEMENTS_DIR, ignore_errors=True)
    print(f"[wipe] Recreating: {STATEMENTS_DIR}")
    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)


def copy_account_pdfs():
    """
    Copy all account PDFs into uploads/statements.

    We keep filenames as-is so later tools (like validators) still recognize patterns.
    """
    if not ACCOUNTS_BASE.exists():
        raise SystemExit(f"Accounts base not found: {ACCOUNTS_BASE}")

    count = 0
    print(f"[copy] Scanning {ACCOUNTS_BASE} for PDFs...")
    for path in ACCOUNTS_BASE.rglob("*.pdf"):
        # Skip the tar backup and checksum files if any accidentally match
        if path.name.endswith(".tar"):
            continue
        rel = path.relative_to(ACCOUNTS_BASE)
        dest = STATEMENTS_DIR / rel.name
        print(f"   - {rel}  ->  {dest.name}")
        shutil.copy2(path, dest)
        count += 1

    print(f"[copy] Copied {count} PDF(s) into {STATEMENTS_DIR}")
    if count == 0:
        print("[copy] WARNING: No PDFs found to import!")


def run_pipeline():
    """
    Run the same OCR/import pipeline the app uses,
    but pointed at uploads/statements.
    """
    print(f"[pipeline] Running process_statement_files on: {STATEMENTS_DIR}")
    with app.app_context():
        # Many versions of your pipeline expect a directory path.
        process_statement_files(str(STATEMENTS_DIR))
    print("[pipeline] Done.")


def main():
    print("==============================================")
    print("  Rebuild uploads/statements from Downloads   ")
    print("  and run OCR+import pipeline (no frontend)   ")
    print("==============================================")
    print()
    print(f"STATEMENTS_DIR: {STATEMENTS_DIR}")
    print(f"ACCOUNTS_BASE:  {ACCOUNTS_BASE}")
    print()
    confirm = input("Type YES (all caps) to proceed: ").strip()
    if confirm != "YES":
        print("Aborted.")
        return

    wipe_statements_dir()
    copy_account_pdfs()
    run_pipeline()

    print()
    print("All done.")
    print("Now start the Flask app and check:")
    print("  - Dashboard")
    print("  - Reports")
    print("  - OCR Rejected (after re-running populate_ocr_rejected.py if desired).")


if __name__ == "__main__":
    main()
