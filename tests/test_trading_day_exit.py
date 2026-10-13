"""Synthetic regression cases for an explicitly supplied trading-session calendar."""
from __future__ import annotations

import copy

import pytest

from longarc.analytics.decisions import evaluate
from longarc.analytics.replay import replay
from longarc.data.option_capture import ingest_capture
from longarc.storage import store

SESSIONS = [
    "2026-06-17", "2026-06-18", "2026-06-22", "2026-06-23",
    "2026-06-24", "2026-06-25", "2026-06-26",
]


@pytest.fixture
def policy():
    return {
        "scope": {"underlying": "QQQ"},
        "policy_parameters": {
            "watch_delta": .22,
            "defend_delta": .32,
            "profit_capture_fraction": .6,
            "latest_exit_trading_days": 5,
            "time_exit_action": "roll_or_close",
            "entry_delta_range": [.05, .10],
            "entry_dte_range": [21, 35],
            "entry_max_spread_fraction_of_midpoint": .12,
            "entry_min_open_interest": 80,
        },
        "roll": {"max_rolls_per_episode": 1, "max_extra_calendar_days": 21},
    }


@pytest.fixture
def scenario():
    return {
        "as_of": "2026-06-18T19:00:00Z",
        "trading_calendar": {
            "source_ref": "synthetic-calendar-2026-06",
            "start_date": "2026-06-17",
            "end_date": "2026-06-26",
            "trading_dates": SESSIONS.copy(),
        },
        "facts": {
            "symbol": "QQQ", "option_type": "CALL", "position_contracts": 1,
            "position_verified": True, "coverage_verified": True, "orders_clear": True,
            "quote_usable": True, "greeks_usable": True, "underlying_usable": True,
            "dividend_window_clear": True, "market_open": True,
            "expiry_date": "2026-06-26", "delta": .10, "strike_u": 110_000_000,
            "underlying_price_u": 100_000_000, "bid_u": 750_000, "ask_u": 800_000,
            "opening_premium_u": 1_000_000, "multiplier": 100,
            "allocated_opening_fees_u": 650_000,
            "estimated_closing_total_fees_u": 650_000, "episode_roll_count": 0,
        },
    }


def replacement(scenario):
    return {
        **copy.deepcopy(scenario["facts"]),
        "contracts": 1, "strike_u": 115_000_000,
        "expiry_date": "2026-07-17", "delta": .08,
        "bid_u": 1_000_000, "ask_u": 1_050_000,
        "standard_contract": True, "sizing_approved": True,
        "reentry_cooldown_clear": True, "open_interest": 100,
        "opening_total_fees_u": 650_000,
    }


def test_holiday_is_excluded_and_five_session_boundary(scenario, policy):
    # June 19 is Juneteenth. Six weekdays remain, but only five sessions.
    result = evaluate(scenario, policy)
    assert result["metrics"]["trading_dte"] == 5
    assert result["checks"]["time_exit"] is True
    assert result["action"] == "BTC_RISK"
    assert "time_exit" in result["reasons"]

    scenario["as_of"] = "2026-06-17T19:00:00Z"
    result = evaluate(scenario, policy)
    assert result["metrics"]["trading_dte"] == 6
    assert result["checks"]["time_exit"] is False
    assert result["action"] == "HOLD"


def test_half_day_is_one_session(scenario, policy):
    scenario["as_of"] = "2026-11-20T19:00:00Z"
    scenario["facts"]["expiry_date"] = "2026-11-30"
    scenario["trading_calendar"] = {
        "source_ref": "synthetic-thanksgiving-week",
        "start_date": "2026-11-20",
        "end_date": "2026-11-30",
        # Nov. 27 is a shortened Friday session; Thanksgiving is absent.
        "trading_dates": ["2026-11-20", "2026-11-23", "2026-11-24",
                          "2026-11-25", "2026-11-27", "2026-11-30"],
    }
    result = evaluate(scenario, policy)
    assert result["metrics"]["trading_dte"] == 5
    assert result["checks"]["time_exit"] is True


