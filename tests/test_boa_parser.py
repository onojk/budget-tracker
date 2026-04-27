"""
Tests for the Bank of America statement parser and router.
"""
from pathlib import Path
import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "boa_statement.txt"


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

def test_boa_parser_returns_rows():
    from ocr_pipeline import parse_boa_statement_text
    rows = parse_boa_statement_text(FIXTURE)
    assert len(rows) == 7, f"Expected 7 rows, got {len(rows)}: {rows}"


def test_boa_parser_account_name():
    from ocr_pipeline import parse_boa_statement_text
    rows = parse_boa_statement_text(FIXTURE)
    assert all(r["Account"] == "BoA Adv Plus" for r in rows), (
        "All rows should be tagged 'BoA Adv Plus'"
    )


def test_boa_parser_deposits_are_positive():
    from ocr_pipeline import parse_boa_statement_text
    rows = parse_boa_statement_text(FIXTURE)
    deposits = [r for r in rows if r["Direction"] == "credit"]
    assert len(deposits) == 2
    assert all(r["Amount"] > 0 for r in deposits)


def test_boa_parser_withdrawals_are_negative():
    from ocr_pipeline import parse_boa_statement_text
    rows = parse_boa_statement_text(FIXTURE)
    withdrawals = [r for r in rows if r["Direction"] == "debit"]
    assert len(withdrawals) == 5
    assert all(r["Amount"] < 0 for r in withdrawals)


def test_boa_parser_specific_amounts():
    from ocr_pipeline import parse_boa_statement_text
    rows = parse_boa_statement_text(FIXTURE)
    amounts = sorted([abs(r["Amount"]) for r in rows])
    expected = sorted([1835.43, 500.00, 89.99, 200.00, 300.00, 750.00, 12.00])
    for a, e in zip(amounts, expected):
        assert abs(a - e) < 0.005, f"Amount mismatch: got {a}, expected {e}"


def test_boa_parser_dates():
    from ocr_pipeline import parse_boa_statement_text
    rows = parse_boa_statement_text(FIXTURE)
    dates = {r["Date"] for r in rows}
    assert "2024-01-03" in dates
    assert "2024-01-31" in dates


def test_boa_parser_source_tag():
    from ocr_pipeline import parse_boa_statement_text
    rows = parse_boa_statement_text(FIXTURE)
    assert all(r["Source"] == "Statement OCR" for r in rows)


# ---------------------------------------------------------------------------
# Router test: BoA fixture is detected and routed before Chase fallback
# ---------------------------------------------------------------------------

def test_boa_router_detects_bank_of_america(tmp_path, monkeypatch):
    """process_uploaded_statement_files routes BoA OCR text to parse_boa_statement_text."""
    import ocr_pipeline

    uploads_dir = tmp_path / "uploads"
    stmts = tmp_path / "statements"
    uploads_dir.mkdir()
    stmts.mkdir()

    # Place the pre-OCR'd fixture in uploads_dir — the pipeline copies .txt files
    # straight through to statements_dir without needing pdftotext/tesseract.
    (uploads_dir / "boa_statement.txt").write_text(FIXTURE.read_text())

    # Patch import_ocr_rows so no DB/app context is needed.
    monkeypatch.setattr(
        ocr_pipeline,
        "import_ocr_rows",
        lambda rows, **kw: (len(rows), 0),
    )

    stats = ocr_pipeline.process_uploaded_statement_files(uploads_dir, stmts)

    assert stats["candidate_lines"] == 7, (
        f"Router should route 7 BoA rows, got {stats['candidate_lines']}. "
        "Check that 'bank of america' detection fires before the Chase check."
    )
    assert stats["added_transactions"] == 7


# ---------------------------------------------------------------------------
# Regression: explicit negative-sign amounts in BoA OCR text
#
# BoA writes withdrawal amounts with a leading '-' (e.g. "-150.00").
# Before the fix the regex `([\d,]+\.\d{2})` silently dropped those lines.
# After the fix `-?` in the regex captures them and `abs()` ensures the
# section-based direction logic (not the raw sign) controls the final sign.
# ---------------------------------------------------------------------------

_EXPLICIT_NEG_FIXTURE = """\
Bank of America
Account # XXXX XXXX XXXX 0205

Deposits and other additions
01/10/26  Refund From Merchant                    -25.00

Withdrawals and other subtractions
01/15/26  Card Purchase Coffee Shop               -12.50
"""


def test_explicit_negative_in_deposits_section_is_credit(tmp_path):
    """
    A line with a '-' amount inside a deposits section must parse as credit
    (positive). Typical case: a refund/reversal credited back to the account
    that BoA writes as '-25.00' in the deposits table.
    """
    from ocr_pipeline import parse_boa_statement_text

    f = tmp_path / "boa_neg_test.txt"
    f.write_text(_EXPLICIT_NEG_FIXTURE)

    rows = parse_boa_statement_text(f)
    deposits = [r for r in rows if r["Direction"] == "credit"]

    assert len(deposits) == 1, f"Expected 1 credit row, got {len(deposits)}: {rows}"
    assert deposits[0]["Amount"] == pytest.approx(25.00), (
        f"Refund in deposits section must be positive, got {deposits[0]['Amount']}"
    )


def test_explicit_negative_in_withdrawals_section_is_debit(tmp_path):
    """
    A line with a '-' amount inside a withdrawals section must parse as debit
    (negative). Without abs() the double-negation would flip the sign to +12.50.
    """
    from ocr_pipeline import parse_boa_statement_text

    f = tmp_path / "boa_neg_test.txt"
    f.write_text(_EXPLICIT_NEG_FIXTURE)

    rows = parse_boa_statement_text(f)
    debits = [r for r in rows if r["Direction"] == "debit"]

    assert len(debits) == 1, f"Expected 1 debit row, got {len(debits)}: {rows}"
    assert debits[0]["Amount"] == pytest.approx(-12.50), (
        f"Purchase in withdrawals section must be negative, got {debits[0]['Amount']}"
    )
