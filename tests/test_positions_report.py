from copy import deepcopy
from pathlib import Path

import pytest

from longarc.analytics.executions import record_execution
from longarc.analytics.positions import position_report, render_markdown
from longarc.storage import store

CONTRACT = dict(symbol="QQQ", option_type="CALL", expiry_date="2026-10-16",
                strike_u=770000000, multiplier=100)
FEES = dict(as_of="2026-09-01", base_fee_u=0, per_contract_fee_u=650000,
            btc_waiver_threshold_u=50000, closing_extra_fees_u=10000)
AS_OF = "2026-09-21T20:00:00Z"


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    path = tmp_path / "positions.sqlite3"
    store.initialize(path)
    monkeypatch.setattr(store, "utc_now", lambda: "2026-09-21T19:00:00Z")
    return path


def execution(db, key="open", action="STO", **updates):
    data = dict(account="test", mode="manual", external_execution_id=key,
                action=action, contract=deepcopy(CONTRACT), quantity=2,
                price_u=2000000, fees_u=1300000, executed_at="2026-09-20T20:00:00Z",
                source="test-confirmation", evidence_ref="fixture:fill", episode_id="episode")
    if action != "STO":
        data.update(opening_execution_id="open", quantity=1, price_u=1000000,
                    fees_u=650000, executed_at="2026-09-21T18:00:00Z")
    return record_execution(db, data | updates)["record_id"]


def quote(db, key="quote", captured="2026-09-21T19:59:00Z", *, source="feed",
          contract=None, mode="observe", supersedes=None, **updates):
    snap = dict(captured_at=captured, bid_u=900000, ask_u=1000000, delta=.1, theta=-.03,
                underlying_price_u=740000000, quote_at=captured, greeks_at=captured,
                underlying_at=captured)
    snap.update(updates)
    data = dict(idempotency_key=key, scope="options:QQQ", mode=mode,
                kind="observation", observed_at=captured, source=source,
                quality="synthetic" if mode == "shadow" else "unverified",
                code_version="fixture", inputs={"format": "option-chain-v1"},
                results={"quotes": [{"contract": contract or deepcopy(CONTRACT),
                                      "snapshot": snap}], "status": "ok"}, evidence_ids=[])
    if supersedes:
        data["supersedes_id"] = supersedes
    return store.save_observation(db, data)["record_id"]


def report(db, **updates):
    return position_report(db, **(dict(account="test", as_of=AS_OF, fees=FEES) | updates))


def test_partial_close_remaining_fee_and_realized_separate(db):
    execution(db)
    execution(db, "close", "BTC")
    quote(db)
    before = db.read_bytes()
    result = report(db)
    assert db.read_bytes() == before
    lot = result["open_lots"][0]
    assert lot["remaining_quantity"] == 1
    assert lot["remaining_opening_fees_u"] == 650000
    scenario = lot["quote_series"][0]["close_scenario"]
    assert scenario["gross_option_pnl_u"] == "100000000"
    assert scenario["buyback_premium_u"] == "100000000"
    assert scenario["buyback_total_u"] == "100660000"
    assert scenario["net_option_pnl_u"] == "98690000"
    assert scenario["premium_capture_fraction"] == .5
    assert result["realized"]["realized_net_option_pnl_u"] == "98700000"
    assert "scenario_not_realized_profit" in scenario["warnings"]
    assert "historical estimates" in render_markdown(result)


def test_empty_ledger_markdown_labels_recorded_subtotal_not_account_income(db):
    markdown = render_markdown(report(db))
    assert "Recorded all-time realized net option P&L subtotal through as-of: $0.0000" in markdown
    assert "Complete account realized option P&L is unknown" in markdown
    assert "an empty ledger does not establish zero income" in markdown


def test_residual_fee_uses_ledger_rounding(db):
    execution(db, quantity=3, fees_u=1)
    execution(db, "close", "BTC", quantity=2, fees_u=0)
    quote(db)
    assert report(db)["open_lots"][0]["remaining_opening_fees_u"] == 0


def test_as_of_excludes_future_execution_capture_and_late_record(db, monkeypatch):
    execution(db)
    execution(db, "future-close", "BTC", executed_at="2026-09-22T18:00:00Z")
    quote(db, "valid")
    quote(db, "future", captured="2026-09-22T19:00:00Z", ask_u=100000)
    monkeypatch.setattr(store, "utc_now", lambda: "2026-09-22T19:00:00Z")
    quote(db, "late", captured="2026-09-21T19:59:30Z", ask_u=200000)
    execution(db, "late-open")
    result = report(db)
    assert result["realized"]["execution_count"] == 1
    lot = result["open_lots"][0]
    assert lot["remaining_quantity"] == 2
    assert len(lot["quote_series"][0]["history"]) == 1
    assert lot["quote_series"][0]["latest"]["ask_u"] == 1000000


