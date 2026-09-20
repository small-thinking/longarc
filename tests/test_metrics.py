"""Independent arithmetic and missing-data checks for the pure quote calculator."""

import json

import pytest

from longarc.analytics.metrics import calculate

AS_OF = "2026-09-20T14:30:00Z"
CONTRACT = {
    "symbol": "QQQ", "option_type": "CALL", "expiry_date": "2026-10-16",
    "strike_u": 750_000_000, "multiplier": 100,
}
SNAPSHOT = {
    "bid_u": 3_000_000, "ask_u": 3_200_001, "underlying_price_u": 740_000_000,
    "delta": 0.25, "theta": -0.12,
    "quote_at": "2026-09-20T14:29:00Z", "greeks_at": "2026-09-20T14:28:00Z",
    "underlying_at": "2026-09-20T14:29:30Z", "captured_at": "2026-09-20T14:30:00Z",
}


def test_quote_values_preserve_half_micro_and_provider_greeks():
    result = calculate(CONTRACT, SNAPSHOT, AS_OF)
    metrics = result["metrics"]
    assert result["status"] == "ok"
    assert metrics["midpoint_u"] == "3100000.5"
    assert metrics["spread_u"] == "200001"
    assert metrics["strike_distance_u"] == "10000000"
    assert metrics["intrinsic_u"] == "0"
    assert metrics["dte_calendar_days"] == 26
    assert metrics["quote_age_seconds"] == 60
    assert metrics["observed_theta"] == "-0.12"
    json.dumps(result, allow_nan=False)


def test_new_york_date_and_signed_intrinsic_extrinsic():
    result = calculate(CONTRACT, {**SNAPSHOT, "underlying_price_u": 755_000_000},
                       "2026-09-21T01:00:00Z")
    assert result["metrics"]["dte_calendar_days"] == 26  # Still September 20 in New York.
    assert result["metrics"]["intrinsic_u"] == "5000000"
    assert result["metrics"]["midpoint_extrinsic_u"] == "-1899999.5"
    assert result["metrics"]["strike_distance_u"] == "-5000000"
    assert "negative_midpoint_extrinsic" in result["warnings"]


def test_scenario_uses_explicit_quantity_and_total_fees():
    scenario = {"contracts": 2, "opening_premium_u": 5_000_000,
                "opening_fees_u": 1_300_000, "closing_fees_u": 1_300_000,
                "covered_shares": 150}
    result = calculate(CONTRACT, SNAPSHOT, AS_OF, scenario)["metrics"]
    assert result["scenario_close_gross_u"] == "640000200"
    assert result["scenario_close_cost_u"] == "641300200"
    assert result["scenario_net_option_pnl_u"] == "357399800"
    assert result["scenario_gross_premium_captured_fraction"] == "0.3599998"
    assert result["scenario_coverage_fraction"] == "0.75"
    assert result["scenario_uncovered_units"] == 50


def test_unknown_fees_quantity_and_multiplier_are_not_invented():
    result = calculate({**CONTRACT, "multiplier": None}, SNAPSHOT, AS_OF,
                       {"contracts": 2, "opening_premium_u": 5_000_000})
    assert result["metrics"]["midpoint_u"] == "3100000.5"
    assert result["metrics"]["scenario_close_cost_u"] is None
    assert result["metrics"]["scenario_net_option_pnl_u"] is None
    assert "unknown_multiplier" in result["warnings"]
    no_quantity = calculate(CONTRACT, SNAPSHOT, AS_OF, {})
    assert no_quantity["metrics"]["scenario_close_gross_u"] is None
    assert "incomplete_pnl_scenario" in no_quantity["warnings"]


def test_absent_prices_and_zero_denominators_do_not_become_zero_metrics():
    result = calculate(CONTRACT, {}, AS_OF)
    assert result["status"] == "partial"
    assert result["metrics"]["midpoint_u"] is None
    assert result["metrics"]["intrinsic_u"] is None
    zero = calculate(CONTRACT, {**SNAPSHOT, "bid_u": 0, "ask_u": 0}, AS_OF,
                     {"opening_premium_u": 0})
    assert zero["metrics"]["spread_fraction_of_midpoint"] is None
    assert zero["metrics"]["scenario_gross_premium_captured_fraction"] is None


def test_crossed_quote_is_invalid_and_future_data_is_not_used():
    crossed = calculate(CONTRACT, {**SNAPSHOT, "bid_u": 4_000_000}, AS_OF)
    assert crossed["status"] == "invalid"
    assert crossed["metrics"]["midpoint_u"] is None
    future = calculate(CONTRACT, {**SNAPSHOT, "quote_at": "2026-09-21T00:00:00Z"}, AS_OF)
    assert future["metrics"]["quote_age_seconds"] < 0
    assert future["metrics"]["midpoint_u"] is None
    assert "future_quote_at" in future["warnings"]
    future_capture = calculate(CONTRACT, SNAPSHOT, "2026-09-20T14:29:45Z")
    assert future_capture["metrics"]["observed_delta"] is None
    assert future_capture["metrics"]["intrinsic_u"] is None


