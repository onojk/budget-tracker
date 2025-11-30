import re
import os
from pathlib import Path
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
