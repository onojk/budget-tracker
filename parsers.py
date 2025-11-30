# parsers.py
import re
from datetime import datetime
from dateutil import parser as dateparser
from typing import List, Optional, Dict, Any

from models import Transaction, db

DATE_PATTERNS = [
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",        # 11/28/2025
    r"\b\d{4}-\d{2}-\d{2}\b",              # 2025-11-28
]

AMOUNT_PATTERN = r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d{2})"

def parse_amount(text: str) -> Optional[float]:
    match = re.search(AMOUNT_PATTERN, text.replace(" ", ""))
    if not match:
        return None
    # Remove commas
    amt_str = match.group(0).replace(",", "")
    try:
        return float(amt_str)
    except ValueError:
        return None

def parse_date(text: str) -> Optional[datetime]:
    # Try explicit patterns first
    for pat in DATE_PATTERNS:
        m = re.search(pat, text)
        if m:
            try:
                return dateparser.parse(m.group(0)).date()
            except Exception:
                pass
    # Fallback: try to parse anything that looks like a date
    try:
        return dateparser.parse(text, fuzzy=True).date()
    except Exception:
        return None

def guess_category(merchant: str, description: str) -> str:
    m = (merchant or "").lower()
    d = (description or "").lower()

    s = m + " " + d

    if any(x in s for x in ["doordash", "grubhub", "ubereats"]):
        return "Food/Delivery"
    if any(x in s for x in ["starbucks", "coffee"]):
        return "Food/Coffee"
    if any(x in s for x in ["walmart", "costco", "albertsons", "cvs"]):
        return "Groceries/General Merchandise"
    if any(x in s for x in ["verizon", "spectrum", "cox"]):
        return "Utilities/Phone-Internet"
    if "spotify" in s or "hulu" in s or "paramount" in s or "netflix" in s:
        return "Entertainment/Streaming"
    if "venmo" in s or "paypal" in s or "transfer" in s:
        return "Transfer"
    if "arco" in s or "ampm" in s or "shell" in s:
        return "Transportation/Gas"

    return "Uncategorized"

def create_transaction_from_line(
    line: str,
    source_system: str,
    account_name: str,
    import_method: str,
) -> Optional[Transaction]:
    """
    VERY generic line parser.
    You will tweak/extend this with Chase/BoA/Venmo-specific regexes over time.
    """
    stripped = " ".join(line.split())
    if not stripped:
        return None

    date = parse_date(stripped)
    amount = parse_amount(stripped)

    if not date or amount is None:
        # line probably isn't a transaction
        return None

    # crude merchant guess: remove date + amount, what remains is merchant/desc
    temp = stripped
    # remove first date substring
    for pat in DATE_PATTERNS:
        temp = re.sub(pat, "", temp, count=1)
    # remove first amount substring
    temp = re.sub(AMOUNT_PATTERN, "", temp, count=1)
    merchant_desc = temp.strip(" -•|\t")

    merchant = merchant_desc[:64]
    description = merchant_desc

    direction = "debit" if amount < 0 else "credit"
    category = guess_category(merchant, description)

    return Transaction(
        date=date,
        amount=amount,
        merchant=merchant,
        description=description,
        source_system=source_system,
        account_name=account_name,
        import_method=import_method,
        category=category,
        direction=direction,
        raw_text=line,
    )

def parse_text_block(
    text: str,
    source_system: str,
    account_name: str,
    import_method: str,
) -> List[Transaction]:
    transactions: List[Transaction] = []
    for raw_line in text.splitlines():
        tx = create_transaction_from_line(raw_line, source_system, account_name, import_method)
        if tx:
            transactions.append(tx)
    return transactions

def save_transactions(transactions: List[Transaction]) -> int:
    for tx in transactions:
        db.session.add(tx)
    db.session.commit()
    return len(transactions)