def test_previous_changes_and_roll_cashflow_are_not_total_profit():
    prior = {**SNAPSHOT, "bid_u": 4_000_000, "ask_u": 4_200_001, "delta": .35,
             "underlying_price_u": 741_000_000}
    replacement = {"contract": {**CONTRACT, "strike_u": 760_000_000,
                                 "expiry_date": "2026-10-23"},
                   "snapshot": {**SNAPSHOT, "bid_u": 4_000_000, "ask_u": 4_200_000}}
    result = calculate(CONTRACT, SNAPSHOT, AS_OF, previous=prior, replacement=replacement)
    metrics = result["metrics"]
    assert metrics["change_midpoint_u"] == "-1000000.0"
    assert metrics["change_observed_delta"] == "-0.10"
    assert metrics["change_underlying_price_u"] == "-1000000"
    assert metrics["roll_quote_cashflow_per_unit_u"] == "799999"
    assert metrics["roll_added_calendar_days"] == 7
    assert metrics["roll_strike_change_u"] == "10000000"
    assert "roll_quote_cashflow_excludes_fees" in result["warnings"]
    assert "scenario_net_option_pnl_u" not in metrics


@pytest.mark.parametrize("patch", [
    {"bid_u": True}, {"ask_u": 3.1}, {"delta": float("nan")},
    {"theta": float("inf")}, {"delta": -0.1}, {"quote_at": "2026-09-20"},
])
def test_invalid_types_ranges_and_naive_times_are_rejected(patch):
    with pytest.raises(ValueError):
        calculate(CONTRACT, {**SNAPSHOT, **patch}, AS_OF)


def test_invalid_scenario_and_contract_are_rejected():
    for scenario in ({"contracts": 0}, {"contracts": True}, {"unknown": 1}):
        with pytest.raises(ValueError):
            calculate(CONTRACT, SNAPSHOT, AS_OF, scenario)
    with pytest.raises(ValueError):
        calculate({**CONTRACT, "option_type": "PUT"}, SNAPSHOT, AS_OF)
    with pytest.raises(ValueError):
        calculate(CONTRACT, SNAPSHOT, AS_OF, replacement={})


def test_large_integer_money_is_not_rounded_through_float():
    scenario = {"contracts": 1_000_000, "opening_premium_u": 5_000_000,
                "opening_fees_u": 1, "closing_fees_u": 1}
    result = calculate(CONTRACT, SNAPSHOT, AS_OF, scenario)["metrics"]
    assert result["scenario_net_option_pnl_u"] == "179999899999998"


def test_age_is_reported_without_inventing_a_freshness_threshold():
    old_quote = {**SNAPSHOT, "quote_at": "2026-09-18T14:30:00Z"}
    result = calculate(CONTRACT, old_quote, AS_OF)
    assert result["metrics"]["quote_age_seconds"] == 172800
    assert result["status"] == "ok"  # Completeness, not an authorization to execute.
    assert result["metrics"]["midpoint_u"] == "3100000.5"


def test_roll_rejects_unrelated_contracts():
    with pytest.raises(ValueError, match="same symbol"):
        calculate(CONTRACT, SNAPSHOT, AS_OF, replacement={
            "contract": {**CONTRACT, "symbol": "SPY"}, "snapshot": SNAPSHOT,
        })


def test_roll_credit_does_not_disguise_old_option_loss():
    scenario = {"contracts": 1, "opening_premium_u": 2_000_000,
                "opening_fees_u": 650_000, "closing_fees_u": 650_000}
    replacement = {"contract": {**CONTRACT, "expiry_date": "2026-10-23"},
                   "snapshot": {**SNAPSHOT, "bid_u": 4_000_000, "ask_u": 4_200_000},
                   "opening_fees_u": 650_000}
    result = calculate(CONTRACT, SNAPSHOT, AS_OF, scenario, replacement=replacement)
    assert result["metrics"]["scenario_roll_net_cashflow_u"] == "78699900"
    assert result["metrics"]["scenario_net_option_pnl_u"] == "-121300100"
    without_fees = {key: value for key, value in replacement.items() if key != "opening_fees_u"}
    incomplete = calculate(CONTRACT, SNAPSHOT, AS_OF, scenario, replacement=without_fees)
    assert incomplete["metrics"]["scenario_roll_net_cashflow_u"] is None
    assert "incomplete_roll_fee_scenario" in incomplete["warnings"]
    with pytest.raises(ValueError, match="Unknown replacement"):
        calculate(CONTRACT, SNAPSHOT, AS_OF, replacement={**replacement, "fee": 0})
