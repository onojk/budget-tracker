import re
import os
from pathlib import Path
from datetime import datetime


def _parse_ocr_text_file(path, default_source):
    '''
    Generic OCR parser:

    - Reads the file as plain text
    - For each line:
        * find first date-like token (YYYY-MM-DD or MM/DD/YYYY or MM/DD/YY)
        * find last amount-like token (-1322.15, $1,234.56, etc.)
        * everything between date and amount is the description
    - Returns a list of dicts in the format expected by app.py
    '''
    rows = []
    try:
        raw = Path(path).read_text(errors="ignore")
    except Exception:
        return rows

    # Date token patterns
    date_re = re.compile(
        r'^\d{4}-\d{2}-\d{2}$'          # 2025-11-29
        r'|^\d{1,2}/\d{1,2}/\d{2,4}$'   # 11/29/2025 or 11/29/25
    )

    # Amount token pattern
    amount_re = re.compile(r'^[-+]?\$?\d[\d,]*\.\d{2}$')

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue

        tokens = s.split()
        if len(tokens) < 3:
            continue

        # ---------- find first date token ----------
        date_idx = None
        for i, t in enumerate(tokens):
            if date_re.match(t):
                date_idx = i
                break
        if date_idx is None:
            continue

        # ---------- find last amount token ----------
        amount_idx = None
        for i in range(len(tokens) - 1, -1, -1):
            if amount_re.match(tokens[i]):
                amount_idx = i
                break
        if amount_idx is None or amount_idx <= date_idx:
            continue

        # ---------- normalize date ----------
        raw_date = tokens[date_idx]
        try:
            if '-' in raw_date:
                dt = datetime.strptime(raw_date, '%Y-%m-%d')
            else:
                # MM/DD/YYYY or MM/DD/YY
                month, day, year = raw_date.split('/')
                if len(year) == 2:
                    year = '20' + year
                dt = datetime.strptime(f'{month}/{day}/{year}', '%m/%d/%Y')
            date_str = dt.date().isoformat()
        except Exception:
            continue

        # ---------- description ----------
        desc = " ".join(tokens[date_idx + 1:amount_idx]).strip()
        if not desc:
            continue

        # ---------- amount ----------
        amt_raw = tokens[amount_idx]
        try:
            amt_clean = amt_raw.replace(',', '').replace('$', '')
            amount = float(amt_clean)
        except Exception:
            continue

        direction = 'debit' if amount < 0 else 'credit'

        rows.append({
            'Date': date_str,
            'Amount': abs(amount),          # sign handled via Direction
            'Direction': direction,
            'Source': default_source,
            'Account': '',
            'Merchant': desc,
            'Description': desc,
            'Category': '',
            'Notes': f'from {os.path.basename(path)}',
        })

    return rows


def process_screenshot_files(file_paths):
    '''
    Process OCR text files from account screenshots.
    '''
    rows = []
    for p in file_paths:
        rows.extend(_parse_ocr_text_file(p, 'Screenshot OCR'))
    return rows


def process_statement_files(file_paths):
    '''
    Process OCR text files from full statements.
    '''
    rows = []
    for p in file_paths:
        rows.extend(_parse_ocr_text_file(p, 'Statement OCR'))
    return rows
