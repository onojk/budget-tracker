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
    assert b"2,431" in resp.data


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


# ── Chart presence tests (Commit C → all fail until Commit D) ──────────────


def test_charts_has_chartjs_cdn(client):
    resp = client.get("/budget-summary")
    assert b"chart.umd.min.js" in resp.data


def test_section1_has_cc_net_chart(client):
    resp = client.get("/budget-summary")
    assert b"cc-net-chart" in resp.data


def test_section2_has_paydown_chart(client):
    resp = client.get("/budget-summary")
    assert b"paydown-chart" in resp.data


def test_section3_has_income_chart(client):
    resp = client.get("/budget-summary")
    assert b"income-chart" in resp.data


def test_section4_has_variable_svg_bars(client):
    resp = client.get("/budget-summary")
    assert b"var-bar" in resp.data


def test_section5_has_gap_svg(client):
    resp = client.get("/budget-summary")
    assert b"gap-svg" in resp.data


def test_section6_has_rent_progress(client):
    resp = client.get("/budget-summary")
    assert b"rent-progress" in resp.data


def test_section7_has_projection_chart(client):
    resp = client.get("/budget-summary")
    assert b"projection-chart" in resp.data


# ── Realistic Uber income update tests (Commit E → all fail until Commit F) ──


def test_budget_summary_shows_uber_realistic_income(client):
    resp = client.get("/budget-summary")
    assert b"1,290" in resp.data


def test_budget_summary_shows_income_with_uber(client):
    resp = client.get("/budget-summary")
    assert b"4,625" in resp.data


def test_budget_summary_shows_available_with_uber_updated(client):
    resp = client.get("/budget-summary")
    assert b"1,878" in resp.data


def test_budget_summary_shows_gap_after_uber(client):
    resp = client.get("/budget-summary")
    assert b"1,432" in resp.data


def test_budget_summary_shows_surplus_realistic(client):
    resp = client.get("/budget-summary")
    assert b"+$68" in resp.data


def test_section5_has_three_waterfalls(client):
    resp = client.get("/budget-summary")
    assert b"gap-svg-2" in resp.data


# ── CareCredit clearance + totals table tests (Commit G → fail until H) ──────


def test_section1_shows_debt_after_carecredit(client):
    resp = client.get("/budget-summary")
    assert b"1,747" in resp.data


def test_section1_shows_carecredit_commitment(client):
    resp = client.get("/budget-summary")
    assert b"committed" in resp.data


def test_section2_has_totals_table(client):
    resp = client.get("/budget-summary")
    assert b"cc-totals" in resp.data


def test_section5_updated_available_with_uber(client):
    resp = client.get("/budget-summary")
    assert b"1,878" in resp.data


def test_section6_notes_carecredit_separately(client):
    resp = client.get("/budget-summary")
    assert b"Separately" in resp.data


def test_section7_shows_updated_payoff_timeline(client):
    resp = client.get("/budget-summary")
    assert b"~26" in resp.data


# ── Net Uber + gas recompute + transportation category (Commit I → fail until J) ──


def test_section3_shows_uber_gas_deduction(client):
    resp = client.get("/budget-summary")
    assert b"incremental gas" in resp.data


def test_section3_shows_maintenance_deduction(client):
    resp = client.get("/budget-summary")
    assert b"maintenance set" in resp.data


def test_section3_shows_net_income_total(client):
    resp = client.get("/budget-summary")
    assert b"4,625" in resp.data


def test_section4_has_gas_transport_line(client):
    resp = client.get("/budget-summary")
    assert b"Gas/Transportation" in resp.data


def test_section4_shows_updated_variable_total(client):
    resp = client.get("/budget-summary")
    assert b"3,310" in resp.data


def test_section5_waterfall1_updated_gap(client):
    resp = client.get("/budget-summary")
    assert b"2,431" in resp.data


def test_section5_waterfall2_updated_gap(client):
    resp = client.get("/budget-summary")
    assert b"1,432" in resp.data


def test_section7_shows_realistic_shortfall(client):
    resp = client.get("/budget-summary")
    assert b"$232" in resp.data


def test_section5_shows_two_cut_scenarios(client):
    resp = client.get("/budget-summary")
    assert b"gap-svg-3b" in resp.data


# ── DoorDash Reality Check section (Commit M → all fail until Commit N) ──────


def test_doordash_section_present(client):
    resp = client.get("/budget-summary")
    assert b"dd-reality" in resp.data


def test_doordash_section_has_heading(client):
    resp = client.get("/budget-summary")
    assert b"DoorDash" in resp.data


def test_doordash_shows_real_annual_total(client):
    resp = client.get("/budget-summary")
    assert b"9,366" in resp.data


def test_doordash_shows_order_count(client):
    resp = client.get("/budget-summary")
    assert b"240" in resp.data


def test_doordash_has_cost_stack_visual(client):
    resp = client.get("/budget-summary")
    assert b"dd-cost-stack" in resp.data


def test_doordash_shows_contradiction_framing(client):
    resp = client.get("/budget-summary")
    assert b"bringing money in" in resp.data


def test_doordash_shows_monthly_normalized(client):
    resp = client.get("/budget-summary")
    assert b"560" in resp.data


def test_doordash_shows_reduction_target(client):
    resp = client.get("/budget-summary")
    assert b"$50" in resp.data


def test_doordash_shows_annual_savings(client):
    resp = client.get("/budget-summary")
    assert b"6,120" in resp.data


def test_doordash_shows_uber_hours_equivalence(client):
    resp = client.get("/budget-summary")
    assert b"Uber" in resp.data and b"hours" in resp.data
