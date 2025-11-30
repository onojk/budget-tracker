import re
from typing import List, Dict

import pytesseract
from pdf2image import convert_from_path
from PIL import Image


def extract_text_from_image(path: str) -> str:
    img = Image.open(path)
    text = pytesseract.image_to_string(img)
    return text


def extract_text_from_pdf(path: str) -> str:
    pages = convert_from_path(path)
    text_chunks = []
    for pg in pages:
        text_chunks.append(pytesseract.image_to_string(pg))
    return "\n".join(text_chunks)


def extract_transactions(text: str) -> List[Dict]:
    """
    Very simple starter parser.

    Tries to catch lines like:
      11/25 WALMART -59.97
      2025-11-03 Starbucks 6.45
    """

    rows: List[Dict] = []
    for line in text.splitlines():
        ln = line.strip()
        if not ln:
            continue

        # date + amount pattern (you can enhance this later per bank)
        m = re.search(
            r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}).*?(?P<amount>-?\$?\d+\.\d{2})",
            ln,
        )
        if not m:
            continue

        dt = m.group("date")
        amt = m.group("amount").replace("$", "")
        merchant = ln  # crude default; refine later with custom templates

        direction = "debit"
        try:
            if float(amt) > 0:
                direction = "credit"
        except ValueError:
            pass

        row = {
            "Date": dt,
            "Source": "OCR",
            "Account": "OCR",
            "Direction": direction,
            "Amount": amt,
            "Merchant": merchant,
            "Description": merchant,
            "Category": "",
            "Notes": "Extracted via OCR",
        }
        rows.append(row)

    return rows


def process_ocr_files(files: List[str]) -> List[Dict]:
    all_rows: List[Dict] = []
    for path in files:
        path_lower = path.lower()
        if path_lower.endswith(".pdf"):
            text = extract_text_from_pdf(path)
        else:
            text = extract_text_from_image(path)

        tx_rows = extract_transactions(text)
        all_rows.extend(tx_rows)

    return all_rows
