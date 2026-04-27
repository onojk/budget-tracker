"""Tests for /budget-summary route."""


def test_budget_summary_returns_200(client):
    resp = client.get("/budget-summary")
    assert resp.status_code == 200


def test_budget_summary_shows_cash_total(client):
    resp = client.get("/budget-summary")
    assert b"457.84" in resp.data


def test_budget_summary_shows_debt_total(client):
    resp = client.get("/budget-summary")
    assert b"4,488.01" in resp.data


def test_budget_summary_shows_carecredit_balance(client):
    resp = client.get("/budget-summary")
    assert b"2,740.45" in resp.data


def test_budget_summary_shows_rent_shortfall(client):
    resp = client.get("/budget-summary")
    assert b"1,876.10" in resp.data


def test_budget_summary_shows_structural_gap(client):
    resp = client.get("/budget-summary")
    assert b"2,281" in resp.data


def test_budget_summary_has_seven_sections(client):
    resp = client.get("/budget-summary")
    for heading in [
        b"Where We Are",
        b"Recent Effort",
        b"Monthly Household",
        b"Variable Spending",
        b"Structural Gap",
        b"This Month",
        b"Plan To Reduce",
    ]:
        assert heading in resp.data, f"Missing section heading: {heading!r}"


def test_budget_summary_shows_paydown_headline(client):
    resp = client.get("/budget-summary")
    assert b"1,110" in resp.data


def test_budget_summary_shows_net_position(client):
    resp = client.get("/budget-summary")
    assert b"4,030.17" in resp.data


def test_budget_summary_has_slide_mode_assets(client):
    resp = client.get("/budget-summary")
    assert b"scroll-mode" in resp.data
    assert b"slide-mode" in resp.data
    assert b"togglePresentationMode" in resp.data
