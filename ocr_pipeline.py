import re

def get_statement_year(header_line: str, default_year: int | None = None) -> int:
    """
    Extract statement year from a header line such as:
        "December 15, 2023 through January 16, 2024"

    We deliberately choose the *latest* year on the line so that
    Dec→Jan statements are attributed to the January year.
    """
    import re as _re
    from datetime import date as _date

    years = [int(y) for y in _re.findall(r"\b(20\d{2})\b", header_line)]
    if years:
        return max(years)
    if default_year is not None:
        return default_year
    return _date.today().year


import os
from pathlib import Path
import hashlib
from datetime import datetime


# -------------------------------------------------------------------
# Helpers: bank/source + category detection
# -------------------------------------------------------------------


def _detect_source_and_account(line: str, path: str, default_source: str):
    """
    Guess the source_system (bank) and account_name based on the text
    and/or filename. Fallback to default_source if nothing matches.
    """
    text = f"{os.path.basename(path)} {line}".upper()

    source = default_source
    account = ""

    # Very rough heuristics; you can tune these as you see patterns.
    if "VENMO" in text:
        source = "Venmo"
        if "WALLET" in text:
            account = "Venmo Wallet"
    elif "PAYPAL" in text or "PP*" in text:
        source = "PayPal"
        if "ART" in text:
            account = "PayPal Onojk123 Art"
    elif "BANK OF AMERICA" in text or "B OF A" in text or "ADV PLUS" in text:
        source = "Bank of America"
        if "0205" in text:
            account = "Adv Plus 0205"
    elif "CHASE" in text or "PREMIER PLUS" in text:
        source = "Chase"
        if "9765" in text:
            account = "Premier Plus Ckg 9765"

    # Try to infer account from common 4-digit suffixes if still blank
    if not account:
        for suffix in ("0205", "5072", "9765", "3838", "9383"):
            if suffix in text:
                account = f"Acct *{suffix}"
                break

    return source, account


def _guess_category(description: str) -> str:
    """
    Very lightweight category guesser. Purely heuristic and safe to override
    later in the UI.
    """
    d = description.upper()

    if any(k in d for k in ("GAS", "CHEVRON", "ARCO", "SHELL", "COSTCO GAS")):
        return "Transportation/Gas"
    if any(k in d for k in ("UBER", "LYFT", "TAXI")):
        return "Transportation/Other"
    if any(k in d for k in ("WALMART", "WAL-MART", "TARGET", "COSTCO", "GROCERY", "GROCERIES")):
        return "Groceries/General Merchandise"
    if any(k in d for k in ("MCDONALD", "CARL'S JR", "CARLS JR", "BURGER KING", "TACO BELL", "KFC", "FAST FOOD")):
        return "Food/Fast Food"
    if any(k in d for k in ("STARBUCKS", "COFFEE", "CAFE")):
        return "Food/Coffee"
    if any(k in d for k in ("DOORDASH", "UBER EATS", "GRUBHUB", "POSTMATES")):
        return "Food/Delivery"
    if any(k in d for k in ("SPOTIFY", "NETFLIX", "HULU", "PARAMOUNT", "DISNEY+", "MAX ")):
        return "Entertainment/Streaming"
    if any(k in d for k in ("VERIZON", "T-MOBILE", "AT&T", "ATT MOBILITY")):
        return "Utilities/Phone"
    if any(k in d for k in ("ELECTRIC", "SDGE", "PG&E", "GAS & ELECTRIC")):
        return "Utilities/Energy"
    if any(k in d for k in ("INSURANCE", "PREMIUM")):
        return "Insurance"
    if any(k in d for k in ("TRANSFER", "ZELLE", "VENMO", "P2P", "PERSON-TO-PERSON")):
        return "Transfer/Person-to-person"

    return ""


