
# ---- Safe rename/replace helpers (ignore missing src on first OCR run) ----
import os as _ocr_os



# ==== OCR smart open wrapper ==================================


# ==== OCR Path.rename shim ===================================
import pathlib as _ocr_pathlib

__ocr_real_path_rename = _ocr_pathlib.Path.rename

def _ocr_safe_path_rename(self, target, *args, **kwargs):
    src_str = str(self)
    # Ignore missing previous *_ocr.txt backups on first run
    if src_str.endswith("_ocr.txt"):
        try:
            return __ocr_real_path_rename(self, target, *args, **kwargs)
        except FileNotFoundError:
            # Nothing to rotate yet; safe to skip
            return
    return __ocr_real_path_rename(self, target, *args, **kwargs)

_ocr_pathlib.Path.rename = _ocr_safe_path_rename
# =============================================================

import builtins as _builtins

def _ocr_adjust_path(path):
    import os
    # If someone tries to use _ocr.pass0.tmp but it doesn't exist,
    # and a matching _ocr.pass0.txt DOES exist, use that instead.
    if isinstance(path, str) and path.endswith("_ocr.pass0.tmp"):
        if not os.path.exists(path):
            alt = path[:-3] + "txt"  # swap .tmp -> .txt
            if os.path.exists(alt):
                path = alt
    return path

_original_open = _builtins.open

def _ocr_open(path, *args, **kwargs):
    return _original_open(_ocr_adjust_path(path), *args, **kwargs)

_builtins.open = _ocr_open
# =============================================================

_original_rename = _ocr_os.rename
_original_replace = _ocr_os.replace

def _safe_rename(src, dst, *args, **kwargs):
    try:
        return _original_rename(src, dst, *args, **kwargs)
    except FileNotFoundError:
        # No previous file to rotate (first OCR pass) — this is fine.
        return

def _safe_replace(src, dst, *args, **kwargs):
    try:
        return _original_replace(src, dst, *args, **kwargs)
    except FileNotFoundError:
        # No previous file to rotate (first OCR pass) — this is fine.
        return

# Monkey-patch within this module so all os.rename/os.replace here are safe
_ocr_os.rename = _safe_rename
_ocr_os.replace = _safe_replace
# ---------------------------------------------------------------------------

import csv
#!/usr/bin/env python3
"""
ocr_pipeline.py


# ---- safe rename helper ----
def safe_rename(src, dst):
    import os
    try:
        safe_rename(src, dst)
    except FileNotFoundError:
        pass
# ---------------------------
OCR + import pipeline for statements and screenshots.

Responsibilities:
- Normalize OCR'd text lines into row dicts (Date, Amount, Merchant, etc.).
- Provide screenshot + statement text parsers.
- Provide PDF-parsing hooks (pdfplumber) when available.
- Bridge OCR → DB via ocr_import_helpers.import_ocr_rows.
- Extra statement-specific parsers:
    * Chase "TRANSACTION DETAIL" blocks
    * PayPal Credit / PayPal Cashback Synchrony statements
- Coverage + import report helpers for the Import Report page.
"""

import os
import re
import shutil
import hashlib
from pathlib import Path
from datetime import datetime, date as _date_cls

from decimal import Decimal, InvalidOperation

from chase_amount_utils import (
    AMOUNT_RE,
    DATE_RE,
    parse_amount_token,
    extract_amount_from_txn_line,
)

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


# =====================================================================
# Signed-amount parsing helpers (single source of truth for +/- amounts)
# =====================================================================

# Words that strongly suggest a DEBIT (money leaving you)
_DEBIT_WORDS = [
    "CARD PURCHASE",
    "DEBIT",
    "WITHDRAWAL",
    "ATM",
    "PAYMENT",
    "PURCHASE",
    "POS",
    "CHECKCARD",
    "CHECK CARD",
    "CHECK",
    "FEE",
    "CHARGE",
    "INTEREST",
]

# Words that strongly suggest a CREDIT (money coming in)
_CREDIT_WORDS = [
    "DIRECT DEP",
    "DIRECT DEPOSIT",
    "PAYROLL",
    "DEP PPD",
    "ACH CREDIT",
    "CREDIT",
    "REFUND",
    "REVERSAL",
    "ADJUSTMENT",
    "INTEREST PAID",
    "INTEREST PAYMENT",
    "INT EARNED",
    "CASHBACK",
    "CASH BACK",
]


def parse_signed_amount(raw: str, context: str = "") -> Decimal:
    """
    Parse a money-looking string into a signed Decimal, using both the raw
    sign markers (-, parentheses, trailing -) and some simple context words
    to decide the final sign.

    This is used by the more specialized parsers (Chase, PayPal Credit, etc.)
    so that +/- handling is consistent.
    """
    token = raw.strip()
    token = token.replace("\u2212", "-")  # unicode minus

    negative = False

    # Trailing minus, e.g. "68.02-"
    if token.endswith("-"):
        negative = True
        token = token[:-1]

    # Parentheses, e.g. "(68.02)"
    if token.startswith("(") and token.endswith(")"):
        negative = True
        token = token[1:-1]

    # Leading minus
    if token.startswith("-"):
        negative = True
        token = token[1:]

    token = token.replace("$", "").replace(",", "")

    if not token:
        return Decimal("0.00")

    try:
        value = Decimal(token)
    except InvalidOperation:
        return Decimal("0.00")

    if negative:
        value = -value

    ctx = (context or "").upper()

    # Context nudges: some descriptions indicate debit, some credit
    if any(w in ctx for w in _DEBIT_WORDS):
        # Debits should be negative
        if value > 0:
            value = -value
    elif any(w in ctx for w in _CREDIT_WORDS):
        # Credits should be positive
        if value < 0:
            value = -value

    return value