def test_new_york_asof_date_excludes_today_not_utc_date(scenario, policy):
    scenario["as_of"] = "2026-06-18T00:30:00Z"  # June 17 in New York.
    result = evaluate(scenario, policy)
    assert result["metrics"]["trading_dte"] == 6
    assert result["checks"]["time_exit"] is False


@pytest.mark.parametrize("calendar", [
    None,
    {"source_ref": "synthetic", "start_date": "2026-06-19",
     "end_date": "2026-06-26", "trading_dates": SESSIONS[2:]},
    {"source_ref": "synthetic", "start_date": "2026-06-17",
     "end_date": "2026-06-25", "trading_dates": SESSIONS[:-1]},
])
def test_missing_or_incomplete_calendar_is_unknown_and_known_risks_win(
    scenario, policy, calendar,
):
    if calendar is None:
        scenario.pop("trading_calendar")
    else:
        scenario["trading_calendar"] = calendar
    result = evaluate(scenario, policy)
    assert result["metrics"]["trading_dte"] is None
    assert result["checks"]["time_exit"] is None
    assert result["action"] == "INSUFFICIENT_DATA"

    for field, value in (("delta", .32), ("underlying_price_u", 110_000_000),
                         ("dividend_window_clear", False)):
        changed = copy.deepcopy(scenario)
        changed["facts"][field] = value
        risky = evaluate(changed, policy)
        assert risky["action"] == "BTC_RISK"


def test_low_delta_qualified_roll_inside_time_exit_window(scenario, policy):
    scenario["replacement"] = replacement(scenario)
    result = evaluate(scenario, policy)
    assert scenario["facts"]["delta"] < policy["policy_parameters"]["watch_delta"]
    assert result["action"] == "ROLL_CANDIDATE"
    assert result["checks"]["time_exit"] is True
    assert result["metrics"]["net_roll_cashflow_u"] == 18_700_000
    assert all(value is True for key, value in result["checks"].items()
               if key.startswith("roll_"))


def test_time_exit_defaults_to_close_even_with_qualified_roll(scenario, policy):
    policy["policy_parameters"].pop("time_exit_action")
    scenario["replacement"] = replacement(scenario)
    result = evaluate(scenario, policy)
    assert result["action"] == "BTC_RISK"
    assert "time_exit" in result["reasons"]


@pytest.mark.parametrize("change", [
    {"episode_roll_count": 1},
    {"coverage_verified": False},
    {"orders_clear": False},
    {"quote_usable": False},
])
def test_failed_current_leg_gate_falls_back_to_close(scenario, policy, change):
    scenario["replacement"] = replacement(scenario)
    scenario["facts"].update(change)
    result = evaluate(scenario, policy)
    assert result["action"] == "BTC_RISK"
    assert "time_exit" in result["reasons"]


@pytest.mark.parametrize("change", [
    {"delta": .11},  # Above the new entry range.
    {"strike_u": 110_000_000},  # No higher strike.
    {"expiry_date": "2026-07-18"},  # More than 21 calendar days.
    {"bid_u": 700_000},  # Negative net credit after fees.
    {"contracts": 2},
    {"multiplier": 10},
    {"symbol": "IAU"},
    {"option_type": "PUT"},
    {"sizing_approved": False},
])
def test_failed_replacement_gate_falls_back_to_close(scenario, policy, change):
    scenario["replacement"] = replacement(scenario)
    scenario["replacement"].update(change)
    result = evaluate(scenario, policy)
    assert result["action"] == "BTC_RISK"
    assert "time_exit" in result["reasons"]


@pytest.mark.parametrize("change,reason", [
    ({"delta": .32}, "delta_exit"),
    ({"underlying_price_u": 110_000_000}, "strike_exit"),
    ({"dividend_window_clear": False}, "dividend_exit"),
])
def test_hard_risk_overrides_qualified_roll(scenario, policy, change, reason):
    scenario["replacement"] = replacement(scenario)
    scenario["facts"].update(change)
    result = evaluate(scenario, policy)
    assert result["action"] == "BTC_RISK"
    assert reason in result["reasons"]


