"""
Tests for parsers/venmo_csv_parser.py

Fixture: tests/fixtures/venmo_statement.csv
  - 4 preamble rows (header, activity, columns, beginning balance stub)
  - 8 transaction rows:
      1001 Payment received +$25 (Alice Smith)         → IMPORT credit
      1002 Payment sent -$15 (Bob Jones)               → IMPORT debit
      1003 Venmo Card Transaction -$5 (Coffee Shop)    → IMPORT debit
      1004 Merchant Transaction -$3 (DoorDash, Venmo balance) → IMPORT debit
      1005 Merchant Transaction -$38.87 (bank-funded)  → SKIP + log
      1006 Instant Add Funds +$50                      → SKIP
      1007 Instant Transfer -$50 fee -$0.88            → SKIP
      1008 Venmo account repayment +$5                 → SKIP
  - Footer row: end=$11.12, period_fees=$0.88

Reconciliation identity (fixture-level):
  sum(imported) + period_fees = end - begin
  (+25 - 15 - 5 - 3) + (-0.88) = 11.12 - 10.00
  1.12 = 1.12  ✓
  (holds because Add Funds and Transfer amounts cancel, and bank-funded
  merchant does not touch the Venmo balance)
"""
from decimal import Decimal
from pathlib import Path
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "venmo_statement.csv"

OWN_NAME = "Test User"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_parser():
    from parsers.venmo_csv_parser import parse_venmo_csv
    return parse_venmo_csv


# ---------------------------------------------------------------------------
# Skip / preamble tests
# ---------------------------------------------------------------------------

def test_parse_skips_preamble_rows():
    rows, _ = get_parser()(FIXTURE)
    dates = {r["Date"] for r in rows}
    assert "Account Activity" not in str(rows)
    # no row should have a non-date string in Date field
    for r in rows:
        assert len(r["Date"]) == 10, f"Bad date: {r['Date']!r}"


def test_parse_skips_footer_row():
    rows, meta = get_parser()(FIXTURE)
    amounts = [r["Amount"] for r in rows]
    # footer has no ID — should not appear as a transaction
    assert all(isinstance(a, float) for a in amounts)
    assert meta["end"] == pytest.approx(11.12, abs=0.01)


def test_parse_skips_instant_add_funds():
    rows, _ = get_parser()(FIXTURE)
    types = [r.get("_type") for r in rows]
    assert "Instant Add Funds" not in types
    # ID 1006 should not appear
    ids = [r.get("Notes", "") for r in rows]
    assert not any("1006" in n for n in ids)


def test_parse_skips_instant_transfer():
    rows, _ = get_parser()(FIXTURE)
    ids = [r.get("Notes", "") for r in rows]
    assert not any("1007" in n for n in ids)


def test_parse_skips_venmo_account_repayment():
    rows, _ = get_parser()(FIXTURE)
    ids = [r.get("Notes", "") for r in rows]
    assert not any("1008" in n for n in ids)


def test_parse_skips_bank_funded_merchant_transaction(tmp_path):
    """
    Bank-funded merchant (row 1005) must NOT appear in returned transactions,
    but MUST be written to the skip log.
    """
    import tempfile, os
    log_path = tmp_path / "venmo-skipped.log"
    rows, _ = get_parser()(FIXTURE, skip_log=log_path)
    ids = [r.get("Notes", "") for r in rows]
    assert not any("1005" in n for n in ids), "Bank-funded merchant should be skipped"
    assert log_path.exists(), "Skip log must be written"
    log_text = log_path.read_text()
    assert "1005" in log_text or "Restaurant" in log_text, (
        f"Skip log should mention row 1005 / Restaurant, got: {log_text!r}"
    )
    assert "BANK OF AMERICA" in log_text.upper(), (
        "Skip log should record the funding source"
    )


# ---------------------------------------------------------------------------
# Import / content tests
# ---------------------------------------------------------------------------

def test_parse_imports_p2p_payment_received_as_credit():
    rows, _ = get_parser()(FIXTURE)
    received = [r for r in rows if "1001" in r.get("Notes", "")]
    assert len(received) == 1, f"Expected 1 row for ID 1001, got {len(received)}"
    r = received[0]
    assert r["Amount"] == pytest.approx(25.00)
    assert r["Direction"] == "credit"
    assert r["Merchant"] == "Alice Smith"
    assert r["Description"] == "pizza"
    assert r["Account"] == "Venmo"


