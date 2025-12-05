#!/usr/bin/env python3
"""
bulk_import_from_downloads.py

Offline batch importer to rebuild the database from the canonical
statement set under:

    ~/Downloads/accounts/

Accounts covered (PDF-based):
- chase_9765    (Chase checking)
- chase_9383    (Chase savings)
- capitalone/platinum1
- capitalone/quicksilver1
- carecredit    (Synchrony CareCredit)
- paypal_CC     (PayPal credit card)
- citi_costco   (Citi Costco card)
- paypal_general (monthly PayPal reports)

Venmo CSVs and transfers_virtual are *listed* but not imported yet
(TODO: separate CSV pipeline).

This script does **not** touch ~/Downloads/accounts (read-only) and
does **not** drop tables. Run `hard_reset_budget_data.py` first if
you want a clean DB.
"""

from pathlib import Path

from app import app  # uses same app / db / models as your Flask server
from ocr_pipeline import process_statement_files  # existing OCR/import pipeline


ACCOUNTS_BASE = Path.home() / "Downloads" / "accounts"


def list_pdfs(folder: Path):
    """Return sorted list of PDF Paths in folder."""
    if not folder.exists():
        print(f"  [WARN] Folder missing: {folder}")
        return []
    pdfs = sorted(folder.glob("*.pdf"))
    print(f"  Found {len(pdfs)} PDF(s) in {folder}")
    return pdfs


def import_with_pipeline(label: str, pdfs):
    """
    Helper that calls your existing process_statement_files() once per file.
    If your function supports a list of paths, you can batch it instead.
    """
    if not pdfs:
        print(f"[SKIP] {label}: no PDFs.")
        return

    print(f"[IMPORT] {label}: {len(pdfs)} file(s).")
    for p in pdfs:
        print(f"    -> {p}")
        # If your process_statement_files expects a list, change to [str(p)] list
        process_statement_files(str(p))


def import_chase_9765():
    """Chase checking 9765 from chase_9765/ PDFs."""
    folder = ACCOUNTS_BASE / "chase_9765"
    pdfs = list_pdfs(folder)
    import_with_pipeline("Chase 9765 checking", pdfs)


def import_chase_9383():
    """Chase savings 9383 from chase_9383/ PDFs."""
    folder = ACCOUNTS_BASE / "chase_9383"
    pdfs = list_pdfs(folder)
    import_with_pipeline("Chase 9383 savings", pdfs)


def import_capitalone_platinum1():
    """Capital One Platinum (0728) from capitalone/platinum1 PDFs."""
    folder = ACCOUNTS_BASE / "capitalone" / "platinum1"
    pdfs = list_pdfs(folder)
    import_with_pipeline("Capital One Platinum1 (0728)", pdfs)


def import_capitalone_quicksilver1():
    """Capital One Quicksilver (7398) from capitalone/quicksilver1 PDFs."""
    folder = ACCOUNTS_BASE / "capitalone" / "quicksilver1"
    pdfs = list_pdfs(folder)
    import_with_pipeline("Capital One Quicksilver1 (7398)", pdfs)


def import_carecredit():
    """Synchrony CareCredit PDFs from carecredit/."""
    folder = ACCOUNTS_BASE / "carecredit"
    pdfs = list_pdfs(folder)
    import_with_pipeline("CareCredit", pdfs)


def import_paypal_cc():
    """PayPal Credit Card PDFs from paypal_CC/."""
    folder = ACCOUNTS_BASE / "paypal_CC"
    pdfs = list_pdfs(folder)
    import_with_pipeline("PayPal Credit Card", pdfs)


def import_citi_costco():
    """Citi Costco card (single PDF) from citi_costco/."""
    folder = ACCOUNTS_BASE / "citi_costco"
    pdfs = list_pdfs(folder)
    import_with_pipeline("Citi Costco", pdfs)


def import_paypal_general():
    """
    PayPal general monthly reports from paypal_general/.

    These are already wired into your pipeline (same as before),
    so we just feed the PDFs into process_statement_files.
    """
    folder = ACCOUNTS_BASE / "paypal_general"
    pdfs = list_pdfs(folder)
    import_with_pipeline("PayPal general monthly reports", pdfs)


def show_todo_accounts():
    """
    Just log what exists for Venmo & transfers_virtual so you can see
    they're discovered even though we don't import them yet.
    """
    venmo_dir = ACCOUNTS_BASE / "venmo"
    tv_dir = ACCOUNTS_BASE / "transfers_virtual"

    if venmo_dir.exists():
        venmo_files = sorted(venmo_dir.glob("*.csv"))
        print(f"[TODO] Venmo CSVs ({len(venmo_files)} file(s)) at {venmo_dir}")
        for f in venmo_files:
            print(f"    - {f.name}")
    else:
        print("[TODO] Venmo folder missing; nothing to do.")

    if tv_dir.exists():
        tv_files = sorted(tv_dir.glob("*"))
        print(f"[TODO] transfers_virtual ({len(tv_files)} placeholder file(s)) at {tv_dir}")
        for f in tv_files:
            print(f"    - {f.name}")
    else:
        print("[TODO] transfers_virtual folder missing; nothing to do.")


def main():
    print("==============================================")
    print("  Bulk import from ~/Downloads/accounts/      ")
    print("  (no frontend, uses existing OCR pipeline)   ")
    print("==============================================")
    print("")
    print("This will:")
    print("  - Read PDFs from each account subfolder")
    print("  - Send them through ocr_pipeline.process_statement_files()")
    print("  - Populate Transactions / related tables accordingly.")
    print("")
    print("It does NOT:")
    print("  - Drop or reset the database (you already ran hard_reset).")
    print("  - Import Venmo CSVs or transfers_virtual (TODO).")
    print("")

    confirm = input("Type YES (all caps) to proceed: ").strip()
    if confirm != "YES":
        print("Aborted; no imports performed.")
        return

    with app.app_context():
        # Order: Chase core -> cards/credit -> PayPal/CareCredit/etc.
        import_chase_9765()
        import_chase_9383()
        import_capitalone_platinum1()
        import_capitalone_quicksilver1()
        import_carecredit()
        import_paypal_cc()
        import_citi_costco()
        import_paypal_general()

        # Log non-imported items
        show_todo_accounts()

    print("")
    print("All done. Check the dashboard, reports, and OCR Rejected for results.")


if __name__ == "__main__":
    main()