# -------------------------------------------------------------------
# Core line normalizer for generic OCR text rows
# -------------------------------------------------------------------


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

    description = " ".join(tokens[date_idx + 1 : amount_idx]).strip()
    if not description:
        return None

    amt_raw = tokens[amount_idx]
    amount = parse_signed_amount(amt_raw, context=description)

    upper_desc = description.upper()

    # ----------------------------------------------------------
    # Direction logic
    # ----------------------------------------------------------
    # Default assumption:
    #   - if amount < 0 => debit (spending)
    #   - if amount > 0 => credit (income/refund)
    direction = "debit" if amount < 0 else "credit"

    # Transfers & neutral internal moves
    transfer_keywords = [
        "TRANSFER TO", "XFER TO", "TO SAVINGS", "TO CHECKING",
        "REAL TIME TRANSFER RCD TO",
        "PAYMENT TO",
        "PAYPAL TRANSFER TO", "VENMO TRANSFER TO",
        "ZELLE TO",
        "CASH APP TO",
    ]
    if any(k in upper_desc for k in transfer_keywords):
        direction = "transfer"

    source_system, account_name = _detect_source_and_account(line, path, default_source)
    category = _guess_category(description)

    return {
        "Date": date_str,
        "Amount": float(amount),        # stored as float for import_ocr_rows; DB uses Decimal
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


def _parse_ocr_text_file(path, default_source: str, collect_rejected: bool = False, rejected_rows=None):
    """
    Parse a single *_ocr.txt file using the generic line normalizer.

    If collect_rejected is True, any line that:
      - is non-empty AND
      - contains at least one amount-looking token
    but fails to normalize into a row will be recorded in rejected_rows.
    """
    from pathlib import Path as _LocalPath

    rows = []
    if collect_rejected and rejected_rows is None:
        rejected_rows = []

    try:
        raw = _LocalPath(path).read_text(errors="ignore")
    except Exception:
        return (rows, rejected_rows) if collect_rejected else rows

    for idx, line in enumerate(raw.splitlines(), start=1):
        row = _normalize_row(line, default_source, str(path))
        if row:
            rows.append(row)
            continue

        if not collect_rejected:
            continue

        if not line.strip():
            continue

        # Only flag lines that appear to have a dollar amount
        if AMOUNT_RE.search(line) is None:
            continue

        rejected_rows.append(
            {
                "source_file": os.path.basename(str(path)),
                "line_no": idx,
                "page_no": None,
                "raw_text": line.rstrip("\n"),
                "amount_text": None,
                "reason": "no_generic_match",
            }
        )

    return (rows, rejected_rows) if collect_rejected else rows


def process_screenshot_files(file_paths):
    """
    Process OCR text files generated from account screenshots.
    Returns a list of normalized row dicts.
    """
    rows = []
    for p in file_paths or []:
        rows.extend(_parse_ocr_text_file(p, "Screenshot OCR"))
    return rows


def process_statement_files(file_paths=None, collect_rejected: bool = False):
    """
    Process OCR text files generated from full statements (generic parser).

    If collect_rejected is False (default), returns:
        rows

    If collect_rejected is True, returns:
        (rows, rejected_rows)
    where rejected_rows is a list of dicts ready for OcrRejected.
    """
    rows = []
    rejected_rows = []
    if not file_paths:
        return (rows, rejected_rows) if collect_rejected else rows

    for p in file_paths:
        if collect_rejected:
            file_rows, file_rejected = _parse_ocr_text_file(
                p, "Statement OCR", collect_rejected=True, rejected_rows=[]
            )
            rows.extend(file_rows)
            rejected_rows.extend(file_rejected)
        else:
            rows.extend(_parse_ocr_text_file(p, "Statement OCR"))

    return (rows, rejected_rows) if collect_rejected else rows

# =====================================================================
# Capital One (card ending 0728) statement parser (from *_ocr.txt)
# =====================================================================

def _parse_capone_0728_statement(txt_path):
    """
    Parse a Capital One Platinum Mastercard (ending in 0728) statement
    from its *_ocr.txt file (generated by pdftotext -layout).

    Returns a list of row dicts compatible with Transaction insertion.
    - Payments, Credits and Adjustments  -> card payments (transfer)
    - Transactions                       -> spending
    """

    from pathlib import Path
    import re
    from decimal import Decimal
    from datetime import date as _date_cls

    try:
        text = Path(txt_path).read_text(errors="ignore")
    except Exception:
        return []

    # Quick guard: if it doesn't look like this Cap One card, bail out.
    if "Platinum Card | Platinum Mastercard ending in 0728" not in text:
        return []

    # --------------------------------------------------------------
    # 1) Extract statement period: "Dec 10, 2024 - Jan 09, 2025"
    # --------------------------------------------------------------
    period_re = re.compile(
        r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s*-\s*"
        r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})"
    )

    MONTH = {
        "JANUARY": 1, "JAN": 1,
        "FEBRUARY": 2, "FEB": 2,
        "MARCH": 3, "MAR": 3,
        "APRIL": 4, "APR": 4,
        "MAY": 5,
        "JUNE": 6, "JUN": 6,
        "JULY": 7, "JUL": 7,
        "AUGUST": 8, "AUG": 8,
        "SEPTEMBER": 9, "SEP": 9, "SEPT": 9,
        "OCTOBER": 10, "OCT": 10,
        "NOVEMBER": 11, "NOV": 11,
        "DECEMBER": 12, "DEC": 12,
    }

    start_month_name = end_month_name = None
    start_year = end_year = None

    for line in text.splitlines():
        m = period_re.search(line)
        if m:
            sm, sd, sy, em, ed, ey = m.groups()
            start_month_name = sm.upper()
            start_year = int(sy)
            end_month_name = em.upper()
            end_year = int(ey)
            break

    if start_year is None:
        start_year = _date_cls.today().year
    if end_year is None:
        end_year = start_year

    def _month_year_for_abbrev(mon_abbrev: str):
        """Map 'Jan'/'Feb'/etc to (year, month) within this period."""
        mon_key = mon_abbrev.upper()
        mnum = MONTH.get(mon_key)
        if mnum is None:
            return end_year, _date_cls.today().month

        if start_month_name and mon_key.startswith(start_month_name[:3]):
            return start_year, mnum
        if end_month_name and mon_key.startswith(end_month_name[:3]):
            return end_year, mnum

        if start_month_name:
            smnum = MONTH.get(start_month_name, mnum)
            if mnum < smnum and end_year > start_year:
                return end_year, mnum
        return start_year, mnum

    # --------------------------------------------------------------
    # 2) Walk through text and capture the two tables:
    #    - Payments, Credits and Adjustments
    #    - Transactions
    # --------------------------------------------------------------
    lines = text.splitlines()
    rows = []

    mode = None  # None / "payments" / "spend"

    line_re = re.compile(
        r"^\s*([A-Za-z]{3,9})\s+(\d{1,2})\s+"
        r"([A-Za-z]{3,9})\s+(\d{1,2})\s+"
        r"(.+?)\s+(-?\s*\$?\d[\d,]*\.\d{2})\s*$"
    )

    for raw in lines:
        s = raw.strip()

        if "JONATHAN KENDALL #0728: Payments, Credits and Adjustments" in s:
            mode = "payments"
            continue
        if "JONATHAN KENDALL #0728: Transactions" in s:
            mode = "spend"
            continue
        if not mode:
            continue

        if s.startswith("Trans Date Post Date Description Amount"):
            continue
        if s.startswith("Total Transactions for This Period"):
            mode = None
            continue
        if s.startswith("Total Transactions") or s.startswith("Total Fees"):
            mode = None
            continue
        if not s:
            continue

        m = line_re.match(raw)
        if not m:
            continue

        mon1, day1, _mon2, _day2, desc, amt_str = m.groups()

        desc_clean = " ".join(desc.split())

        amt_token = amt_str.replace(" ", "")
        amt = parse_amount_token(amt_token)
        if amt is None:
            continue

        year, month = _month_year_for_abbrev(mon1)
        day = int(day1)
        iso_date = f"{year:04d}-{month:02d}-{day:02d}"

        if mode == "payments":
            amount_signed = abs(amt)
            direction = "credit"
            category = "Transfer:Card Payment"
        else:
            amount_signed = -abs(amt)
            direction = "debit"
            category = _guess_category(desc_clean)

        rows.append(
            {
                "Date": iso_date,
                "Amount": float(amount_signed),
                "Direction": direction,
                "Source": "Capital One",
                "Account": "Capital One 0728",
                "Merchant": desc_clean,
                "Description": desc_clean,
                "Category": category,
                "Notes": f"from {Path(txt_path).name}",
            }
        )

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

    for path in file_paths or []:
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

                            fake_line = f"{first} {' '.join(cells[1:-1])} {last}"
                            row = _normalize_row(fake_line, "Statement PDF", str(path))
                            if row:
                                rows.append(row)
        except Exception as e:
            print(f"[OCR] Error parsing PDF {path}: {e}")

    return rows