def _normalize_row(line: str, default_source: str, path: str):
    """
    Core parser for a single OCR line:

    - Finds date-like token
    - Finds last amount-like token
    - Everything between = description/merchant
    - Derives Direction, Source, Account, Category
    """
    s = line.strip()
    if not s:
        return None

    tokens = s.split()
    if len(tokens) < 3:
        return None

    # Date token patterns
    date_re = re.compile(
        r"^\d{4}-\d{2}-\d{2}$"          # 2025-11-29
        r"|^\d{1,2}/\d{1,2}/\d{2,4}$"   # 11/29/2025 or 11/29/25
    )

    # Amount token pattern
    amount_re = re.compile(r"^[-+]?\$?\d[\d,]*\.\d{2}$")

    # --- find first date token ---
    date_idx = None
    for i, t in enumerate(tokens):
        if date_re.match(t):
            date_idx = i
            break
    if date_idx is None:
        return None

    # --- find last amount token ---
    amount_idx = None
    for i in range(len(tokens) - 1, -1, -1):
        if amount_re.match(tokens[i]):
            amount_idx = i
            break
    if amount_idx is None or amount_idx <= date_idx:
        return None

    raw_date = tokens[date_idx]
    try:
        if "-" in raw_date:
            dt = datetime.strptime(raw_date, "%Y-%m-%d")
        else:
            month, day, year = raw_date.split("/")
            if len(year) == 2:
                year = "20" + year
            dt = datetime.strptime(f"{month}/{day}/{year}", "%m/%d/%Y")
        date_str = dt.date().isoformat()
    except Exception:
        return None

    description = " ".join(tokens[date_idx + 1:amount_idx]).strip()
    if not description:
        return None

    amt_raw = tokens[amount_idx]
    try:
        amt_clean = amt_raw.replace(",", "").replace("$", "")
        amount = float(amt_clean)
    except Exception:
        return None

    direction = "debit" if amount < 0 else "credit"
    abs_amount = abs(amount)

    source_system, account_name = _detect_source_and_account(line, path, default_source)
    category = _guess_category(description)

    return {
        "Date": date_str,
        "Amount": abs_amount,
        "Direction": direction,
        "Source": source_system,
        "Account": account_name,
        "Merchant": description,
        "Description": description,
        "Category": category,
        "Notes": f"from {os.path.basename(path)}",
    }


# -------------------------------------------------------------------
# TEXT-BASED OCR PARSING (.txt from screenshots/statements)
# -------------------------------------------------------------------


def _parse_ocr_text_file(path, default_source):
    rows = []
    try:
        raw = Path(path).read_text(errors="ignore")
    except Exception:
        return rows

    for line in raw.splitlines():
        row = _normalize_row(line, default_source, str(path))
        if row:
            rows.append(row)
    return rows


def process_screenshot_files(file_paths):
    """
    Process OCR text files generated from account screenshots.
    """
    rows = []
    for p in file_paths:
        rows.extend(_parse_ocr_text_file(p, "Screenshot OCR"))
    return rows


def process_statement_files(file_paths):
    """
    Process OCR text files generated from full statements.
    """
    rows = []
    for p in file_paths:
        rows.extend(_parse_ocr_text_file(p, "Statement OCR"))
    return rows


# -------------------------------------------------------------------
# PDF STATEMENT PARSING (true table extraction with pdfplumber)
# -------------------------------------------------------------------


def process_statement_pdfs(file_paths):
    """
    Parse PDF statements directly using pdfplumber.

    Generic approach:
      - for each table cell row:
          * first cell: date-like?
          * last cell: amount-like?
          * middle cells: description
    """
    try:
        import pdfplumber
    except ImportError:
        print("[OCR] pdfplumber not installed; skipping PDF parsing.")
        return []

    rows = []
    date_re = re.compile(
        r"^\d{4}-\d{2}-\d{2}$"
        r"|^\d{1,2}/\d{1,2}/\d{2,4}$"
    )
    amount_re = re.compile(r"^[-+]?\$?\d[\d,]*\.\d{2}$")

    for path in file_paths:
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables() or []
                    for table in tables:
                        for raw_row in table:
                            if not raw_row:
                                continue
                            cells = [c.strip() for c in raw_row if c]
                            if len(cells) < 2:
                                continue

                            first = cells[0]
                            last = cells[-1]

                            if not date_re.match(first) or not amount_re.match(last):
                                continue

                            # Reuse the same normalization logic by faking a "line"
                            fake_line = f"{first} {' '.join(cells[1:-1])} {last}"
                            row = _normalize_row(fake_line, "Statement PDF", str(path))
                            if row:
                                rows.append(row)
        except Exception as e:
            print(f"[OCR] Error parsing PDF {path}: {e}")

    return rows

