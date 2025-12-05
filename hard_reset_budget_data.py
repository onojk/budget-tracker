#!/usr/bin/env python3
"""
hard_reset_budget_data.py

Hard reset for budget_app:

- DROP and RECREATE all SQLAlchemy tables.
- Wipe selected artifact / temp directories under uploads/.

Does NOT touch:
- ~/Downloads/accounts   (your canonical statement PDFs/CSVs)
- uploads/statements     (existing OCR text files; safe to keep / re-use)
"""

import shutil
from pathlib import Path

from app import app, db


# --- CONFIG: which dirs count as "artifacts / temp" -------------------------

BASE_DIR = Path(__file__).resolve().parent

# Feel free to edit this list if you add more temp dirs later.
ARTIFACT_DIRS = [
    BASE_DIR / "uploads" / "tmp",
    BASE_DIR / "uploads" / "cache",
    BASE_DIR / "uploads" / "ocr_tmp",
    BASE_DIR / "uploads" / "import_tmp",
    BASE_DIR / "uploads" / "export_tmp",
    # NOTE: we intentionally do NOT include uploads/statements here.
]


def wipe_dir(path: Path) -> None:
    """
    Delete a directory if it exists, then recreate it empty.
    """
    if path.exists():
        if path.is_dir():
            print(f"  - Removing directory tree: {path}")
            shutil.rmtree(path, ignore_errors=True)
        else:
            print(f"  - {path} exists but is not a directory; deleting file.")
            path.unlink(missing_ok=True)

    print(f"  - Recreating empty dir: {path}")
    path.mkdir(parents=True, exist_ok=True)


def reset_database() -> None:
    """
    Drop and recreate all SQLAlchemy tables.
    """
    with app.app_context():
        print("Dropping all tables via db.drop_all() …")
        db.drop_all()
        db.session.commit()
        print("Recreating all tables via db.create_all() …")
        db.create_all()
        db.session.commit()
        print("Database reset complete (all tables empty).")


def main() -> None:
    print("======================================================")
    print("   HARD RESET: budget_app database + temp artifacts   ")
    print("======================================================")
    print("")
    print("This will:")
    print("  * DROP and RECREATE all database tables.")
    print("  * Wipe selected artifact/temp directories under uploads/.")
    print("")
    print("It will NOT touch:")
    print("  * ~/Downloads/accounts  (statement source-of-truth)")
    print("  * uploads/statements    (existing OCR text files)")
    print("")
    confirm = input("Type YES (all caps) to proceed: ").strip()
    if confirm != "YES":
        print("Aborted; no changes made.")
        return

    print("\n[1/2] Resetting database …")
    reset_database()

    print("\n[2/2] Cleaning artifact/temp directories …")
    for d in ARTIFACT_DIRS:
        wipe_dir(d)

    print("\nAll done!")
    print("- Fresh empty DB")
    print("- Temp/artifact dirs wiped and recreated")
    print("")
    print("Next steps (when you're ready):")
    print("  * Run your import pipeline to rebuild data from ~/Downloads/accounts/")
    print("    in a clean, consistent way.")
    print("")


if __name__ == "__main__":
    main()
