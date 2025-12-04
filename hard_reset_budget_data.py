#!/usr/bin/env python3
"""
hard_reset_budget_data.py

1) Backup the SQLite database (if using sqlite:///).
2) Wipe ALL Transaction rows.
3) Remove OCR artifacts and temp files so you can manually re-import later.

Artifacts removed:
  - uploads/statements/*_ocr.txt
  - uploads/statements/*.pass*.tmp
  - uploads/statement_uploads_for_reimport/*
  - ocr_output/*.csv, ocr_output/*.tmp
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


def wipe_transactions():
    """Delete ALL rows in the Transaction table."""
    before = db.session.query(Transaction).count()
    print(f"[wipe] Transaction rows BEFORE delete: {before}")

    deleted = db.session.query(Transaction).delete()
    db.session.commit()

    after = db.session.query(Transaction).count()
    print(f"[wipe] Transaction rows AFTER delete: {after} (deleted {deleted})")


def wipe_ocr_artifacts():
    """Remove OCR text/CSV/temp artifacts but leave original PDFs/images."""
    base = Path(__file__).parent

    patterns = [
        base / "uploads" / "statements" / "*_ocr.txt",
        base / "uploads" / "statements" / "*.pass*.tmp",
        base / "uploads" / "statement_uploads_for_reimport" / "*",
        base / "ocr_output" / "*.csv",
        base / "ocr_output" / "*.tmp",
    ]

    removed = 0
    for pattern in patterns:
        for p in pattern.parent.glob(pattern.name):
            if p.is_file():
                try:
                    p.unlink()
                    removed += 1
                    print(f"[files] removed {p}")
                except FileNotFoundError:
                    continue

    # Optionally clean up now-empty dirs (non-fatal if they are not empty)
    for d in [
        base / "uploads" / "statement_uploads_for_reimport",
        base / "ocr_output",
    ]:
        try:
            d.rmdir()
            print(f"[dirs] removed empty dir {d}")
        except OSError:
            # Not empty or doesn't exist; ignore
            pass

    print(f"[files] Total artifact files removed: {removed}")


def main():
    with app.app_context():
        backup_sqlite_db()
        wipe_transactions()
        wipe_ocr_artifacts()

        final = db.session.query(Transaction).count()
        print(f"[final] Transaction row count after hard reset: {final}")


if __name__ == "__main__":
    main()
