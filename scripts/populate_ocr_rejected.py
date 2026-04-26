#!/usr/bin/env python3
"""
populate_ocr_rejected.py

Scan all *_ocr.txt statement files and populate the OcrRejected table
with every non-blank line that does *not* look like a final transaction
amount line.

This does NOT touch the existing import pipeline. It is a post-pass that
ensures every bit of OCR text is represented somewhere:

- "Amount-line" transaction rows → stay in Transactions only
- All other lines (headers, summaries, continuations, noise) → OcrRejected
"""

from pathlib import Path
import re

from app import app, db, OcrRejected  # uses existing app/db/model


# Match a money amount at or near the END of the line, e.g.
#  123.45
#  1,234.56
#  -68.02
#  68.02-
AMOUNT_AT_END_RE = re.compile(
    r'(-?\d{1,3}(?:,\d{3})*\.\d{2}-?)\s*$'
)


def looks_like_transaction_amount_line(line: str) -> bool:
    """
    Very simple heuristic: if there's an amount-looking token at the end
    of the line, treat this as the "main transaction row" and *exclude*
    it from OcrRejected.

    Everything else gets logged as a rejected/non-transaction line.
    """
    s = line.strip()
    if not s:
        return False

    m = AMOUNT_AT_END_RE.search(s)
    if not m:
        return False

    # Require at least one digit *before* the matched amount as well,
    # so a bare "123.45" line with nothing else still counts.
    prefix = s[: m.start()].strip()
    if not prefix:
        # Still treat as a transaction line – it's just amount-only.
        return True

    return True


def iter_statement_ocr_files():
    """
    Yield (Path) for every *_ocr.txt in uploads/statements.
    Adjust here if your OCR files live somewhere else.
    """
    base = Path("uploads/statements")
    for path in sorted(base.glob("*_ocr.txt")):
        yield path


def rebuild_ocr_rejected():
    """
    Main entry: wipe OcrRejected and repopulate from OCR text files.
    """
    with app.app_context():
        print("Clearing existing OcrRejected rows…")
        db.session.query(OcrRejected).delete()
        db.session.commit()

        total_inserted = 0

        for path in iter_statement_ocr_files():
            print(f"Scanning {path} …")
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                print(f"  !! File missing: {path}")
                continue

            lines = text.splitlines()
            for line_no, raw in enumerate(lines, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue  # skip blank

                # If it looks like the primary transaction row (amount at end),
                # skip it; it's assumed to be covered by Transactions already.
                if looks_like_transaction_amount_line(stripped):
                    continue

                r = OcrRejected(
                    source_file=path.name,
                    line_no=line_no,
                    page_no=None,
                    raw_text=stripped,
                    reason="non_transaction_line",
                    amount_text=None,
                )
                db.session.add(r)
                total_inserted += 1

            db.session.commit()

        print(f"Done. Inserted {total_inserted} OcrRejected row(s).")


if __name__ == "__main__":
    rebuild_ocr_rejected()
