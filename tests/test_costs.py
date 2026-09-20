"""Cost scenarios use explicit microdollars and distinguish unknown from zero."""

import json
from pathlib import Path

import pytest

from longarc.analytics.costs import calculate_costs

SCHEDULE = json.loads(Path("config/options-costs.json").read_text())


def scenario(**kwargs):
    inputs = dict(contracts=1, multiplier=100, opening_premium_u=1_100_000,
                  closing_ask_u=500_000, opening_extra_fees_u=0, closing_extra_fees_u=0)
    inputs.update(kwargs)
    return calculate_costs(SCHEDULE, **inputs)


def test_fees_scale_by_contract_not_order():
    result = scenario(contracts=6)
    assert result["metrics"]["opening_fees_u"] == "3900000"
    assert result["metrics"]["closing_fees_u"] == "3900000"
    assert result["metrics"]["net_option_pnl_u"] == "352200000"


@pytest.mark.parametrize("price,fee", [(49_999, "0"), (50_000, "0"), (50_001, "650000")])
def test_btc_waiver_boundary(price, fee):
    assert scenario(closing_ask_u=price)["metrics"]["closing_fees_u"] == fee


def test_slippage_can_cross_waiver_boundary():
    result = scenario(closing_ask_u=50_000, closing_slippage_u=1)
    assert result["metrics"]["closing_fees_u"] == "650000"


def test_actual_all_in_fees_override_estimates_and_extras_including_zero():
    result = scenario(opening_actual_fees_u=0, closing_actual_fees_u=123,
                      opening_extra_fees_u=999, closing_extra_fees_u=None)
    assert result["fees_complete"]
    assert result["metrics"]["opening_fees_u"] == "0"
    assert result["metrics"]["closing_fees_u"] == "123"
    assert result["metrics"]["net_option_pnl_u"] == "59999877"


def test_unknown_extras_are_not_claimed_exact_profit():
    result = scenario(opening_extra_fees_u=None)
    assert result["status"] == "partial"
    assert not result["fees_complete"]
    assert result["metrics"]["net_option_pnl_u"] is None
    assert result["metrics"]["known_cost_net_option_pnl_u"] == "58700000"
    assert "unknown_opening_extra_fees" in result["warnings"]


def test_ask_and_opening_fill_already_include_spread():
    result = scenario(closing_bid_u=460_000, opening_mid_u=1_120_000)
    assert result["metrics"]["closing_spread_vs_midpoint_u"] == "2000000"
    assert result["metrics"]["opening_spread_vs_midpoint_u"] == "2000000"
    assert result["metrics"]["net_option_pnl_u"] == "58700000"


def test_explicit_slippage_is_separate_from_spread():
    result = scenario(closing_bid_u=460_000, closing_slippage_u=10_000)
    assert result["metrics"]["closing_spread_vs_midpoint_u"] == "2000000"
    assert result["metrics"]["closing_slippage_total_u"] == "1000000"
    assert result["metrics"]["net_option_pnl_u"] == "57700000"


def test_fill_takes_precedence_over_quotes():
    result = scenario(closing_fill_u=400_000)
    assert result["price_basis"] == "actual_fill"
    assert result["metrics"]["net_option_pnl_u"] == "68700000"
    with pytest.raises(ValueError, match="already includes"):
        scenario(closing_fill_u=400_000, closing_slippage_u=1)


@pytest.mark.parametrize("quote,basis", [({"closing_mid_u": 500_000}, "midpoint_benchmark"),
                                        ({"closing_bid_u": 400_000}, "bid_benchmark")])
def test_non_executable_benchmarks_warn(quote, basis):
    result = scenario(closing_ask_u=None, **quote)
    assert result["price_basis"] == basis
    assert "closing_price_is_not_executable_buy_quote" in result["warnings"]


@pytest.mark.parametrize("changes", [
    {"contracts": 0}, {"multiplier": True}, {"opening_premium_u": -1},
    {"closing_ask_u": float("nan")}, {"closing_mid_u": float("inf")},
    {"opening_actual_fees_u": "0"}, {"closing_slippage_u": -1},
    {"closing_bid_u": 600_000}, {"closing_bid_u": 400_000, "closing_mid_u": 600_000},
    {"closing_ask_u": None},
])
def test_invalid_inputs_rejected(changes):
    with pytest.raises(ValueError):
        scenario(**changes)


def test_invalid_schedule_rejected():
    with pytest.raises(ValueError):
        calculate_costs({**SCHEDULE, "per_contract_fee_u": float("nan")},
                        contracts=1, multiplier=100, opening_premium_u=1_000_000,
                        closing_ask_u=500_000)
