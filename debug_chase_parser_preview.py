#!/usr/bin/env python3
import re
from pathlib import Path

from ocr_pipeline import _TX_LINE_RE  # reuse existing pattern if you like

# Files to inspect
TARGETS = [
    "uploads/statements/20240514-statements-9765-_ocr.txt",
    "uploads/statements/20240614-statements-9765-_ocr.txt",
]

# Copy of the Chase "TRANSACTION DETAIL" line regex,
# but we’ll use .search() instead of .match() for debugging.
CHASE_LINE_RE = re.compile(
    r"(\d{2})/(\d{2})\s+(.+?)\s+(-?\d[\d,]*\.\d{2})\s+(-?\d[\d,]*\.\d{2})"
)

def iter_transaction_lines(txt: str):
    """Mirror of _iter_transaction_lines, but without needing nested defs."""
    in_block = False
    for line in txt.splitlines():
        if "*start*transaction detail" in line:
            in_block = True
            continue
        if in_block and line.strip().startswith("*end*"):
            break
        if in_block:
            yield line

def main():
    base = Path(".")

    for rel in TARGETS:
        path = base / rel
        print(f"\n=== File: {rel} ===")
        if not path.exists():
            print("  (file not found)")
            continue

        txt = path.read_text(errors="ignore")

        # 1) Show if we even *have* a transaction-detail block
        has_block = "*start*transaction detail" in txt
        print(f"  Has *start*transaction detail block? {has_block}")

        # 2) Loop over candidate lines inside that block and see what matches
        count_candidates = 0
        count_matches = 0

        for line in iter_transaction_lines(txt):
            if re.search(r"\d{2}/\d{2}", line):
                count_candidates += 1
            m = CHASE_LINE_RE.search(line)
            if m:
                count_matches += 1
                mm, dd, desc, amt_str, bal_str = m.groups()
                print(f"  MATCH: {mm}/{dd} | {amt_str} | {bal_str} | {desc[:60]}")

        print(f"  Candidate date lines inside block : {count_candidates}")
        print(f"  Lines matching CHASE_LINE_RE       : {count_matches}")

        # 3) As a sanity check: explicitly search for Millennium lines anywhere in file
        print("\n  Millennium lines anywhere in file:")
        for ln in txt.splitlines():
            low = ln.lower()
            if "millennium" in low or "milenium" in low or "millenium" in low:
                print("   ->", ln.strip())

if __name__ == "__main__":
    main()