# ============================================================
# OCR → DB bridge
# ============================================================

from ocr_import_helpers import import_ocr_rows


def collect_all_ocr_rows():
    """
    Collect ALL OCR rows from:
    - Screenshot-based flows
    - Cap One / Chase / PayPal Credit / other card PDFs
    - Any other statement PDFs you support

    This function MUST return a list of dicts in the format
    expected by ocr_import_helpers.import_ocr_rows().

    PSEUDOCODE / TEMPLATE — replace with your real calls:
        rows = []
        rows.extend(collect_bank_screenshot_rows())
        rows.extend(collect_capone_pdf_rows())
        rows.extend(collect_chase_pdf_rows())
        rows.extend(collect_paypal_credit_pdf_rows())
        ...
        return rows
    """
    rows = []

    # TODO: plug in your real functions here.
    # Example shape:

    # from your_capone_module import parse_capone_pdfs
    # rows.extend(parse_capone_pdfs("/path/to/capone/pdfs"))

    # from your_screenshot_module import parse_new_screenshots
    # rows.extend(parse_new_screenshots("/path/to/screenshot/folder"))

    return rows


def import_all_ocr_to_db():
    """
    High-level helper:
    - Gathers all OCR rows (bank + CC + PayPal Credit + screenshots)
    - Feeds them into Transaction via import_ocr_rows.

    Safe to re-run many times: import_ocr_rows does a de-dup check.
    """
    rows = collect_all_ocr_rows()
    print(f"collect_all_ocr_rows() returned {len(rows)} rows.")
    inserted, skipped = import_ocr_rows(rows)
    print(f"OCR → DB import finished. Inserted={inserted}, skipped_existing={skipped}")

# ============================================================
# New implementation of collect_all_ocr_rows using OCR CSVs
# ============================================================

from pathlib import Path as _Path
import pandas as _pd

def collect_all_ocr_rows():
    """
    Collect ALL OCR rows from CSVs under ./ocr_output.

    Expected:
    - Your OCR pipelines (screenshots, Cap One, Chase card, PayPal Credit, etc.)
      write one or more CSV files into:  <repo_root>/ocr_output/*.csv

    This loader:
      - walks that directory
      - reads each CSV with pandas
      - normalizes column names into the format expected by import_ocr_rows()
    """
    base_dir = _Path(__file__).parent / "ocr_output"
    rows = []

    if not base_dir.exists():
        print(f"collect_all_ocr_rows: no directory {base_dir}, nothing to import.")
        return rows

    csv_paths = sorted(base_dir.glob("*.csv"))
    if not csv_paths:
        print(f"collect_all_ocr_rows: no CSV files in {base_dir}, nothing to import.")
        return rows

    print(f"collect_all_ocr_rows: found {len(csv_paths)} CSV files under {base_dir}")

    for csv_path in csv_paths:
        print(f"  - loading {csv_path}")
        try:
            df = _pd.read_csv(csv_path)
        except Exception as e:
            print(f"    ! ERROR reading {csv_path}: {e}")
            continue

        cols_lower = {c.lower(): c for c in df.columns}

        def _get_val(row, *names, default=""):
            for name in names:
                key = name.lower()
                if key in cols_lower:
                    return row[cols_lower[key]]
            return default

        for _, r in df.iterrows():
            row = {
                "Date": _get_val(r, "date", "transaction_date", "posted_date", default=None),
                "Amount": _get_val(r, "amount", "amt"),
                "Merchant": _get_val(r, "merchant", "payee", "description"),
                "Source": _get_val(r, "source", "source_system", "institution", default="Screenshot OCR"),
                "Account": _get_val(r, "account", "account_name", "card_name", default=""),
                "Direction": _get_val(r, "direction", "type", default="debit"),
                "Description": _get_val(r, "description", "memo", "details", default=""),
                "Category": _get_val(r, "category", default=""),
                "Notes": _get_val(r, "notes", default=""),
            }
            rows.append(row)

    print(f"collect_all_ocr_rows: assembled {len(rows)} rows from OCR CSVs.")
    return rows