@pytest.mark.parametrize("change", [
    {"source_ref": ""},
    {"trading_dates": SESSIONS[:2] + [SESSIONS[1]] + SESSIONS[2:]},
    {"trading_dates": SESSIONS[:2] + ["2026-06-20"] + SESSIONS[2:]},
    {"trading_dates": ["2026-06-16"] + SESSIONS},
    {"trading_dates": SESSIONS[1:] + [SESSIONS[0]]},
])
def test_malformed_calendar_rejected(scenario, policy, change):
    scenario["trading_calendar"].update(change)
    with pytest.raises(ValueError):
        evaluate(scenario, policy)


def test_exit_policy_requires_exactly_one_dte_mode(scenario, policy):
    policy["policy_parameters"]["latest_exit_dte"] = 5
    with pytest.raises(ValueError):
        evaluate(scenario, policy)
    policy["policy_parameters"].pop("latest_exit_trading_days")
    result = evaluate(scenario, policy)
    assert result["metrics"]["dte"] == 8
    assert result["checks"]["time_exit"] is False
    assert result["action"] == "HOLD"


def test_legacy_watch_roll_reason_is_unchanged(scenario, policy):
    policy["policy_parameters"].pop("latest_exit_trading_days")
    policy["policy_parameters"]["latest_exit_dte"] = 5
    scenario["facts"]["delta"] = .22
    scenario["replacement"] = replacement(scenario)
    result = evaluate(scenario, policy)
    assert result["action"] == "ROLL_CANDIDATE"
    assert result["reasons"] == ["watch_range_and_replacement_screen_passed"]


def test_replay_passes_frame_calendar_to_time_exit(tmp_path, policy, scenario):
    db = tmp_path / "session-replay.sqlite3"
    store.initialize(db)

    def observed_frame(day, bid, ask):
        stamp = f"2026-06-{day:02}T19:00:00Z"
        raw = {
            "format": "schwab-browser-chain-v1", "symbol": "QQQ",
            "captured_at": stamp,
            "slices": [{
                "expiry_date": "2026-06-26",
                "headers": ["Strike", "Bid", "Ask", "Delta", "Open Interest"],
                "rows": [["110", bid, ask, ".08", "200"]],
                "underlying_price": "100", "underlying_at": stamp,
                "quote_at": stamp, "greeks_at": stamp,
            }],
        }
        saved = ingest_capture(db, raw, mode="shadow")
        checks = dict.fromkeys([
            "market_open", "dividend_window_clear", "quote_usable",
            "greeks_usable", "underlying_usable", "standard_contract",
            "reentry_cooldown_clear",
        ], True)
        return {"record_id": saved["record_id"], "checks": checks}

    opening = observed_frame(1, "1", "1.05")
    near_expiry = observed_frame(18, ".75", ".8")
    near_expiry["trading_calendar"] = scenario["trading_calendar"]
    replay_request = {
        "episode_id": "synthetic-session-exit",
        "as_of": "2026-06-18T19:00:00Z",
        "contract": {"expiry_date": "2026-06-26", "strike_u": 110_000_000},
        "assumptions": {
            "contracts": 1, "multiplier": 100, "covered_shares": 100,
            "max_gap_hours": 500, "max_quote_age_seconds": 60,
            "max_greeks_age_seconds": 60, "max_underlying_age_seconds": 60,
            "opening_slippage_u": 0, "closing_slippage_u": 0,
            "opening_extra_fees_u": 0, "closing_extra_fees_u": 0,
            "allow_roll": False,
        },
        "observations": [opening, near_expiry],
    }
    fees = {"as_of": "2026-06-01", "base_fee_u": 0,
            "per_contract_fee_u": 650_000, "btc_waiver_threshold_u": 50_000}
    result = replay(db, replay_request, policy, fees)
    assert [d["action"] for d in result["decisions"]] == ["STO_CANDIDATE", "BTC_RISK"]
    assert result["decisions"][1]["metrics"]["trading_dte"] == 5
    assert result["decisions"][1]["checks"]["time_exit"] is True