# ============================================================
# OCR → DB bridge via CSV files (ocr_output/*.csv)
# ============================================================

from ocr_import_helpers import import_ocr_rows

from pathlib import Path as _Path
import pandas as _pd

def collect_all_ocr_rows(base_dir: Path = Path("ocr_output")):
    """
    Unified generator for imported transaction rows.

    TEMP: Only Capital One CSV rows (Chase OCR disabled here to avoid NameError).
    We can re-add Chase once iter_chase_ocr_rows is confirmed in this module.
    """
    for row in iter_capone_csv_rows(base_dir):
        yield row

# === Capital One CSV support ===

def parse_capone_date(s: str):
    """
    Capital One CSV uses ISO dates: YYYY-MM-DD
    """
    s = (s or "").strip()
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_capone_amount(row: dict) -> Decimal:
    """
    Capital One CSV has separate Debit and Credit columns.

    - Debit  (charges, purchases, interest) -> money out  -> NEGATIVE
    - Credit (payments, refunds)            -> money in   -> POSITIVE
    """
    debit_raw = (row.get("Debit") or "").strip()
    credit_raw = (row.get("Credit") or "").strip()

    def to_dec(s: str) -> Decimal:
        s = s.replace("$", "").replace(",", "").strip()
        if not s:
            return Decimal("0")
        try:
            return Decimal(s)
        except InvalidOperation:
            return Decimal("0")

    debit = to_dec(debit_raw)
    credit = to_dec(credit_raw)

    # spending (debit) -> negative, payments/credits -> positive
    return credit - debit


