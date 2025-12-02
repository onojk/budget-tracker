"""
direction_rules.py

Centralized logic for turning raw OCR'd transaction rows into
signed amounts (debit / credit) plus optional semantic labels.

Bank-specific parsers (e.g. Chase statement OCR) should:
  1. Extract a clean numeric amount string (no currency symbol).
  2. Build a short context string that includes the transaction
     description and any key words like "DEBIT", "CREDIT",
     "Direct Dep", "Card Purchase", "Fee", etc.
  3. Call parse_signed_amount() with the raw amount and context.

If you ever onboard another bank or card provider, you generally
only need to:
  * update DEBIT_HINT_WORDS / CREDIT_HINT_WORDS, or
  * add new patterns to classify_transaction_type().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Heuristic keyword lists
# ---------------------------------------------------------------------------

DEBIT_HINT_WORDS: tuple[str, ...] = (
    # very strong "money going out" words
    "card purchase",
    "pos purchase",
    "debit card",
    "withdrawal",
    "atm withdrawal",
    "atm withdrl",
    "atm",
    "cash withdrawal",
    "cash advance",
    "payment",
    "bill pay",
    "auto pay",
    "autopay",
    "ach debit",
    "ach withdrawal",
    "ach withdrl",
    "recurring card purchase",
    "subscription",
    "online transfer to",
    "transfer to",
    "zelle to",
    "venmo cashout",
    "venmo payment",
    "paypal inst xfer",
    "paypal inst transfer",
    "paypal inst xfer",
    "paypal debit",
    "doordash",
    "uber",
    "lyft",
    "grubhub",
    "postmates",
    "ubereats",
    "instacart",
    # fee / charge language
    "fee",
    "service charge",
    "maintenance fee",
    "overdraft fee",
    "late fee",
    "nsf fee",
    "interest charged",
    "finance charge",
    # other negatives
    "charge",
    "purchase",
    "preauth",
    "authorization",
)

CREDIT_HINT_WORDS: tuple[str, ...] = (
    # very strong "money coming in" words
    "deposit",
    "direct dep",
    "directdep",
    "directdeposit",
    "direct deposit",
    "payroll",
    "salary",
    "wages",
    "employer",
    "refund",
    "rebate",
    "reversal",
    "returned item",
    "ach credit",
    "ach deposit",
    "ach cr",
    "credit",
    "cr ",
    " interest paid",
    "interest payment",
    "dividend",
    "cashback",
    "cash back",
    "reward",
    "real time transfer recd from",
    "transfer from",
    "online transfer from",
    "zelle from",
    "venmo payment received",
    "venmo cashin",
    "paypal transfer",
    "paypal cashout from",
)


@dataclass
class DirectionContext:
    """
    Optional extra context for inferring sign.

    In most current callsites we only pass the `description` and rely on
    keyword heuristics. The balance_before / balance_after fields are here
    for future extension when we wire in ledger balance deltas.
    """

    description: str
    balance_before: Optional[float] = None
    balance_after: Optional[float] = None

    @property
    def normalized(self) -> str:
        return " ".join(self.description.lower().split())


def _parse_amount_core(raw: str) -> float:
    """
    Parse a numeric amount string that may contain commas and an optional sign.
    Parentheses are treated as a negative sign, e.g. "(123.45)" -> -123.45.
    """
    if raw is None:
        raise ValueError("Amount is None")

    s = str(raw).strip()

    negative = False
    # handle parentheses: (123.45) => -123.45
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # explicit leading sign
    if s.startswith("+"):
        s = s[1:].strip()
    elif s.startswith("-"):
        negative = True
        s = s[1:].strip()

    s = s.replace(",", "")

    if not s:
        raise ValueError(f"Empty amount after cleaning: {raw!r}")

    value = float(s)
    return -value if negative else value


def _score_keywords(text: str, keywords: tuple[str, ...]) -> int:
    """
    Very small heuristic scorer: counts how many hint words appear in
    the normalized text.
    """
    text = text.lower()
    score = 0
    for kw in keywords:
        if kw in text:
            score += 1
    return score


def infer_direction_sign(
    amount_raw: str,
    ctx: Optional[DirectionContext] = None,
) -> int:
    """
    Infer the *sign* (direction) for a transaction amount.

    Returns:
        +1 for credit (money coming *into* the account)
        -1 for debit  (money going *out* of the account)

    Strategy, in priority order:

      1. Trust explicit sign or parentheses in the amount string.
      2. If no explicit sign, look at known debit/credit hint words in the
         description (e.g. "Card Purchase" vs. "Direct Dep").
      3. If balance_before / balance_after are provided, cross-check the
         change against the raw magnitude to choose a direction.
      4. Default to debit (-1) as a conservative assumption if we truly
         can't tell — it's easier for a human to reclassify an expense
         as income than to miss an outflow.
    """
    # First see if amount_raw already encodes the sign.
    s = str(amount_raw).strip()
    explicit_negative = False
    explicit_positive = False

    if s.startswith("-"):
        explicit_negative = True
    if s.startswith("+") or (s.startswith("(") and s.endswith(")")):
        explicit_positive = True

    if explicit_negative and not explicit_positive:
        return -1
    if explicit_positive and not explicit_negative:
        return +1

    # No explicit sign → we need context.
    description = ctx.normalized if ctx is not None else ""
    debit_score = _score_keywords(description, DEBIT_HINT_WORDS)
    credit_score = _score_keywords(description, CREDIT_HINT_WORDS)

    if debit_score > credit_score:
        return -1
    if credit_score > debit_score:
        return +1

    # If we have balances, try to infer from delta (after - before).
    if ctx is not None and ctx.balance_before is not None and ctx.balance_after is not None:
        delta = ctx.balance_after - ctx.balance_before
        if delta > 0:
            return +1
        if delta < 0:
            return -1

    # Absolute fallback: treat as debit.  Conservative, and fits the
    # "Goldman review" mindset: better to over-count spend than to
    # accidentally inflate income.
    return -1


def parse_signed_amount(
    raw: str,
    context: str = "",
    *,
    balance_before: Optional[float] = None,
    balance_after: Optional[float] = None,
) -> float:
    """
    Parse an OCR'd amount string into a properly signed float, using a
    combination of explicit sign characters and context heuristics.

    This is a drop-in replacement for the legacy `parse_signed_amount`
    function in ocr_pipeline.py, but with the logic centralized here.

    Args:
        raw:      The raw OCR'd amount, e.g. "1,234.56", "(45.67)", "-89.00".
        context:  A one-line description or snippet from the transaction row.
        balance_before / balance_after:
                  Optional running-balance figures from the statement line.

    Returns:
        A Python float with correct sign applied.
    """
    ctx = DirectionContext(
        description=context or "",
        balance_before=balance_before,
        balance_after=balance_after,
    )
    magnitude = abs(_parse_amount_core(raw))
    sign = infer_direction_sign(raw, ctx=ctx)
    return sign * magnitude


def classify_transaction_type(description: str) -> str:
    """
    Lightweight semantic classifier used for higher-level analytics
    (e.g. dashboard groupings).  This does *not* affect the numeric sign
    of the transaction, just the label.

    Returns one of:
        "income", "expense", "transfer", "fee", "interest", "refund", "unknown"
    """
    text = " ".join(description.lower().split())

    # Order matters – check for more specific patterns first.
    if any(w in text for w in ("payroll", "direct dep", "direct deposit", "salary", "wages")):
        return "income"
    if "interest paid" in text or "interest payment" in text or "interest income" in text:
        return "interest"
    if "fee" in text or "service charge" in text or "overdraft fee" in text or "nsf fee" in text:
        return "fee"
    if "refund" in text or "rebate" in text or "reversal" in text or "returned item" in text:
        return "refund"
    if any(w in text for w in ("transfer to", "transfer from", "online transfer", "zelle", "venmo", "paypal")):
        return "transfer"

    # Expense-ish words
    if any(w in text for w in ("card purchase", "pos purchase", "debit card", "atm", "purchase", "charge", "payment")):
        return "expense"

    # Fallback
    return "unknown"
