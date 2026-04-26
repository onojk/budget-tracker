#!/usr/bin/env python3
"""
full_reset_and_reimport.py

1) Backup the SQLite database (if using sqlite:///).
2) Delete ALL Transaction rows.
3) Re-import all statement-based OCR transactions from uploads/statements/*_ocr.txt
   using ocr_pipeline.process_uploaded_statement_files.

Usage:
  source budget-env/bin/activate
  cd ~/budget_app
  python full_reset_and_reimport.py
"""

import shutil
import datetime
from pathlib import Path

from app import app, db, Transaction


def backup_sqlite_db():
    """If using sqlite:///, copy the DB file to a timestamped backup."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:///"):
        print(f"[backup] Non-sqlite URI ({uri!r}); skipping file backup.")
        return None

    db_path = Path(uri.replace("sqlite:///", ""))
    if not db_path.exists():
        print(f"[backup] SQLite file {db_path} does not exist; skipping backup.")
        return None

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_backup_{ts}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    print(f"[backup] Database backed up to {backup_path}")
    return backup_path


def main():
    from ocr_pipeline import process_uploaded_statement_files  # imported here to avoid any weird cycles

    with app.app_context():
        # 1) Backup
        backup_sqlite_db()

        # 2) Wipe Transaction table
        before = db.session.query(Transaction).count()
        print(f"[wipe] Transaction rows BEFORE delete: {before}")

        deleted = db.session.query(Transaction).delete()
        db.session.commit()

        after_delete = db.session.query(Transaction).count()
        print(f"[wipe] Transaction rows AFTER delete: {after_delete} (deleted {deleted})")

        # 3) Re-import from existing *_ocr.txt statement files
        uploads_dir = Path("uploads/statement_uploads_for_reimport")
        statements_dir = Path("uploads/statements")

        print(f"[reimport] Using uploads_dir={uploads_dir} (will be created if missing)")
        print(f"[reimport] Using statements_dir={statements_dir} (expects *_ocr.txt here)")

        stats = process_uploaded_statement_files(
            uploads_dir=uploads_dir,
            statements_dir=statements_dir,
            db_session=db.session,
            Transaction=Transaction,
            is_duplicate_transaction=None,
        )

        # Stats dict from process_uploaded_statement_files
        print("[reimport] process_uploaded_statement_files stats:")
        for k, v in stats.items():
            print(f"  - {k}: {v}")

        final_count = db.session.query(Transaction).count()
        print(f"[reimport] Transaction rows AFTER re-import: {final_count}")


if __name__ == "__main__":
    main()
