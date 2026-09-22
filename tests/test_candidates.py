import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from longarc.analytics.candidates import compare_candidates
from longarc.storage import store

NOW = "2026-09-21T19:00:00Z"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "candidates.sqlite3"
    store.initialize(path)
    return path


@pytest.fixture
def policy():
    return {"scope": {"underlying": "QQQ"}, "policy_parameters": {
        "entry_delta_range": [.04, .12], "entry_dte_range": [20, 40],
        "entry_max_spread_fraction_of_midpoint": .12, "entry_min_open_interest": 80,
    }}


@pytest.fixture
def fees():
    return {"as_of": "2026-09-01", "base_fee_u": 0, "per_contract_fee_u": 650000,
            "btc_waiver_threshold_u": 50000, "opening_extra_fees_u": 10000,
            "closing_extra_fees_u": 10000}


@pytest.fixture
def quote():
    return {"contract": {"symbol": "QQQ", "option_type": "CALL", "expiry_date": "2026-10-16",
                         "strike_u": 790000000, "multiplier": None},
            "snapshot": {"captured_at": "2026-09-21T18:59:00Z", "bid_u": 1000000,
                         "ask_u": 1100000, "delta": .08, "open_interest": 120,
                         "underlying_price_u": 750000000, "probability_touch": .2,
                         "probability_otm": .91}}


def save(db, quotes, key="batch", **kwargs):
    event = dict(idempotency_key=key, scope="options:QQQ", mode="shadow", kind="observation",
                 observed_at=NOW, source="fixture", quality="synthetic", code_version="test",
                 inputs={"format": "option-chain-v1", "symbol": "QQQ"},
                 results={"status": "partial", "complete": False, "quotes": quotes,
                          "missing_expiries": ["2026-10-23"]}, evidence_ids=[])
    event.update(kwargs)
    return store.save_observation(db, event)["record_id"]


def report(db, policy, fees, record_id, **kwargs):
    return compare_candidates(db, observation_id=record_id, as_of=NOW,
                              policy=policy, fees=fees, **kwargs)


def test_partial_single_batch_policy_and_readonly(db, policy, fees, quote):
    first = save(db, [quote])
    other = copy.deepcopy(quote)
    other["snapshot"]["bid_u"] = 800000
    save(db, [other], "other")
    before = db.read_bytes()
    result = report(db, policy, fees, first)
    row = result["candidates"][0]
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["missing_expiries"] == ["2026-10-23"]
    assert result["coverage"]["candidate_count"] == 1
    assert row["snapshot"]["bid_u"] == 1000000
    assert row["metrics"]["dte_calendar_days"] == 25
    assert row["metrics"]["strike_distance_u"] == "40000000"
    assert row["quote_policy_thresholds_pass"] is True
    assert row["entry_eligible"] is None
    assert row["timing"]["quote"]["time_consistent"] is None
    assert row["provider_probabilities"]["probability_otm"] == "0.91"
    scenario = row["half_premium_buyback_scenario"]
    assert scenario["metrics"]["net_option_pnl_u"] == "48680000"
    assert json.loads(json.dumps(result)) == result
    assert before == db.read_bytes()


def test_missing_extras_preserve_unknown_net(db, policy, fees, quote):
    fees.pop("closing_extra_fees_u")
    row = report(db, policy, fees, save(db, [quote]))["candidates"][0]
    scenario = row["half_premium_buyback_scenario"]
    assert scenario["metrics"]["net_option_pnl_u"] is None
    assert scenario["metrics"]["known_cost_net_option_pnl_u"] == "48690000"
    assert "unknown_closing_extra_fees" in scenario["warnings"]


@pytest.mark.parametrize("bid,waived", [(100000, True), (100001, False)])
def test_exact_half_microdollars_and_fee_waiver(db, policy, fees, quote, bid, waived):
    quote["snapshot"]["bid_u"] = bid
    scenario = report(db, policy, fees, save(db, [quote]), contracts=3)["candidates"][0][
        "half_premium_buyback_scenario"]
    metrics = scenario["metrics"]
    assert Decimal(metrics["closing_price_u"]) == Decimal(bid) / 2
    assert Decimal(metrics["gross_option_pnl_u"]) == Decimal(bid) * 150
    assert metrics["estimated_closing_contract_fee_waived"] is waived
    assert Decimal(metrics["known_cost_net_option_pnl_u"]) == (
        Decimal(bid) * 150 - 1960000 - (10000 if waived else 1960000))


@pytest.mark.parametrize("changes", [{"bid_u": None}, {"bid_u": 1200000},
                                     {"quote_at": "2026-09-21T19:01:00Z"},
                                     {"quote_at": "2026-09-21T18:59:30Z"},
                                     {"captured_at": "2026-09-21T19:01:00Z"}])
def test_unusable_prices_never_produce_scenario(db, policy, fees, quote, changes):
    quote["snapshot"].update(changes)
    row = report(db, policy, fees, save(db, [quote]))["candidates"][0]
    assert row["half_premium_buyback_scenario"] is None
    assert row["quote_policy_checks"]["liquidity_spread"] is None
    assert row["entry_eligible"] is None