def test_superseded_as_of_cutoff(db, monkeypatch):
    execution(db)
    old = quote(db)
    monkeypatch.setattr(store, "utc_now", lambda: "2026-09-22T19:00:00Z")
    quote(db, "correction", supersedes=old, ask_u=1200000)
    assert report(db)["open_lots"][0]["quote_series"][0]["latest"]["ask_u"] == 1000000
    later = report(db, as_of="2026-09-22T20:00:00Z")
    series = later["open_lots"][0]["quote_series"][0]
    assert len(series["history"]) == 1
    assert series["latest"]["ask_u"] == 1200000


def test_missing_stale_future_source_times(db):
    execution(db)
    quote(db, quote_at=None, greeks_at=None, underlying_at=None)
    latest = report(db)["open_lots"][0]["quote_series"][0]["latest"]
    assert latest["valuation_freshness"] == "unknown"
    assert latest["freshness"]["greeks_at"] == "unknown"
    assert latest["price_valid"]
    quote(db, "stale", captured="2026-09-21T19:59:30Z",
          quote_at="2026-09-21T18:00:00Z")
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert series["latest"]["valuation_freshness"] == "stale"
    assert series["close_scenario"]["valuation_freshness"] == "stale"
    quote(db, "future-source", captured="2026-09-21T19:59:45Z",
          quote_at="2026-09-22T18:00:00Z", greeks_at="2026-09-22T18:00:00Z")
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert series["latest"]["delta"] is None
    assert series["close_scenario"] is None


@pytest.mark.parametrize("changes", [{"bid_u": None}, {"ask_u": None},
                                    {"bid_u": 2000000}, {"ask_u": True}])
def test_never_falls_back_to_older_good_quote(db, changes):
    execution(db)
    quote(db)
    quote(db, "bad", captured="2026-09-21T19:59:30Z", **changes)
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert len(series["history"]) == 2
    assert not series["latest"]["price_valid"]
    assert series["close_scenario"] is None


def test_known_mismatched_multiplier_is_not_marked(db):
    execution(db)
    quote(db, contract=CONTRACT | {"multiplier": 10})
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert series["close_scenario"] is None
    assert "mismatched_multiplier" in series["latest"]["warnings"]
    assert series["latest"]["contract_match_verified"] is False


def test_unknown_multiplier_has_explicit_conditional_scenario(db):
    execution(db)
    quote(db, contract=CONTRACT | {"multiplier": None})
    result = report(db)
    series = result["open_lots"][0]["quote_series"][0]
    assert series["latest"]["contract_match_verified"] is None
    scenario = series["close_scenario"]
    assert scenario["contract_match_verified"] is None
    assert scenario["multiplier_used"] == 100
    assert scenario["buyback_premium_u"] == "200000000"
    assert scenario["assumptions"] == [
        "Quoted option has the same deliverable and multiplier as recorded lot"]
    assert "Assumption: Quoted option" in render_markdown(result)


def test_ambiguous_simultaneous_quotes_and_separate_providers(db):
    execution(db)
    quote(db)
    quote(db, "adjusted", contract=CONTRACT | {"option_symbol": "adjusted"})
    quote(db, "other", source="other-provider", ask_u=1200000)
    lot = report(db)["open_lots"][0]
    assert "multiple_sources_valued_separately_no_combined_mark" in lot["warnings"]
    assert len(lot["quote_series"]) == 2
    assert lot["quote_series"][0]["close_scenario"] is None
    assert lot["quote_series"][1]["close_scenario"]["buyback_premium_u"] == "240000000"


def test_unknown_opening_and_closing_fees_stay_unknown(db):
    execution(db, fees_u=None)
    quote(db)
    scenario = report(db)["open_lots"][0]["quote_series"][0]["close_scenario"]
    assert scenario["net_option_pnl_u"] is None
    assert not scenario["fees_complete"]
    assert "unknown_actual_opening_fees" in scenario["warnings"]
    assert scenario["buyback_total_u"] == "201310000"
    fees = {k: v for k, v in FEES.items() if k != "closing_extra_fees_u"}
    scenario = report(db, fees=fees)["open_lots"][0]["quote_series"][0]["close_scenario"]
    assert scenario["net_option_pnl_u"] is None
    assert scenario["buyback_total_u"] is None
    assert scenario["estimated_closing_fees_u"] is None
    scenario = report(db, fees={})["open_lots"][0]["quote_series"][0]["close_scenario"]
    assert "unknown_closing_fee_schedule" in scenario["warnings"]
    assert scenario["gross_option_pnl_u"] == "200000000"