# ============================================================
# Screenshot OCR → CSV helper + entrypoint
# ============================================================
from pathlib import Path as _SSPath
import datetime as _dt
import pandas as _pd2

def save_screenshot_csv(rows, prefix="screenshots"):
    """
    Save a list of OCR row dicts as a CSV into ./ocr_output/.

    Expected row keys (but we don't enforce):
      Date, Amount, Merchant, Source, Account, Direction, Description, Category
    """
    outdir = _SSPath(__file__).parent / "ocr_output"
    outdir.mkdir(exist_ok=True)

    if not rows:
        print("save_screenshot_csv: no rows, skipping CSV write.")
        return None

    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    outpath = outdir / f"{prefix}_{ts}.csv"

    try:
        df = _pd2.DataFrame(rows)
        df.to_csv(outpath, index=False)
        print(f"save_screenshot_csv: wrote {len(rows)} rows to {outpath}")
    except Exception as e:
        print(f"save_screenshot_csv: ERROR writing CSV: {e}")
        return None

    return outpath


def process_screenshot_files(files=None):
    """
    ENTRYPOINT used by app.py and run_ocr_and_refresh.sh.

    CURRENTLY:
      - Acts as a stub (no real OCR yet).
      - This keeps imports working and makes the pipeline stable.
    
    LATER:
      - Replace the body with real screenshot OCR that builds a list of
        row dicts in the same normalized format used by collect_all_ocr_rows()
        and then calls save_screenshot_csv(rows).
    """
    print("process_screenshot_files: no real screenshot OCR wired yet.")
    print("process_screenshot_files: when ready, implement OCR and call save_screenshot_csv(rows).")
    rows = []  # TODO: fill with real OCR output later
    return rows


# ======================================================================
# Checksum + OCR helpers
# ======================================================================