def test_missing_and_failed_thresholds(db, policy, fees, quote):
    quote["snapshot"].update(delta=.2, open_interest=None, underlying_price_u=None)
    row = report(db, policy, fees, save(db, [quote]))["candidates"][0]
    assert row["quote_policy_checks"]["entry_delta"] is False
    assert row["quote_policy_checks"]["liquidity_oi"] is None
    assert row["quote_policy_checks"]["otm"] is None
    assert row["quote_policy_thresholds_pass"] is False


def test_symbol_mismatch_and_legacy_policy(db, policy, fees, quote):
    record_id = save(db, [quote])
    policy["scope"]["underlying"] = "IAU"
    with pytest.raises(ValueError, match="Policy underlying"):
        report(db, policy, fees, record_id)
    del policy["scope"]
    assert report(db, policy, fees, record_id)["symbol"] == "QQQ"
    quote["contract"]["symbol"] = "IAU"
    with pytest.raises(ValueError, match="Every candidate"):
        report(db, policy, fees, save(db, [quote], "mismatch"))


def test_superseded_duplicate_and_wrong_format_rejected(db, policy, fees, quote):
    first = save(db, [quote])
    save(db, [quote], "replacement", supersedes_id=first)
    with pytest.raises(ValueError, match="superseded"):
        report(db, policy, fees, first)
    with pytest.raises(ValueError, match="Duplicate"):
        report(db, policy, fees, save(db, [quote, quote], "duplicate"))
    with pytest.raises(ValueError, match="canonical"):
        report(db, policy, fees, save(db, [quote], "wrong", inputs={"format": "legacy"}))


def test_window_clocks_and_multiplier_mismatch(db, policy, fees, quote):
    other = copy.deepcopy(quote)
    other["contract"].update(strike_u=795000000, multiplier=10)
    other["snapshot"]["captured_at"] = "2026-09-21T18:49:00Z"
    result = report(db, policy, fees, save(db, [quote, other]))
    assert result["coverage"]["capture_span_seconds"] == 600
    assert result["candidates"][1]["half_premium_buyback_scenario"] is None
    assert "scenario_multiplier_does_not_match_contract" in result["candidates"][1]["warnings"]


@pytest.mark.parametrize("captured,warning,consistent", [
    ("2026-09-21T19:01:00Z", "capture_timestamp_after_observation", False),
    (None, "missing_captured_at", None),
])
def test_missing_or_after_observation_capture_is_unqualified(
    db, policy, fees, quote, captured, warning, consistent,
):
    quote["snapshot"].update(captured_at=captured, quote_at="2026-09-21T18:58:00Z")
    record_id = save(db, [quote])
    # Evaluation may be later than collection; this cannot fix an internally
    # inconsistent saved frame, nor establish when an unstamped row was captured.
    row = compare_candidates(db, observation_id=record_id, as_of="2026-09-21T20:00:00Z",
                             policy=policy, fees=fees)["candidates"][0]
    assert row["half_premium_buyback_scenario"] is None
    assert row["quote_policy_thresholds_pass"] is False
    assert row["quote_policy_checks"]["entry_delta"] is None
    assert row["quote_policy_checks"]["liquidity_oi"] is None
    assert row["timing"]["capture"]["time_consistent"] is consistent
    assert row["timing"]["quote"]["time_consistent"] is consistent
    assert warning in row["warnings"]
    assert row["entry_eligible"] is None


def test_evaluation_clock_does_not_filter_later_database_import(
    db, policy, fees, quote, monkeypatch,
):
    monkeypatch.setattr(store, "utc_now", lambda: "2026-09-22T19:00:00Z")
    record_id = save(db, [quote])
    assert store.get_observation(db, record_id)["recorded_at"] > NOW
    result = report(db, policy, fees, record_id)
    assert result["observed_at"] == "2026-09-21T19:00:00.000000Z"
    assert result["candidates"][0]["half_premium_buyback_scenario"] is not None


@pytest.mark.parametrize("extras_known", [True, False])
def test_fractional_scenario_cashflows_are_consistent(db, policy, fees, quote, extras_known):
    quote["snapshot"]["bid_u"] = 100001
    if not extras_known:
        fees.pop("closing_extra_fees_u")
    scenario = report(db, policy, fees, save(db, [quote]), contracts=3)["candidates"][0][
        "half_premium_buyback_scenario"]
    metrics = scenario["metrics"]
    opening = Decimal(scenario["opening_price_u"])
    closing = Decimal(metrics["closing_price_u"])
    units = scenario["contracts"] * scenario["multiplier"]
    assert (opening - closing) / opening == Decimal(scenario["gross_capture_fraction"])
    gross = (opening - closing) * units
    assert Decimal(metrics["gross_option_pnl_u"]) == gross
    known_net = gross - Decimal(metrics["opening_fees_u"]) - Decimal(metrics["closing_fees_u"])
    assert Decimal(metrics["known_cost_net_option_pnl_u"]) == known_net
    if extras_known:
        assert Decimal(metrics["net_option_pnl_u"]) == known_net
    else:
        assert metrics["net_option_pnl_u"] is None
    assert metrics["closing_slippage_total_u"] == "0"
    assert metrics["closing_spread_vs_midpoint_u"] is None
    assert metrics["opening_spread_vs_midpoint_u"] is None