def test_parse_imports_p2p_payment_sent_as_debit():
    rows, _ = get_parser()(FIXTURE)
    sent = [r for r in rows if "1002" in r.get("Notes", "")]
    assert len(sent) == 1
    r = sent[0]
    assert r["Amount"] == pytest.approx(-15.00)
    assert r["Direction"] == "debit"
    assert r["Merchant"] == "Bob Jones"
    assert r["Description"] == "coffee"


def test_parse_imports_venmo_card_transaction_as_debit_with_merchant():
    rows, _ = get_parser()(FIXTURE)
    card = [r for r in rows if "1003" in r.get("Notes", "")]
    assert len(card) == 1
    r = card[0]
    assert r["Amount"] == pytest.approx(-5.00)
    assert r["Direction"] == "debit"
    assert r["Merchant"] == "Coffee Shop"


def test_parse_imports_balance_funded_merchant_transaction():
    rows, _ = get_parser()(FIXTURE)
    merch = [r for r in rows if "1004" in r.get("Notes", "")]
    assert len(merch) == 1
    r = merch[0]
    assert r["Amount"] == pytest.approx(-3.00)
    assert r["Direction"] == "debit"
    assert r["Merchant"] == "DoorDash"


def test_parse_extracts_beginning_and_ending_balance_for_reconciliation():
    _, meta = get_parser()(FIXTURE)
    assert meta["begin"] == pytest.approx(10.00, abs=0.01)
    assert meta["end"]   == pytest.approx(11.12, abs=0.01)
    assert meta["period_fees"] == pytest.approx(0.88, abs=0.01)


def test_reconciliation_matches_balance_delta():
    """
    For the synthetic fixture (Add Funds and Transfer cancel, bank-funded
    merchant doesn't touch Venmo balance):
      sum(imported) + period_fees ≈ end - begin
    """
    rows, meta = get_parser()(FIXTURE)
    imported_sum = sum(r["Amount"] for r in rows)
    lhs = round(imported_sum + (-meta["period_fees"]), 2)
    rhs = round(meta["end"] - meta["begin"], 2)
    assert abs(lhs - rhs) <= 0.01, (
        f"Reconciliation failed: sum(imported)={imported_sum:.2f} "
        f"fees={meta['period_fees']:.2f}  lhs={lhs}  rhs={rhs}"
    )


# ---------------------------------------------------------------------------
# Total imported count
# ---------------------------------------------------------------------------

def test_parse_returns_exactly_four_imported_rows():
    rows, _ = get_parser()(FIXTURE)
    assert len(rows) == 4, (
        f"Expected 4 imported rows (skipping 5 rows), got {len(rows)}: "
        + str([r.get('Notes') for r in rows])
    )


# ---------------------------------------------------------------------------
# Deduplication regression test
# ---------------------------------------------------------------------------

def test_parse_deduplicates_rows_with_same_transaction_id(tmp_path, capsys):
    """
    When a Venmo CSV contains two rows with the same transaction ID (a known
    Venmo export artifact seen in the Apr-2026 statement), only the first row
    must be imported and a dedup message must be printed.
    """
    csv_content = "\n".join([
        "Account Statement - (@Dup-Test) ,,,,,,,,,,,,,,,,,,,,,",
        "Account Activity,,,,,,,,,,,,,,,,,,,,,",
        ",ID,Datetime,Type,Status,Note,From,To,Amount (total),,,Amount (fee),,,Funding Source,Destination,Beginning Balance,Ending Balance,Statement Period Venmo Fees,,,",
        ",,,,,,,,,,,,,,,,$0.00,,,,,",
        ",9001,2026-04-01T10:00:00,Venmo Card Transaction,Complete,,Dup Test,Acme Corp,- $50.00,,,,,,Venmo balance,,,,Venmo,,",
        ",9001,2026-04-01T10:00:00,Venmo Card Transaction,Complete,,Dup Test,Acme Corp,- $50.00,,,,,,Venmo balance,,,,Venmo,,",
        ",,,,,,,,,,,,,,,,,$0.00,$0.00,,$0.00,",
    ]) + "\n"

    dup_csv = tmp_path / "dup_venmo.csv"
    dup_csv.write_text(csv_content)

    rows, _ = get_parser()(dup_csv)

    assert len(rows) == 1, f"Expected 1 row after dedup, got {len(rows)}"
    assert rows[0]["Amount"] == pytest.approx(-50.00)
    assert rows[0]["Merchant"] == "Acme Corp"

    captured = capsys.readouterr()
    assert "duplicate" in captured.out.lower(), (
        f"Expected dedup log message in stdout, got: {captured.out!r}"
    )
    assert "9001" in captured.out, (
        f"Expected duplicate ID in log message, got: {captured.out!r}"
    )
