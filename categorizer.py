# categorizer.py
"""
Simple rule-based auto-categorizer.

Usage:
    from categorizer import auto_category
    cat = auto_category(merchant, amount, notes)
"""

CATEGORY_RULES = {
    # Groceries
    "FOOD4LESS": "Groceries",
    "FOOD 4 LESS": "Groceries",
    "WAL-MART": "Groceries",
    "WALMART": "Groceries",
    "ALDI": "Groceries",
    "COSTCO": "Groceries",
    "SPROUTS": "Groceries",
    "RALPHS": "Groceries",
    "VONS": "Groceries",
    "TRADER JOE": "Groceries",

    # Bills / Utilities / Phone
    "VERIZON": "Bills",
    "T-MOBILE": "Bills",
    "SPECTRUM": "Bills",
    "SDGE": "Bills",
    "SAN DIEGO GAS": "Bills",
    "GEICO": "Insurance",

    # Subscriptions / Streaming
    "HULU": "Subscriptions",
    "NETFLIX": "Subscriptions",
    "SPOTIFY": "Subscriptions",
    "ESET": "Subscriptions",
    "ADOBE": "Subscriptions",

    # Dining / coffee
    "IN-N-OUT": "Dining",
    "MCDONALD": "Dining",
    "STARBUCKS": "Dining",
    "TACO BELL": "Dining",
    "PANDA EXPRESS": "Dining",
    "BURGER KING": "Dining",
    "PIZZA": "Dining",
    "CAFE": "Dining",
    "COFFEE": "Dining",

    # Pets
    "BANFIELD": "Pets",
    "PETCO": "Pets",
    "PETSMART": "Pets",

    # Car / Gas
    "ARCO": "Auto & Gas",
    "SHELL": "Auto & Gas",
    "CHEVRON": "Auto & Gas",
    "VALVOLINE": "Auto & Gas",
    "DMV": "Auto & Gas",

    # Income-ish
    "PAYROLL": "Income",
    "MILLENNIUM HEALTC": "Income",  # your job
    "VENMO": "Transfers & P2P",
    "PAYPAL": "Transfers & P2P",
    "ZELLE": "Transfers & P2P",
    "CASH APP": "Transfers & P2P",

    # Credit card payments / internal transfers
    "CAPITAL ONE": "Debt Payments",
    "CHASE CREDIT": "Debt Payments",
    "DISCOVER": "Debt Payments",
    "ONLINE TRANSFER": "Internal Transfer",

    # Fun / games (example)
    "UNIFIED ESPORTS": "Fun & Games",
}

def auto_category(merchant: str, amount: float, notes: str = "") -> str:
    if not merchant:
        merchant = ""

    up = merchant.upper()

    # 1) Merchant-based rules
    for needle, cat in CATEGORY_RULES.items():
        if needle in up:
            return cat

    # 2) Basic fallbacks
    if amount > 0:
        return "Income"
    if "REFUND" in up or "REVERSAL" in up:
        return "Refund"

    # 3) Default
    return "Uncategorized"
