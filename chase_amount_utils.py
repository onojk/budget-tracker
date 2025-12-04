#!/usr/bin/env python3
"""
chase_amount_utils.py

Shared helpers for parsing Chase-style OCR statement lines:

- Robust money parsing (68.02-, (68.02), -68.02, $1,234.56, etc.)
- Shared AMOUNT_RE + DATE_RE
- Helper to extract the transaction AMOUNT from a TRANSACTION DETAIL line
  (i.e., second-to-last money token on the line, not the running balance).
"""

import re
from decimal import Decimal, InvalidOperation

# Matches values like:
#   68.02
#   $68.02
#   68.02-
#   $68.02-
#   (68.02)
#   $(68.02)
AMOUNT_RE = re.compile(r"\$?\(?-?\d[\d,]*\.\d{2}\)?-?")

# Lines that start with a date like 1/2, 01/02/24, etc.
DATE_RE = re.compile(r"^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")


def parse_amount_token(token: str) -> Decimal | None:
    """
    Parse a single money-looking token into a signed Decimal.

    Handles:
      - 68.02-
      - (68.02)
      - -68.02
      - $68.02-
      - $1,234.56
    """
    token = token.strip()
    token = token.replace("\u2212", "-")  # normalize unicode minus

    negative = False

    # Trailing minus, e.g. "68.02-"
    if token.endswith("-"):
        negative = True
        token = token[:-1]

    # Parentheses, e.g. "(68.02)"
    if token.startswith("(") and token.endswith(")"):
        negative = True
        token = token[1:-1]

    # Leading minus, e.g. "-68.02"
    if token.startswith("-"):
        negative = True
        token = token[1:]

    token = token.replace("$", "").replace(",", "")

    if not token:
        return None

    try:
        value = Decimal(token)
    except InvalidOperation:
        return None

    if negative and value > 0:
        value = -value

    return value


def extract_amount_from_txn_line(line: str) -> Decimal | None:
    """
    Given a TRANSACTION DETAIL line that starts with a date:

        DATE  DESCRIPTION...   AMOUNT   RUNNING BALANCE

    We return the AMOUNT, which is usually the SECOND-TO-LAST money token.

    If only one money token is present, we return that.
    If no money token is found or parsing fails, return None.
    """
    matches = list(AMOUNT_RE.finditer(line))
    if not matches:
        return None

    # If 2+ money fields, use SECOND-TO-LAST as txn amount.
    if len(matches) >= 2:
        amount_match = matches[-2]
    else:
        amount_match = matches[-1]

    text = amount_match.group(0)
    return parse_amount_token(text)
