"""
Tests for the Chase statement detail parser and its routing inside
process_uploaded_statement_files.
"""
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "chase_statement_detail.txt"


# ---------------------------------------------------------------------------
# 1. Direct parser test
#    Calls _parse_chase_transaction_detail on the fixture and asserts that
#    the correct rows come back.  This test exercises the parser itself —
#    it will pass whether or not the routing is wired up.
# ---------------------------------------------------------------------------
def test_parse_chase_transaction_detail_extracts_rows():
    from ocr_pipeline import _parse_chase_transaction_detail

    rows = _parse_chase_transaction_detail(FIXTURE)

    # Fixture has 2 blocks: 2 transactions in block 1, 2 in block 2 = 4 total.
    # Beginning Balance line is skipped. Pre-fix: only 2 rows (block 1 only).
    assert len(rows) == 4, f"Expected 4 rows, got {len(rows)}: {rows}"

    # Row 0: debit grocery purchase
    assert rows[0]["Date"] == "2025-01-05"
    assert rows[0]["Amount"] == pytest.approx(-45.67)
    assert rows[0]["Merchant"] == "Grocery Stop Market"
    assert rows[0]["Direction"] == "debit"
    assert rows[0]["Source"] == "Statement OCR"

    # Row 1: credit payroll deposit — ACH prefix stripped from merchant
    assert rows[1]["Date"] == "2025-01-10"
    assert rows[1]["Amount"] == pytest.approx(1500.00)
    assert rows[1]["Merchant"] == "Payroll"
    assert rows[1]["Description"] == "ACH Credit Payroll"
    assert rows[1]["Direction"] == "credit"

    # Row 2: debit card purchase — prefix + state/Card suffix stripped
    assert rows[2]["Date"] == "2025-01-15"
    assert rows[2]["Amount"] == pytest.approx(-99.99)
    assert rows[2]["Merchant"] == "Online Retailer"
    assert rows[2]["Description"] == "Card Purchase 01/15 Online Retailer CA Card"

    # Row 3: debit ATM withdrawal — normalized to "ATM Withdrawal"
    assert rows[3]["Date"] == "2025-01-20"
    assert rows[3]["Amount"] == pytest.approx(-60.00)
    assert rows[3]["Merchant"] == "ATM Withdrawal"
    assert rows[3]["Description"] == "ATM Cash Withdrawal"

    # Account detection: blocks preceded by different Account Number: lines
    # should be tagged with the correct account names.
    assert rows[0]["Account"] == "Chase Checking", f"Row 0 account: {rows[0]['Account']}"
    assert rows[1]["Account"] == "Chase Checking", f"Row 1 account: {rows[1]['Account']}"
    assert rows[2]["Account"] == "Chase Savings",  f"Row 2 account: {rows[2]['Account']}"
    assert rows[3]["Account"] == "Chase Savings",  f"Row 3 account: {rows[3]['Account']}"


# ---------------------------------------------------------------------------
# 3. Merchant extraction helper
#    Covers the common Chase transaction line prefixes.  The helper must
#    strip the prefix (and inline MM/DD date for card purchases) and return
#    a clean merchant name alongside the unchanged original description.
# ---------------------------------------------------------------------------
def test_chase_merchant_extraction():
    from ocr_pipeline import _split_chase_merchant

    cases = [
        # Card Purchase with phone + 2-letter state + "Card"
        (
            "Card Purchase 01/19 Super Care Health 888-260-2550 CA Card",
            "Super Care Health",
        ),
        # Card Purchase with state + "Card" (no phone)
        (
            "Card Purchase 02/03 Online Retailer CA Card",
            "Online Retailer",
        ),
        # Card Purchase With Pin — no trailing state/Card suffix
        (
            "Card Purchase With Pin 01/15 Dollar Tree",
            "Dollar Tree",
        ),
        # Recurring Card Purchase
        (
            "Recurring Card Purchase 02/15 Prime Video",
            "Prime Video",
        ),
        # ACH Credit
        (
            "ACH Credit Payroll",
            "Payroll",
        ),
        # Zelle Payment To
        (
            "Zelle Payment To John Smith",
            "John Smith",
        ),
        # ATM
        (
            "ATM Cash Withdrawal",
            "ATM Withdrawal",
        ),
        # Online Payment
        (
            "Online Payment 123456789 To Electric Company",
            "Electric Company",
        ),
        # No known prefix — falls through unchanged
        (
            "Grocery Stop Market",
            "Grocery Stop Market",
        ),
    ]
    for raw, expected_merchant in cases:
        merchant, description = _split_chase_merchant(raw)
        assert merchant == expected_merchant, (
            f"raw={raw!r}: got {merchant!r}, want {expected_merchant!r}"
        )
        assert description == raw, (
            f"description should be unchanged raw for {raw!r}"
        )


# ---------------------------------------------------------------------------
# 2. Routing test
#    Calls process_uploaded_statement_files with a txt file that contains
#    the *start*transaction detail marker and asserts that candidate_lines
#    is non-zero — proving the new routing reaches _parse_chase_transaction_detail.
#
#    import_ocr_rows is patched out so this test needs no database.
#
#    BEFORE the routing fix: candidate_lines == 0 (both legacy parsers miss
#    the MM/DD-only date format), so this test FAILS.
#    AFTER  the routing fix: candidate_lines == 4, test PASSES.
# ---------------------------------------------------------------------------
def test_uploader_routes_to_chase_detail_parser(tmp_path, monkeypatch):
    import ocr_pipeline

    uploads_dir = tmp_path / "uploads"
    statements_dir = tmp_path / "statements"
    uploads_dir.mkdir()
    statements_dir.mkdir()

    # The pipeline treats .txt files in uploads_dir as pre-OCR'd text and
    # copies them straight into statements_dir — no pdftotext/tesseract needed.
    (uploads_dir / "chase_statement_detail.txt").write_text(FIXTURE.read_text())

    # Patch import_ocr_rows so we don't need a DB or app context.
    monkeypatch.setattr(
        ocr_pipeline,
        "import_ocr_rows",
        lambda rows, **kw: (len(rows), 0),
    )

    stats = ocr_pipeline.process_uploaded_statement_files(uploads_dir, statements_dir)

    # Before fix: both legacy parsers return 0 rows → candidate_lines == 0.
    # After  fix: _parse_chase_transaction_detail returns 4 rows.
    assert stats["candidate_lines"] == 4, (
        f"Expected 4 candidate_lines, got {stats['candidate_lines']}. "
        "This means process_uploaded_statement_files did not route to "
        "_parse_chase_transaction_detail."
    )
    assert stats["statement_rows"] == 4
    assert stats["added_transactions"] == 4