def compute_checksum(path: Path) -> str:
    """Return md5 checksum of a file path."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def ocr_to_text(input_path: Path, out_txt: Path) -> None:
    """Convert PDF/PNG/JPG to a text file using pdftotext or Tesseract.

    - For .pdf: use `pdftotext -layout`
    - For images: run `tesseract`.
    """
    ext = input_path.suffix.lower()
    out_txt.parent.mkdir(parents=True, exist_ok=True)

    if ext == ".pdf":
        import subprocess
        subprocess.run(
            ["pdftotext", "-layout", str(input_path), str(out_txt)],
            check=True,
        )
    elif ext in {".png", ".jpg", ".jpeg"}:
        import subprocess
        # tesseract input output(without .txt)
        tmp_no_ext = out_txt.with_suffix("")
        subprocess.run(
            ["tesseract", str(input_path), str(tmp_no_ext)],
            check=True,
        )
        # tesseract creates tmp_no_ext.txt
        # ensure final name matches out_txt exactly
        gen_txt = tmp_no_ext.with_suffix(".txt")
        if gen_txt != out_txt:
            gen_txt.replace(out_txt)
    else:
        raise ValueError(f"Unsupported file type for OCR: {input_path}")


def process_uploaded_statement_files(
    uploads_dir: Path,
    statements_dir: Path,
    db_session=None,
    Transaction=None,
    is_duplicate_transaction=None,
):
    """High-level pipeline used by /import/ocr.

    Steps:
    - For each uploaded file in uploads_dir with extension pdf/png/jpg/jpeg:
      * compute checksum
      * if a file with that checksum already exists in statements_dir, skip it
      * otherwise OCR → *_ocr.txt in statements_dir
    - Then parse all *_ocr.txt files via existing statement-parse logic
      (delegated to process_statement_files).

    Returns a dict with stats for UI flash messages.
    """

    uploads_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    allowed_exts = {".pdf", ".png", ".jpg", ".jpeg"}

    # Build checksum index for already-processed files
    existing_checksums = {}
    for txt in statements_dir.glob("*_ocr.txt"):
        # Build checksum from source PDF/image if still present, or from text itself
        try:
            existing_checksums[compute_checksum(txt)] = txt.name
        except Exception:
            continue

    saved_files = 0
    skipped_files = 0

    for f in uploads_dir.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in allowed_exts:
            continue

        ch = compute_checksum(f)
        if ch in existing_checksums:
            skipped_files += 1
            continue

        # New file → generate OCR text with a name that embeds date or original stem
        out_txt = statements_dir / f"{f.stem}_ocr.txt"
        try:
            ocr_to_text(f, out_txt)
            saved_files += 1
            existing_checksums[ch] = out_txt.name
        except Exception as e:
            print(f"[OCR] Failed to OCR {f}: {e}")

    # Now call existing logic to parse all statement *_ocr.txt files.
    # Collect all *_ocr.txt files and feed them to process_statement_files.
    from datetime import date as _date_cls

    txt_paths = sorted(statements_dir.glob("*_ocr.txt"))
    added_count = 0

    if txt_paths:
        try:
            parsed = process_statement_files(txt_paths)
        except TypeError:
            # Legacy signature that likely does its own DB work.
            process_statement_files(txt_paths)
        else:
            # If the parser returns a list/tuple of dicts, optionally insert here.
            if isinstance(parsed, (list, tuple)):
                if db_session is not None and Transaction is not None:
                    for row in parsed:
                        if not isinstance(row, dict):
                            continue

                        d = row.get("Date")
                        tx_date = None
                        if d is not None:
                            try:
                                if isinstance(d, str):
                                    tx_date = _date_cls.fromisoformat(d)
                                else:
                                    tx_date = d
                            except Exception:
                                tx_date = None

                        amount = row.get("Amount")
                        if tx_date is None or amount is None:
                            continue

                        src_system = row.get("Source") or "Statement OCR"
                        merchant = row.get("Merchant") or ""

                        # Avoid inserting duplicates if this parser is run multiple times.
                        existing = (
                            db_session.query(Transaction)
                            .filter(
                                Transaction.date == tx_date,
                                Transaction.amount == amount,
                                Transaction.merchant == merchant,
                                Transaction.source_system == src_system,
                            )
                            .first()
                        )
                        if existing:
                            continue

                        tx = Transaction(
                            date=tx_date,
                            amount=amount,
                            direction=row.get("Direction"),
                            source_system=src_system,
                            account_name=row.get("Account") or "",
                            merchant=merchant,
                            description=row.get("Description") or "",
                            category=row.get("Category") or "",
                            notes=row.get("Notes") or "",
                        )
                        db_session.add(tx)
                        added_count += 1
                    db_session.commit()
            elif isinstance(parsed, int):
                added_count = parsed

    # Fallback generic parser for any Chase-like statements that might not be
    # handled by process_statement_files (e.g. older 2024 PDFs).
    if db_session is not None and Transaction is not None and txt_paths:
        import re
        DATE_LINE_RE = re.compile(
            r'^\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?\d[\d,]*\.\d{2})\s*$'
        )

        for txt in txt_paths:
            with txt.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = DATE_LINE_RE.match(line)
                    if not m:
                        continue
                    date_s, body, amount_s = m.groups()
                    try:
                        month, day, year = map(int, date_s.split("/"))
                        tx_date = _date_cls(year, month, day)
                    except Exception:
                        continue

                    try:
                        amount = float(amount_s.replace(",", ""))
                    except ValueError:
                        continue

                    # Skip if this transaction already exists (prevents dupes).
                    existing = (
                        db_session.query(Transaction)
                        .filter(
                            Transaction.date == tx_date,
                            Transaction.amount == amount,
                            Transaction.merchant == body,
                            Transaction.source_system == "Statement OCR",
                        )
                        .first()
                    )
                    if existing:
                        continue

                    tx = Transaction(
                        date=tx_date,
                        amount=amount,
                        direction="credit",
                        source_system="Statement OCR",
                        account_name="",
                        merchant=body,
                        description=body,
                        category="",
                        notes=f"from {txt.name}",
                    )
                    db_session.add(tx)
                    added_count += 1

        db_session.commit()

    return {
        "saved_files": saved_files,
        "skipped_duplicates": skipped_files,
        "added_transactions": added_count,
    }