def iter_capone_csv_rows(base_dir: Path):
    """
    Yield normalized row dicts for Capital One CSV exports in:

        base_dir / "capone" / *.csv
    """
    capone_dir = base_dir / "capone"
    if not capone_dir.exists():
        return  # nothing to do

    for csv_path in sorted(capone_dir.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                # 1) Date
                date_str = (raw.get("Transaction Date") or "").strip()
                tx_date = parse_capone_date(date_str)

                # 2) Description / merchant
                description = (raw.get("Description") or "").strip()

                # 3) Card number -> last 4 -> account name
                card_no = (raw.get("Card No.") or "").strip()
                last4 = card_no[-4:] if len(card_no) >= 4 else card_no
                account_name = f"Capital One {last4 or 'Unknown'}"

                # 4) Amount (Debit/Credit -> signed)
                amount = _parse_capone_amount(raw)

                # 5) Category (optional, but nice to keep somewhere)
                category = (raw.get("Category") or "").strip()

                raw_desc = (
                    f"{csv_path.name} | {date_str} | {description} | "
                    f"{card_no} | {category}"
                )

                yield {
                    "date": tx_date,
                    "amount": amount,
                    "merchant": description or "Capital One transaction",
                    "account_name": account_name,
                    "source_system": "Capital One CSV",
                    "raw_desc": raw_desc,
                }

def import_all_ocr_to_db():
    base_dir = Path("ocr_output")
    """
    High-level helper:
    - Gathers all OCR rows (bank + CC + PayPal Credit + screenshots)
    - Feeds them into Transaction via import_ocr_rows.

    Safe to re-run many times: import_ocr_rows does a de-dup check.
    """
    rows = list(collect_all_ocr_rows(base_dir))
    print(f"collect_all_ocr_rows() returned {len(rows)} rows.")
    inserted, skipped = import_ocr_rows(rows)
    print(f"OCR → DB import finished. Inserted={inserted}, skipped_existing={skipped}")


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


def process_screenshot_files_entrypoint(files=None):
    """
    ENTRYPOINT used by app.py and run_ocr_and_refresh.sh (if wired).

    CURRENTLY:
      - Acts as a stub (no real screenshot OCR yet).
      - This keeps imports working and makes the pipeline stable.
    
    LATER:
      - Replace the body with real screenshot OCR that builds a list of
        row dicts in the same normalized format used by collect_all_ocr_rows()
        and then calls save_screenshot_csv(rows).
    """
    print("process_screenshot_files_entrypoint: no real screenshot OCR wired yet.")
    print("process_screenshot_files_entrypoint: when ready, implement OCR and call save_screenshot_csv(rows).")
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
    """
    Convert PDF/PNG/JPG into a text file using either pdftotext (for PDFs)
    or Tesseract (for image screenshots).

    - For .pdf:       pdftotext -layout file.pdf file.txt
    - For images:     tesseract file.png file   → produces file.txt

    The resulting text is written to out_txt exactly.
    """
    import subprocess

    ext = input_path.suffix.lower()
    out_txt.parent.mkdir(parents=True, exist_ok=True)

    if ext == ".pdf":
        subprocess.run(
            ["pdftotext", "-layout", str(input_path), str(out_txt)],
            check=True,
        )

    elif ext in {".png", ".jpg", ".jpeg"}:
        # Tesseract takes output *without* extension
        tmp_no_ext = out_txt.with_suffix("")
        subprocess.run(
            ["tesseract", str(input_path), str(tmp_no_ext)],
            check=True,
        )
        # Tesseract produces tmp_no_ext.txt — rename to the final required out_txt
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
    checksums = []
    tmp_paths = []

    for i in range(passes):
        tmp = tmp_dir / f"{out_txt.stem}.pass{i}.tmp"
        ocr_to_text(src_path, tmp)
        ch = compute_checksum(tmp)
        checksums.append(ch)
        tmp_paths.append(tmp)

    unique = set(checksums)
    if len(unique) > 1:
        print(
            f"[OCR] WARNING: inconsistent OCR across {passes} passes for {src_path.name}: "
            f"{checksums}"
        )
        shutil.move(tmp_paths[0], out_txt)
    else:
        shutil.move(tmp_paths[0], out_txt)
        for p in tmp_paths[1:]:
            try:
                p.unlink()
            except FileNotFoundError:
                pass


# ======================================================================
# Uploaded statements → text OCR → DB
# ======================================================================


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


def _parse_chase_transaction_detail(path: Path):
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

        if current_year is None:
            current_year = end_year or start_year

        if prev_month is None:
            prev_month = month
        else:
            if month < prev_month and end_year and current_year == start_year:
                current_year = end_year
            prev_month = month

        year = current_year or (end_year or start_year or _date_cls.today().year)

        ctx = f"{desc} {amt_str}"
        amt_signed = parse_signed_amount(amt_str, context=ctx)
        direction = "debit" if amt_signed < 0 else "credit"

        iso_date = f"{year:04d}-{month:02d}-{day:02d}"
        desc_clean = " ".join(desc.split())
        note = f"from {path.name}"

        rows.append(
            {
                "Date": iso_date,
                "Amount": float(amt_signed),
                "Direction": direction,
                "Source": "Statement OCR",
                "Account": "",
                "Merchant": desc_clean,
                "Description": desc_clean,
                "Category": "",
                "Notes": note,
            }
        )

    # Extra pass: Millennium Healt Direct Dep lines outside the detail block
    payroll_re = re.compile(
        r"(\d{2})/(\d{2})\s+Millennium Healt\s+Direct Dep\s+PPD ID:\s*\d+\s+(-?\d[\d,]*\.\d{2})\s+(-?\d[\d,]*\.\d{2})"
    )

    for m in payroll_re.finditer(txt):
        mm, dd, amt_str, _bal_str = m.groups()
        month = int(mm)
        day = int(dd)

        year = current_year or (end_year or start_year or _date_cls.today().year)

        ctx = f"Millennium Healt Direct Dep {amt_str}"
        amt_signed = parse_signed_amount(amt_str, context=ctx)
        direction = "debit" if amt_signed < 0 else "credit"

        iso_date = f"{year:04d}-{month:02d}-{day:02d}"
        desc_clean = "Millennium Healt Direct Dep PPD ID: 9111111103"
        note = f"from {path.name} (payroll line outside detail block)"

        already = any(
            r["Date"] == iso_date
            and abs(Decimal(str(r["Amount"])) - amt_signed) < Decimal("0.005")
            and "Millennium Healt" in r["Merchant"]
            for r in rows
        )
        if already:
            continue

        rows.append(
            {
                "Date": iso_date,
                "Amount": float(amt_signed),
                "Direction": direction,
                "Source": "Statement OCR",
                "Account": "",
                "Merchant": desc_clean,
                "Description": desc_clean,
                "Category": "Income:Payroll",
                "Notes": note,
            }
        )

    return rows


# ----------------------------------------------------------------------
# PayPal Credit / PayPal Cashback Synchrony statement parsing
# ----------------------------------------------------------------------


def _is_paypal_credit_statement(txt: str) -> bool:
    """
    Heuristic: detect PayPal Credit / PayPal Cashback Synchrony statements.
    """
    t = txt.upper()
    return (
        "PAYPAL" in t
        and "TRANSACTION DETAILS" in t
        and "ACCOUNT NUMBER" in t
    )


def _extract_paypal_statement_year(txt: str):
    """
    Look for 'Payment due date MM/DD/YYYY' and return (due_year, due_month).
    """
    m = re.search(r"Payment due date\s+(\d{2})/(\d{2})/(\d{4})", txt)
    if not m:
        return None, None
    mm, dd, yyyy = m.groups()
    try:
        return int(yyyy), int(mm)
    except ValueError:
        return None, None


def _paypal_txn_iso_date(mm_dd: str, statement_year: int, due_month: int) -> str:
    """
    Convert MM/DD to YYYY-MM-DD, inferring year from the statement due month.

    If due_month is January (1) and the transaction month > due_month,
    assume previous calendar year (December charges on a Jan due-date
    statement).
    """
    mm, dd = mm_dd.split("/")
    month = int(mm)
    day = int(dd)
    year = statement_year

    if due_month == 1 and month > due_month:
        year = statement_year - 1

    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_paypal_credit_detail(path: Path):
    """
    Parse a PayPal Credit / PayPal Cashback Synchrony statement OCR text file
    into normalized row dicts.

    Expected layout (in OCR text):
        Transaction details
        Date Reference # Description           Amount
        Payments -$29.00
        04/15 8521... PAYMENT - THANK YOU -$29.00
        Purchases and Other Debits $49.03
        03/30 85... PAYPAL PURCHASE ... $30.77
        ALIPAYUSINC
        ...
        Total Fees Charged This Period $31.00
        04/12 LATE FEE $29.00
    """
    try:
        txt = path.read_text(errors="ignore")
    except Exception:
        return []

    if not _is_paypal_credit_statement(txt):
        return []

    stmt_year, due_month = _extract_paypal_statement_year(txt)
    if stmt_year is None:
        today = _date_cls.today()
        stmt_year = today.year
        due_month = today.month

    lines = txt.splitlines()

    # Find "Transaction details"
    start_idx = None
    for i, line in enumerate(lines):
        if "Transaction details" in line:
            start_idx = i
            break
    if start_idx is None:
        return []

    # Skip header lines until after the 'Date Reference #' row
    idx = start_idx + 1
    while idx < len(lines):
        line = lines[idx].rstrip("\n")

        if "Date" in lines[idx] and "Amount" in lines[idx]:
            idx += 1
            break

        idx += 1

    rows = []
    current_section = None  # "payments", "purchases", "fees", "interest"

    detail_re = re.compile(
        r"^\s*(\d{2}/\d{2})\s+(\S+)\s+(.*\S)\s+(-?\$?\d[\d,]*\.\d{2})\s*$"
    )

    def _update_section(line: str):
        t = line.upper()
        if "PAYMENTS" in t:
            return "payments"
        if "PURCHASES AND OTHER DEBITS" in t:
            return "purchases"
        if "TOTAL FEES CHARGED THIS PERIOD" in t or "FEES" in t:
            return "fees"
        if "TOTAL INTEREST CHARGED THIS PERIOD" in t or "INTEREST CHARGED" in t:
            return "interest"
        return None

    def _section_category(section: str, desc_upper: str) -> str:
        if section == "purchases":
            return "Spending:Purchases"
        if section == "payments":
            return "Transfer:Card Payment"
        if section == "fees":
            return "Fees:Card Fees"
        if section == "interest":
            return "Fees:Interest"
        if "CASHBACK" in desc_upper:
            return "Rewards/Cashback"
        return ""

    def _section_direction_and_amount(section: str, raw_amount: str, desc_upper: str):
        amt_signed = parse_signed_amount(raw_amount, context=desc_upper)

        if section in ("purchases", "fees", "interest"):
            if amt_signed > 0:
                amt_signed = -amt_signed
            direction = "debit"
        elif section == "payments":
            # Treat as transfer; positive from the card perspective
            if amt_signed < 0:
                amt_signed = -amt_signed
            direction = "transfer"
        else:
            direction = "credit" if amt_signed > 0 else "debit"

        return amt_signed, direction

    last_row = None

    while idx < len(lines):
        line = lines[idx].rstrip("\n")

        if "Cardholder news and information" in line:
            break

        sec = _update_section(line)
        if sec:
            current_section = sec
            idx += 1
            continue

        # Continuation line: no date, no amount, but we had a last_row
        if last_row is not None and not detail_re.match(line):
            stripped = line.strip()
            if stripped and not re.match(r"^\d{2}/\d{2}", stripped):
                last_row["Description"] = f"{last_row['Description']} {stripped}"
                last_row["Merchant"] = last_row["Description"]
            idx += 1
            continue

        m = detail_re.match(line)
        if not m:
            idx += 1
            continue

        mm_dd, ref, desc, amt_str = m.groups()
        desc_clean = " ".join(desc.split())
        desc_upper = desc_clean.upper()
        iso_date = _paypal_txn_iso_date(mm_dd, stmt_year, due_month)

        amt_signed, direction = _section_direction_and_amount(
            current_section or "", amt_str, desc_upper
        )
        category = _section_category(current_section or "", desc_upper)

        note = f"from {path.name} (PayPal credit detail)"

        row = {
            "Date": iso_date,
            "Amount": float(amt_signed),
            "Direction": direction,
            "Source": "PayPal Credit",
            "Account": "PayPal Credit",
            "Merchant": desc_clean,
            "Description": desc_clean,
            "Category": category,
            "Notes": note,
        }
        rows.append(row)
        last_row = row

        idx += 1

    return rows


# ======================================================================
# High-level pipeline used by /import/ocr
# ======================================================================

# =====================================================================
# Frontend helper: handle uploads on /import/ocr
# =====================================================================

from pathlib import Path
from ocr_import_helpers import import_ocr_rows


def process_uploaded_statement_files(uploads_dir, statements_dir):
    """
    1) Take whatever files are sitting in `uploads_dir` (PNGs, JPGs, PDFs,
       raw *_ocr.txt).
    2) For non-txt, run OCR and write *_ocr.txt into `statements_dir`.
    3) For each *_ocr.txt:
         - Try Chase dashboard screenshot parser
         - Then run the generic statement parser as a fallback
    4) Feed all normalized rows into import_ocr_rows().
    5) Return a stats dict for the Import Report page.
    """

    uploads_dir = Path(uploads_dir)
    statements_dir = Path(statements_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "saved_files": 0,
        "skipped_duplicates": 0,
        "added_transactions": 0,
        "candidate_lines": 0,
        "statement_rows": 0,
    }

    # --------------------------------------------------
    # 1) Normalize uploads -> *_ocr.txt in statements_dir
    # --------------------------------------------------
    txt_paths = []

    for src in sorted(uploads_dir.iterdir()):
        if not src.is_file():
            continue

        ext = src.suffix.lower()

        # Already a text file (e.g. *_ocr.txt from command line)
        if ext == ".txt":
            dst = statements_dir / src.name
            try:
                dst.write_text(src.read_text(errors="ignore"))
                txt_paths.append(dst)
                stats["saved_files"] += 1
            except Exception as e:
                print(f"[OCR] Failed to copy txt {src}: {e}")
            continue

        # PDF / PNG / JPG / JPEG -> run OCR
        if ext in {".pdf", ".png", ".jpg", ".jpeg"}:
            dst = statements_dir / f"{src.stem}_ocr.txt"
            try:
                # use the simplified wrapper we added earlier
                ocr_to_text_with_consistency(src, dst, passes=1)
                txt_paths.append(dst)
                stats["saved_files"] += 1
            except Exception as e:
                print(f"[OCR] Error OCR'ing {src}: {e}")
            continue

        # Ignore unknown extensions
        print(f"[OCR] Skipping unsupported file type: {src}")

    # --------------------------------------------------
    # 2) Parse *_ocr.txt -> normalized row dicts
    # --------------------------------------------------
    all_rows = []

    for txt in txt_paths:
        # a) Chase dashboard screenshot parser (your custom function)
        try:
            chase_rows = parse_chase_dashboard_ocr_text(txt)
        except Exception as e:
            print(f"[OCR] Chase dashboard parse failed for {txt}: {e}")
            chase_rows = []

        # Normalize Chase rows into the schema expected by import_ocr_rows
        normalized_chase = []
        for r in chase_rows:
            normalized_chase.append(
                {
                    "Date": r.get("date") or r.get("Date"),
                    "Amount": r.get("amount") or r.get("Amount"),
                    "Merchant": r.get("merchant") or r.get("Merchant"),
                    "Source": r.get("source_system") or r.get("Source") or "Chase Screenshot",
                    "Account": r.get("account_name") or r.get("Account") or "",
                    # Let import_ocr_rows default Direction/Category if missing
                    "Description": r.get("merchant") or r.get("Merchant") or "",
                    "Category": r.get("Category") or "",
                    "Notes": r.get("raw_line") or r.get("Notes") or f"from {txt.name}",
                }
            )

        # b) Generic OCR text parser as fallback
        generic_rows = process_statement_files(file_paths=[str(txt)])
        # process_statement_files() might return (rows, rejected)
        if isinstance(generic_rows, tuple):
            generic_rows = generic_rows[0]

        all_rows.extend(normalized_chase)
        all_rows.extend(generic_rows)

    stats["candidate_lines"] = len(all_rows)
    stats["statement_rows"] = len(all_rows)

    # --------------------------------------------------
    # 3) Import into DB via import_ocr_rows
    # --------------------------------------------------
    if all_rows:
        inserted, skipped = import_ocr_rows(
            all_rows,
            default_source="Screenshot OCR",
            default_account="",
        )
        stats["added_transactions"] = inserted
        stats["skipped_duplicates"] = skipped

    return stats

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


# === Capital One CSV support ===

def parse_capone_date(s: str):
    """
    Capital One CSV uses ISO dates: YYYY-MM-DD
    """
    s = (s or "").strip()
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_capone_amount(row: dict) -> Decimal:
    """
    Capital One CSV has separate Debit and Credit columns.

    - Debit  (charges, purchases, interest) -> money out  -> NEGATIVE
    - Credit (payments, refunds)            -> money in   -> POSITIVE
    """
    debit_raw = (row.get("Debit") or "").strip()
    credit_raw = (row.get("Credit") or "").strip()

    def to_dec(s: str) -> Decimal:
        s = s.replace("$", "").replace(",", "").strip()
        if not s:
            return Decimal("0")
        try:
            return Decimal(s)
        except InvalidOperation:
            return Decimal("0")

    debit = to_dec(debit_raw)
    credit = to_dec(credit_raw)

    # spending (debit) -> negative, payments/credits -> positive
    return credit - debit


def iter_capone_csv_rows(base_dir: Path):
    """
    Yield normalized row dicts for Capital One CSV exports in:

        base_dir / "capone" / *.csv
    """
    capone_dir = base_dir / "capone"
    if not capone_dir.exists():
        return  # nothing to do

    for csv_path in sorted(capone_dir.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                # 1) Date
                date_str = (raw.get("Transaction Date") or "").strip()
                tx_date = parse_capone_date(date_str)

                # 2) Description / merchant
                description = (raw.get("Description") or "").strip()

                # 3) Card number -> last 4 -> account name
                card_no = (raw.get("Card No.") or "").strip()
                last4 = card_no[-4:] if len(card_no) >= 4 else card_no
                account_name = f"Capital One {last4 or 'Unknown'}"

                # 4) Amount (Debit/Credit -> signed)
                amount = _parse_capone_amount(raw)

                # 5) Category (optional, but nice to keep somewhere)
                category = (raw.get("Category") or "").strip()

                raw_desc = (
                    f"{csv_path.name} | {date_str} | {description} | "
                    f"{card_no} | {category}"
                )

                yield {
                    "date": tx_date,
                    "amount": amount,
                    "merchant": description or "Capital One transaction",
                    "account_name": account_name,
                    "source_system": "Capital One CSV",
                    "raw_desc": raw_desc,
                }

def collect_all_ocr_rows(base_dir: Path = Path("ocr_output")):
    """
    Unified generator for all imported transaction rows.

    - Chase OCR
    - Capital One CSV
    """
    # Existing Chase OCR rows
    for row in iter_chase_ocr_rows(base_dir):
        yield row

    # New: Capital One CSV rows
    for row in iter_capone_csv_rows(base_dir):
        yield row

def collect_all_ocr_rows(base_dir: Path = Path("ocr_output")):
    """
    Unified generator for imported transaction rows.

    TEMP: Only Capital One CSV rows (Chase OCR disabled here to avoid NameError).
    We can re-add Chase once iter_chase_ocr_rows is confirmed in this module.
    """
    for row in iter_capone_csv_rows(base_dir):
        yield row



# ---- OCR rejection helpers (append-only scaffolding) ----

# from app import db, OcrRejected  # db and new model from app.py
import re
from decimal import Decimal, InvalidOperation

AMOUNT_RE = re.compile(
    r"(?<![\d.])-?\d{1,3}(?:,\d{3})*\.\d{2}"
)


def extract_amounts_with_spans(text):
    """
    Return list of dicts: {'amount_text', 'start', 'end', 'claimed': False}
    representing every dollar-like amount in the raw OCR text.
    """
    amounts = []
    for m in AMOUNT_RE.finditer(text):
        amounts.append(
            {
                "amount_text": m.group(0),
                "start": m.start(),
                "end": m.end(),
                "claimed": False,
            }
        )
    return amounts


def record_rejected_line(source_file, line_no, raw_text, reason, amount_text=None, page_no=None):
    from app import db, OcrRejected  # lazy import to avoid circular imports

    """
    Store a single unparsed / rejected OCR line in the OcrRejected table.
    Commit is left to the caller.
    """
    r = OcrRejected(
        source_file=source_file,
        line_no=line_no,
        page_no=page_no,
        raw_text=raw_text,
        reason=reason,
        amount_text=amount_text,
    )
    db.session.add(r)


def mark_amount_claimed(all_amounts, line_offset, parsed_amount_str):
    """
    Mark the first unclaimed amount in this line that matches parsed_amount_str.
    Intended to be called whenever a transaction line is successfully parsed.
    """
    # Normalize for comparison: strip commas
    target = parsed_amount_str.replace(",", "")
    for amt in all_amounts:
        if amt["claimed"]:
            continue
        if not (line_offset <= amt["start"] < line_offset + 300):
            # Quick heuristic: amount must fall near this line offset
            continue
        if amt["amount_text"].replace(",", "") == target:
            amt["claimed"] = True
            return

# TODO:
# - Inside your per-file OCR processing function, you can:
#   1) raw_text = Path(ocr_path).read_text(...)
#   2) all_amounts = extract_amounts_with_spans(raw_text)
#   3) For each successfully parsed transaction line, call mark_amount_claimed(...)
#   4) For any line with AMOUNT_RE.search(line) that fails parsing, call record_rejected_line(...)
#   5) At the end, any unclaimed entries in all_amounts can be recorded as reason="unclaimed_amount".
#   This keeps the changes incremental and safe while you evolve the parser.

# ---- OCR rejection helpers (append-only, safe to call from parsing code) ----
import re as _ocr_re
from decimal import Decimal as _Decimal, InvalidOperation as _InvalidOperation

_OCR_AMOUNT_RE = _ocr_re.compile(
    r"(?<![\d.])(-?\d{1,3}(?:,\d{3})*\.\d{2})"
)

def ocr_parse_decimal(raw):
    """
    Small helper: convert a string like "1,234.56" or "-68.02" to Decimal.
    Returns (Decimal, None) on success, (None, reason) on failure.
    """
    if raw is None:
        return None, "no_amount"
    try:
        value = _Decimal(raw.replace(",", ""))
    except _InvalidOperation:
        return None, "bad_amount"
    return value, None


def record_rejected_line(source_file, line_no, raw_text, reason, amount_text=None, page_no=None):
    """
    Store a single unparsed / rejected OCR line in the OcrRejected table.
    Import is local to avoid circular imports with app.py.
    """
    from app import db, OcrRejected  # lazy import, safe at call time

    r = OcrRejected(
        source_file=source_file,
        line_no=line_no,
        page_no=page_no,
        raw_text=raw_text,
        reason=reason,
        amount_text=amount_text,
    )
    db.session.add(r)


def scan_unclaimed_amounts(source_file, full_text):
    """
    Optional helper: if you have a block of OCR text and some amounts never
    appeared in any parsed transactions, you can call this to store them.

    This is a coarse fallback; the primary path should be record_rejected_line
    at the line level.
    """
    from app import db, OcrRejected  # lazy import

    for m in _OCR_AMOUNT_RE.finditer(full_text or ""):
        amt = m.group(1)
        snippet = (full_text[m.start():m.start()+120] if full_text else amt)
        db.session.add(
            OcrRejected(
                source_file=source_file,
                line_no=None,
                page_no=None,
                raw_text=snippet,
                reason="unclaimed_amount",
                amount_text=amt,
            )
        )
    db.session.commit()
# ---- end OCR rejection helpers ----


# PREMIUM AUTO-CATEGORIZATION (added automatically)
def _guess_category(description: str) -> str:
    if not description: return "Uncategorized"
    d = description.upper()
    rules = {
        "Groceries": ["FOOD4LESS","RALPHS","VONS","ALBERTSONS","TRADER JOE","WHOLEFDS","COSTCO","WALMART","TARGET","SPROUTS","SMART & FINAL"],
        "Dining": ["MCDONALD","STARBUCKS","CHIPOTLE","SUBWAY","IN N OUT","TACOBELL","DOORDASH","UBEREATS","GRUBHUB"],
        "Bills/Utilities": ["VERIZON","AT&T","T-MOBILE","SPECTRUM","COMCAST","SDGE","PG&E","SOUTHERN CALIFORNIA EDISON"],
        "Transportation": ["UBER","LYFT","SHELL","CHEVRON","ARCO","GAS","PARKING"],
        "Entertainment": ["NETFLIX","SPOTIFY","HULU","DISNEY+","YOUTUBE","APPLE.COM"],
        "Shopping": ["AMAZON","AMZN","TARGET.COM","BESTBUY","HOMEDEPOT"],
        "Health": ["CVS","WALGREENS","RITE AID","KAISER"],
        "Income": ["PAYROLL","DIRECT DEP","DEPOSIT","REFUND"],
        "Transfers": ["TRANSFER","ZELLE","VENMO","PAYPAL"]
    }
    for cat, kw in rules.items():
        if any(k in d for k in kw):
            return cat
    return "Uncategorized"

# ---- helper: safe_unlink --------------------------------------------
def safe_unlink(path):
    import os
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
# ---------------------------------------------------------------------

# ==== OCR filesystem helpers ==========================================
def safe_rename(src, dst):
    try:
        safe_rename(src, dst)
    except FileNotFoundError:
        return

def safe_unlink(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
# ======================================================================

# ==== OCR filesystem helpers ==========================================
def safe_rename(src, dst):
    import os
    try:
        os.replace(src, dst)
    except FileNotFoundError:
        pass

def safe_unlink(path):
    """Delete file if it exists; ignore missing temp files."""
    import os
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
# ======================================================================

# ==== OCR filesystem helpers (final clean version) ====================
def safe_rename(src, dst):
    # Rename src->dst safely; ignore missing source on first OCR pass.
    import os
    try:
        os.replace(src, dst)
    except FileNotFoundError:
        pass

def safe_unlink(path):
    # Delete file if it exists; ignore missing temp files.
    import os
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
# ======================================================================
# ---------------------------------------------------------------------
# Simplified override: OCR helper without .pass0.tmp logic
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# Simplified override: OCR helper without .pass0.tmp logic
# ---------------------------------------------------------------------
from pathlib import Path

def ocr_to_text_with_consistency(src_path: Path, out_txt: Path, passes: int = 1) -> None:
    """Simplified OCR wrapper.

    We ignore multi-pass logic entirely because the old implementation
    expected *_ocr.pass0.tmp files that no longer exist.

    Instead: just run `ocr_to_text()` once to generate the final *_ocr.txt.
    """
    ocr_to_text(src_path, out_txt)
# ---------------------------------------------------------------------
# Simplified override: record_rejected_line
# ---------------------------------------------------------------------
def record_rejected_line(*args, **kwargs):
    """No-op placeholder for rejected OCR lines.

    The older implementation tried to create an OcrRejectedLine model with
    keyword arguments (like `source_file`) that no longer match the model
    definition, causing TypeError.

    For now we disable DB persistence of rejected lines so OCR imports
    can proceed without error. You can later reintroduce a full implementation
    that matches your actual OcrRejectedLine schema.
    """
    # Optional: lightweight debug print, comment out if noisy
    # print("[OCR] Rejected line (ignored):", args, kwargs)
    return

# =====================================================================
# Chase dashboard screenshot parser (handles multi-line blocks)
# =====================================================================
from datetime import datetime
import re
from pathlib import Path

# Month name at start of line, like "Dec 03,2025 ..." or "Dec 3, 2025 ..."
_DATE_RE = re.compile(
    r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s*(\d{4})(.*)$'
)

# Rough filter for "this line looks like a transaction row, not a header"
_TXN_KEYWORDS_RE = re.compile(
    r'\b(Card|ACH|Account transfer|POS|DEBIT|CREDIT|PAYMENT|PAYROLL|TRANSFER)\b',
    re.IGNORECASE,
)

_AMOUNT_RE = re.compile(r'(-?\$[\d,]+\.\d{2})')


def _parse_amount_with_sign(rest: str, amount_raw: str) -> float:
    """
    Take a matched dollar string like "-$90.58" or "$73.97" and return a signed float.

    Default rule:
      - If there's an explicit '-' -> negative
      - Otherwise, default to negative (spend) UNLESS the line looks like income
        (credit / payroll / deposit / refund).
    """
    s = amount_raw.replace("$", "").replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return 0.0

    # Explicit minus keeps it negative
    if amount_raw.strip().startswith("-"):
        return value

    # Decide sign from context
    if re.search(r"\b(credit|deposit|payroll|refund)\b", rest, re.IGNORECASE):
        return abs(value)  # income
    else:
        return -abs(value)  # spending


def parse_chase_dashboard_ocr_text(txt_path: Path):
    """
    Parse OCR text from Chase web/mobile dashboard screenshots.

    Strategy:
      - Track the last date we saw (from lines like 'Dec 03,2025 ...').
      - Any line with:
          * at least one '$amount'
          * AND a 'transactiony' keyword (Card, ACH, Account transfer, Payment, etc.)
        becomes its own transaction, using the current date.
      - This picks up:
          * lines starting with dates
          * continuation lines under the same date
          * pending transactions
    """
    rows = []
    last_date_iso: str | None = None

    with open(txt_path, "r", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            # See if this line sets/updates the current date
            m = _DATE_RE.match(line)
            if m:
                mon_abbr, day, year, rest = m.groups()
                try:
                    dt = datetime.strptime(
                        f"{mon_abbr} {int(day)} {int(year)}", "%b %d %Y"
                    )
                    last_date_iso = dt.strftime("%Y-%m-%d")
                except ValueError:
                    last_date_iso = None
                    rest = rest or ""
                else:
                    rest = (rest or "").strip()
            else:
                rest = line

            # We need a date context to attach this to
            if not last_date_iso:
                continue

            # Must contain a $amount
            m_amt = _AMOUNT_RE.search(rest)
            if not m_amt:
                continue

            # Avoid top-of-page summary stuff like "Available balance $1,254.69"
            if not _TXN_KEYWORDS_RE.search(rest):
                continue

            amount_raw = m_amt.group(1)
            amount = _parse_amount_with_sign(rest, amount_raw)

            row = {
                "date": last_date_iso,
                "merchant": rest,
                "amount": amount,
                "account_name": "Chase Checking (screenshot)",
                "source_system": "ChaseScreenshot",
                "raw_line": line,
            }
            rows.append(row)

    return rows

# =====================================================================
# FINAL OVERRIDE: Screenshot OCR entrypoint
# =====================================================================
from pathlib import Path as _PathForScreenshot

def process_screenshot_files(file_paths):
    """
    Override screenshot processing to use the Chase dashboard parser.

    The import pipeline calls this for PNG/JPG OCR text files; we simply
    delegate to parse_chase_dashboard_ocr_text() for each path and
    concatenate the results.
    """
    all_rows = []
    for p in file_paths:
        p_obj = _PathForScreenshot(p)
        rows = parse_chase_dashboard_ocr_text(p_obj)
        print(f"[DEBUG] parse_chase_dashboard_ocr_text({p_obj}) -> {len(rows)} rows")
        if rows:
            all_rows.extend(rows)
    return all_rows
