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

    # Fixture has 4 real transaction lines (Beginning Balance is skipped).
    assert len(rows) == 4, f"Expected 4 rows, got {len(rows)}: {rows}"

    # Row 0: debit grocery purchase
    assert rows[0]["Date"] == "2025-01-05"
    assert rows[0]["Amount"] == pytest.approx(-45.67)
    assert rows[0]["Merchant"] == "Grocery Stop Market"
    assert rows[0]["Direction"] == "debit"
    assert rows[0]["Source"] == "Statement OCR"

    # Row 1: credit payroll deposit
    assert rows[1]["Date"] == "2025-01-10"
    assert rows[1]["Amount"] == pytest.approx(1500.00)
    assert rows[1]["Merchant"] == "ACH Credit Payroll"
    assert rows[1]["Direction"] == "credit"

    # Row 2: debit card purchase
    assert rows[2]["Date"] == "2025-01-15"
    assert rows[2]["Amount"] == pytest.approx(-99.99)

    # Row 3: debit ATM withdrawal
    assert rows[3]["Date"] == "2025-01-20"
    assert rows[3]["Amount"] == pytest.approx(-60.00)


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