def test_no_matching_quote_symbol_account_mode_isolation(db):
    execution(db)
    quote(db, mode="shadow")
    quote(db, "put", contract=CONTRACT | {"option_type": "PUT"})
    quote(db, "strike", contract=CONTRACT | {"strike_u": 771000000})
    quote(db, "expiry", contract=CONTRACT | {"expiry_date": "2026-11-20"})
    quote(db, "before", captured="2026-09-19T20:00:00Z")
    assert report(db)["open_lots"][0]["quote_series"] == []
    assert report(db, account="another")["open_lots"] == []
    assert report(db, symbol="IAU")["open_lots"] == []
    execution(db, "shadow", mode="shadow")
    shadow = report(db, mode="shadow")
    assert shadow["quote_mode"] == "shadow"
    assert len(shadow["open_lots"][0]["quote_series"]) == 1
    assert shadow["realized"]["execution_count"] == 1


def test_closed_lot_omitted_and_inputs_validated(db):
    execution(db)
    execution(db, "close", "BTC", quantity=2)
    assert report(db)["open_lots"] == []
    for updates in ({"mode": "observe"}, {"as_of": "2026-09-21"},
                    {"max_age_seconds": -1}, {"symbol": "qqq"},
                    {"fees": FEES | {"as_of": "2026-09-22"}},
                    {"fees": {"as_of": "2026-09-01"}}):
        with pytest.raises(ValueError):
            report(db, **updates)


def test_invalid_capture_does_not_resurrect_old_quote(db):
    execution(db)
    quote(db)
    quote(db, "bad-clock", captured="2026-09-21T19:59:30Z", captured_at="invalid")
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert series["latest"]["captured_at"] is None
    assert series["close_scenario"] is None
    assert "missing_or_invalid_capture_timestamp" in series["latest"]["warnings"]


def test_requested_missing_contract_blocks_older_quote(db):
    execution(db)
    first = quote(db)
    payload = store.get_observation(db, first)["payload"]
    payload.update(idempotency_key="missing", observed_at="2026-09-21T19:59:30Z")
    payload["results"].update(quotes=[], missing_contracts=[{
        "expiry_date": CONTRACT["expiry_date"], "strike_u": CONTRACT["strike_u"]}])
    store.save_observation(db, payload)
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert series["close_scenario"] is None
    assert "contract_missing_from_requested_capture" in series["latest"]["warnings"]


def test_different_option_symbols_not_silently_merged(db):
    execution(db)
    quote(db, contract=CONTRACT | {"option_symbol": "standard"})
    quote(db, "new", captured="2026-09-21T19:59:30Z",
          contract=CONTRACT | {"option_symbol": "adjusted"})
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert series["close_scenario"] is None
    assert "ambiguous_option_symbols_for_execution_contract" in series["latest"]["warnings"]


def test_invalid_raw_price_is_safe_in_markdown(db):
    execution(db)
    quote(db, ask_u=True)
    assert "unknown" in render_markdown(report(db))


def test_known_cost_subtotal_with_unknown_closing_extra_fees(db):
    execution(db)
    quote(db)
    fees = {k: v for k, v in FEES.items() if k != "closing_extra_fees_u"}
    result = report(db, fees=fees)
    scenario = result["open_lots"][0]["quote_series"][0]["close_scenario"]
    assert scenario["net_option_pnl_u"] is None
    assert scenario["known_cost_net_option_pnl_u"] == "197400000"
    assert "known-cost subtotal; incomplete costs: $197.4000" in render_markdown(result)
    no_schedule = report(db, fees={})["open_lots"][0]["quote_series"][0]["close_scenario"]
    assert no_schedule["known_cost_net_option_pnl_u"] is None
    assert no_schedule["gross_option_pnl_u"] == "200000000"


def test_unknown_actual_opening_fee_has_no_known_cost_subtotal(db):
    execution(db, fees_u=None)
    quote(db)
    scenario = report(db)["open_lots"][0]["quote_series"][0]["close_scenario"]
    assert scenario["known_cost_net_option_pnl_u"] is None


def test_position_row_limit_fails_without_partial_financial_totals(db):
    execution(db)
    quote(db)
    assert len(report(db, limit=2)["open_lots"]) == 1
    with pytest.raises(ValueError, match="row limit exceeded"):
        report(db, limit=1)
    for limit in (0, -1, 100001, True):
        with pytest.raises(ValueError):
            report(db, limit=limit)


@pytest.mark.parametrize("captured_at", ["2026-09-21T19:59:45Z", "2026-09-22T00:00:00Z"])
def test_capture_after_batch_observation_blocks_scenario(db, captured_at):
    execution(db)
    quote(db)
    quote(db, "bad-clock", captured="2026-09-21T19:59:30Z", captured_at=captured_at)
    series = report(db)["open_lots"][0]["quote_series"][0]
    assert series["latest"]["captured_at"] is None
    assert series["latest"]["delta"] is None
    assert "capture_postdates_observation" in series["latest"]["warnings"]
    assert series["close_scenario"] is None
