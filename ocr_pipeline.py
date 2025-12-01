from datetime import date as Date
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
import shutil
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
    - Derives signed Amount + Direction + Source/Account/Category.

    Convention:
      * spending  -> Amount < 0, Direction = "debit"
      * income    -> Amount > 0, Direction = "credit"
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

    # Amount token pattern (optional +/- in front, $ allowed)
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
        from datetime import datetime  # uses existing import, but safe here too
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

    upper_desc = description.upper()

    # ----------------------------------------------------------
    # Sign / Direction logic
    # ----------------------------------------------------------
    # Most rows on a checking statement are spending (debits).
    # Default = debit, unless description screams "income / refund".
    income_keywords = [
        # payroll / direct deposit
        "DIRECT DEP", "DIRECT DEPOSIT", "DIR DEP",
        "PAYROLL", "PAYROLL PPD", "DEP PPD", "PPD ID",
        "ACH CREDIT", "CREDIT", "DEPOSIT",
        # transfers IN
        "REAL TIME TRANSFER RCD FROM",
        "RCD FROM",
        "TRANSFER FROM",
        "XFER FROM",
        "VENMO PAYMENT FROM",
        "VENMO CASHOUT",
        "PAYPAL TRANSFER FROM",
        "ZELLE FROM",
        # adjustments / interest / refunds
        "REVERSAL", "REFUND", "ADJUSTMENT",
        "INTEREST PAID", "INTEREST PAYMENT", "INT EARNED",
    ]

    is_income = any(k in upper_desc for k in income_keywords)

    if amount < 0:
        # OCR already gave us a signed amount; treat negative as spending.
        direction = "debit"
    else:
        if is_income:
            # Positive inflow: keep it positive, mark as credit/income
            direction = "credit"
        else:
            # Default: this is spending → flip sign to negative
            amount = -amount
            direction = "debit"

    source_system, account_name = _detect_source_and_account(line, path, default_source)
    category = _guess_category(description)

    import os
    return {
        "Date": date_str,
        "Amount": amount,                # signed (net effect)
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


def ocr_to_text_with_consistency(src_path: Path, out_txt: Path, passes: int = 3) -> None:
    """
    Run OCR multiple times on the same file and compare the outputs.

    - If all passes produce identical text (by checksum), we keep the first pass.
    - If there is any mismatch, we log a WARNING and still keep the first pass'
      text as the canonical one, leaving the extra .passN files next to it for
      debugging.
    """
    src_path = Path(src_path)
    out_txt = Path(out_txt)

    tmp_dir = out_txt.parent
    checksums: list[str] = []
    tmp_paths: list[Path] = []

    for i in range(passes):
        tmp = tmp_dir / f"{out_txt.stem}.pass{i}.tmp"
        ocr_to_text(src_path, tmp)  # existing OCR function
        ch = compute_checksum(tmp)
        checksums.append(ch)
        tmp_paths.append(tmp)

    unique = set(checksums)
    if len(unique) > 1:
        print(
            f"[OCR] WARNING: inconsistent OCR across {passes} passes for {src_path.name}: "
            f"{checksums}"
        )
        # Keep all tmp files for inspection; use pass0 as canonical text
        shutil.move(tmp_paths[0], out_txt)
    else:
        # All identical: keep one and delete the rest
        shutil.move(tmp_paths[0], out_txt)
        for p in tmp_paths[1:]:
            try:
                p.unlink()
            except FileNotFoundError:
                pass



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
            # Run OCR multiple times and compare outputs for consistency
            ocr_to_text_with_consistency(f, out_txt, passes=3)
            saved_files += 1
            existing_checksums[ch] = out_txt.name
        except Exception as e:
            print(f"[OCR] Failed to OCR {f}: {e}")

    # Now call existing logic to parse all statement *_ocr.txt files.
    # Collect all *_ocr.txt files and feed them to process_statement_files.
    from datetime import date as _date_cls
    import re

    txt_paths = sorted(statements_dir.glob("*_ocr.txt"))
    added_count = 0

    # 1) Run the legacy parser first (handles newer 2025-style files).
    if txt_paths:
        try:
            process_statement_files(txt_paths)
        except TypeError:
            # Older signature that might do its own discovery.
            process_statement_files()

    # 2) Extra parsing pass for Chase "TRANSACTION DETAIL" tables (e.g. 2023–2024).
    # These look like:
    #    12/15          Card Purchase         12/13 Crownview Medical Group Coronado CA Card      -80.00        206.45
    #
    # We only insert rows that do NOT already exist (date+amount+merchant+notes match).

    MONTHS = {
        "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4,
        "MAY": 5, "JUNE": 6, "JULY": 7, "AUGUST": 8,
        "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
    }

    def _extract_statement_years(txt: str):
        """
        Find a line like: 'December 15, 2023 through January 16, 2024'
        and return (start_year, end_year). If not found, return (None, None).
        """
        pat = re.compile(
            r"([A-Za-z]+)\s+\d{1,2},\s+(\d{4})\s+through\s+([A-Za-z]+)\s+\d{1,2},\s+(\d{4})"
        )
        for line in txt.splitlines():
            m = pat.search(line)
            if m:
                _, y1, _, y2 = m.groups()
                try:
                    return int(y1), int(y2)
                except ValueError:
                    return None, None
        return None, None

    def _iter_transaction_lines(txt: str):
        """
        Yield lines inside the *start*transaction detail ... block.
        """
        in_block = False
        for line in txt.splitlines():
            if "*start*transaction detail" in line:
                in_block = True
                continue
            if in_block and line.strip().startswith("*end*"):
                break
            if in_block:
                yield line

    def _parse_chase_transaction_detail(path):
        """
        Parse a single _ocr.txt file in the Chase table layout and return a list
        of dicts compatible with the Transaction insertion logic.
        """
        try:
            txt = path.read_text(errors="ignore")
        except Exception:
            return []

        start_year, end_year = _extract_statement_years(txt)
        current_year = start_year or end_year
        prev_month = None

        # Example line:
        # 12/15          Card Purchase         12/13 Something Desc ...                      -80.00        206.45
        line_re = re.compile(
            r"^\s*(\d{2})/(\d{2})\s+(.+?)\s+(-?\d[\d,]*\.\d{2})\s+(-?\d[\d,]*\.\d{2})\s*$"
        )

        rows = []

        for line in _iter_transaction_lines(txt):
            m = line_re.match(line)
            if not m:
                continue

            mm, dd, desc, amt_str, _bal_str = m.groups()
            month = int(mm)
            day = int(dd)

            # Infer year across the statement period (e.g. Dec 2023 → Jan 2024).
            if current_year is None:
                current_year = end_year or start_year

            if prev_month is None:
                prev_month = month
            else:
                # If month "wraps around" (12 → 01), bump to end_year if available.
                if month < prev_month and end_year and current_year == start_year:
                    current_year = end_year
                prev_month = month

            year = current_year or (end_year or start_year or _date_cls.today().year)

            # Parse amount via shared signed-amount helper so debits are negative, credits positive.
            ctx = f"{desc} {amt_str}"
            amt_signed = parse_signed_amount(amt_str, context=ctx)
            direction = "debit" if amt_signed < 0 else "credit"

            iso_date = f"{year:04d}-{month:02d}-{day:02d}"
            desc_clean = " ".join(desc.split())
            note = f"from {path.name}"

            rows.append(
                {
                    "Date": iso_date,
                    "Amount": amt_signed,
                    "Direction": direction,
                    "Source": "Statement OCR",
                    "Account": "",
                    "Merchant": desc_clean,
                    "Description": desc_clean,
                    "Category": "",
                    "Notes": note,
                }
            )

        return rows

    # Insert parsed rows, skipping duplicates on (date, amount, merchant, notes).
    if txt_paths and db_session is not None and Transaction is not None:
        for path in txt_paths:
            extra_rows = _parse_chase_transaction_detail(path)
            for row in extra_rows:
                try:
                    tx_date = _date_cls.fromisoformat(row["Date"])
                except Exception:
                    tx_date = None

                amount = row["Amount"]
                merchant = row["Merchant"]
                notes = row["Notes"]

                # Duplicate check.
                existing = (
                    db_session.query(Transaction)
                    .filter_by(
                        date=tx_date,
                        amount=amount,
                        merchant=merchant,
                        notes=notes,
                    )
                    .first()
                )
                if existing:
                    continue

                tx = Transaction(
                    date=tx_date,
                    amount=amount,
                    direction=row["Direction"],
                    source_system=row["Source"],
                    account_name=row["Account"],
                    merchant=merchant,
                    description=row["Description"],
                    category=row["Category"],
                    notes=notes,
                )
                db_session.add(tx)
                added_count += 1

        db_session.commit()

    stats = {
        "saved_files": saved_files,
        "skipped_duplicates": skipped_files,
        "added_transactions": added_count,
    }

    # Global OCR coverage stats (for nicer banner + report)
    if db_session is not None and Transaction is not None:
        try:
            coverage = compute_ocr_coverage(
                Path(statements_dir), db_session, Transaction
            )
            stats.update(coverage)
        except Exception:
            # Never let coverage stats break the import
            pass

    return stats

# --- OCR coverage + report helpers -----------------------------------------

# Lines like:
#   "  12/18  Card Purchase  ...   -80.00         206.45"
_TX_LINE_RE = re.compile(
    r"^\s*(\d{2}/\d{2})\s+"
    r"(.*?)\s+"
    r"(-?\d[\d,]*\.\d{2})\s+"
    r"(-?\d[\d,]*\.\d{2})\s*$"
)


def _count_candidates_in_file(path: Path) -> int:
    """
    Count 'candidate' transaction lines in a single *_ocr.txt statement:
    - Only inside *start*transaction detail / *end*transaction detail blocks
    - Skips Beginning/Ending balance and 'Total ...' summary rows
    """
    text = path.read_text(errors="ignore")
    total = 0

    parts = text.split("*start*transaction detail")
    for part in parts[1:]:
        if "*end*transaction detail" not in part:
            continue
        body = part.split("*end*transaction detail", 1)[0]
        for line in body.splitlines():
            m = _TX_LINE_RE.match(line)
            if not m:
                continue
            desc = m.group(2).strip()
            if desc.startswith(("Beginning Balance", "Ending Balance", "Total ")):
                continue
            total += 1

    return total


def compute_ocr_coverage(statements_dir: Path, db_session, Transaction):
    """
    Global coverage stats:
      - total candidate transaction lines across *_ocr.txt
      - total rows in DB that originated from statement OCR
    """
    candidate_total = 0
    for path in Path(statements_dir).glob("*_ocr.txt"):
        candidate_total += _count_candidates_in_file(path)

    q = db_session.query(Transaction)
    stmt_rows = q.filter(
        (Transaction.source_system == "Statement OCR")
        | (Transaction.notes.ilike("from %"))
    ).count()

    return {
        "candidate_lines": candidate_total,
        "statement_rows": stmt_rows,
    }


def build_import_report(statements_dir: Path, db_session, Transaction):
    """
    Detailed per-file report for the Import Report page.

    Returns:
        {
          "files": [
             {
                "filename": "..._ocr.txt",
                "candidate_lines": 152,
                "db_rows": 179,
             },
             ...
          ],
          "totals": {
             "candidate_lines": ...,
             "db_rows": ...,
          },
        }
    """
    rows = []
    total_candidates = 0
    total_db_rows = 0

    for path in sorted(Path(statements_dir).glob("*_ocr.txt")):
        fname = path.name
        cand = _count_candidates_in_file(path)

        db_rows = (
            db_session.query(Transaction)
            .filter(Transaction.notes == f"from {fname}")
            .count()
        )

        total_candidates += cand
        total_db_rows += db_rows

        rows.append(
            {
                "filename": fname,
                "candidate_lines": cand,
                "db_rows": db_rows,
            }
        )

    return {
        "files": rows,
        "totals": {
            "candidate_lines": total_candidates,
            "db_rows": total_db_rows,
        },
    }

# =====================================================================
# Signed-amount parsing helpers (single source of truth for +/- amounts)
# =====================================================================
import re

# Words that strongly suggest a DEBIT (money leaving you)
DEBIT_HINT_WORDS = {
    "debit",
    "withdrawal",
    "purchase",
    "pos",
    "card purchase",
    "payment",
    "payment to",
    "auto payment",
    "ach debit",
    "transfer to",
    "money sent",
    "sent money",
    "cash out",
    "fee",
    "charge",
    "subscription",
    "bill pay",
}

# Words that strongly suggest a CREDIT (money coming in)
CREDIT_HINT_WORDS = {
    "credit",
    "deposit",
    "payout",
    "payouts",
    "settlement",
    "money received",
    "received money",
    "refund",
    "reversal",
    "ach credit",
    "transfer from",
    "payment received",
    "payroll",
    "direct deposit",
}

def parse_signed_amount(amount_str: str, context: str = "") -> float:
    """
    Parse an amount string like:
        "12.34", "-12.34", "12.34-", "(12.34)", "$12.34 DR", "12.34 CR"
    and return a signed float.
    `context` should be the full line/description to help infer debit vs credit.
    """
    if amount_str is None:
        raise ValueError("amount_str is None")

    s = str(amount_str).strip()
    ctx = (context or "").lower()

    # ---- 1) Detect obvious minus markers on the amount itself
    is_negative = False

    # Accounting parentheses: (123.45)
    if "(" in s and ")" in s:
        is_negative = True

    # Leading minus: -123.45
    if s.startswith("-"):
        is_negative = True

    # Trailing minus: 123.45-
    if s.endswith("-"):
        is_negative = True

    # Explicit DR / CR on amount itself
    s_lower = s.lower()
    if " dr" in s_lower or s_lower.endswith("dr"):
        is_negative = True
    if " cr" in s_lower or s_lower.endswith("cr"):
        # explicit credit wins over earlier negatives on the raw string
        is_negative = False

    # ---- 2) Strip decorations to isolate the numeric part
    cleaned = s
    cleaned = cleaned.replace("$", "").replace(",", "")
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = cleaned.replace("+", "")

    # Remove trailing minus AFTER we’ve noted it
    if cleaned.endswith("-"):
        cleaned = cleaned[:-1].strip()

    # Extract the first number we see
    import re as _re
    m = _re.search(r"[-+]?\d+(\.\d+)?", cleaned)
    if not m:
        raise ValueError(f"Could not parse amount from {amount_str!r}")

    raw_number = float(m.group(0))
    magnitude = abs(raw_number)  # strip sign from numeric part

    # ---- 3) Use surrounding text to infer debit vs credit
    ctx_debit = any(word in ctx for word in DEBIT_HINT_WORDS) or _re.search(r"\bdr\b", ctx) is not None
    ctx_credit = any(word in ctx for word in CREDIT_HINT_WORDS) or _re.search(r"\bcr\b", ctx) is not None

    if ctx_debit and not ctx_credit:
        is_negative = True
    elif ctx_credit and not ctx_debit:
        is_negative = False
    # If both or neither: fall back to whatever we inferred earlier

    signed = -magnitude if is_negative else magnitude
    # IMPORTANT: DO NOT call abs() on this later – the sign must be preserved.
    return round(signed, 2)

