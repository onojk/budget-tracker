#!/usr/bin/env python3
"""
End-to-end "refresh from OCR" runner.

Steps:
 1) Run OCR → DB import (bank, CC, PayPal Credit, screenshots).
 2) Run dedupe_transactions to mark intra-source dupes.
 3) Run reconcile_transfers to link transfers between accounts.

Safe to rerun whenever you add new PDFs/screenshots.
"""

import subprocess
import sys

from app import app
from ocr_pipeline import import_all_ocr_to_db


def _run_script(module_name):
    """Run a helper script like `dedupe_transactions.py` in-process via `python module_name.py`."""
    print(f"\n=== Running {module_name}.py ===")
    result = subprocess.run(
        [sys.executable, f"{module_name}.py"],
        check=False,
    )
    if result.returncode != 0:
        print(f"WARNING: {module_name}.py exited with code {result.returncode}")
    else:
        print(f"{module_name}.py completed OK.")


def main():
    # 1) OCR → DB
    print("=== STEP 1: Importing all OCR rows into DB ===")
    with app.app_context():
        import_all_ocr_to_db()

    # 2) Deduplicate intra-source duplicates and Screenshot OCR clones
    print("\n=== STEP 2: Deduping transactions ===")
    _run_script("dedupe_transactions")

    # 3) Reconcile transfers (PayPal/Venmo/bank mirrors)
    print("\n=== STEP 3: Reconciling transfers ===")
    _run_script("reconcile_transfers")

    print("\nAll refresh steps completed.")


if __name__ == "__main__":
    main()
