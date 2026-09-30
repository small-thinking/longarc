import copy
import json
from unittest.mock import patch

import pytest

from longarc.analytics.iv_history import context_metrics, iv_history
from longarc.cli import main
from longarc.storage import store


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "iv.sqlite3"
    store.initialize(path)
    return path


def capture(db, day, iv=.2, *, hour=18, source="browser", mode="observe",
            recorded_at=None, supersedes=None, changes=None, extra=None, key=None, quality=None):
    stamp = f"2026-09-{day:02d}T{hour:02d}:00:00Z"
    snapshot = dict(captured_at=stamp, quote_at=stamp, greeks_at=stamp,
                    bid_u=1000000, ask_u=1100000, iv=iv, iv_unit="fraction", delta=.075)
    snapshot.update(changes or {})
    quote = dict(contract=dict(symbol="QQQ", option_type="CALL", expiry_date="2026-10-16",
                               strike_u=795000000, multiplier=100), snapshot=snapshot)
    quotes = [quote, *extra] if extra else [quote]
    p = dict(idempotency_key=key or f"{source}-{mode}-{day}-{hour}-{iv}",
             scope="options:QQQ", mode=mode, kind="observation", observed_at=stamp,
             source=source, quality=quality or ("synthetic" if mode == "shadow" else "unverified"),
             code_version="test", inputs={"format": "option-chain-v1", "symbol": "QQQ"},
             results={"quotes": quotes}, evidence_ids=[], supersedes_id=supersedes)
    with patch.object(store, "utc_now", return_value=recorded_at or stamp):
        return store.save_observation(db, p)["record_id"]


def report(db, **kwargs):
    return iv_history(db, symbol="QQQ", source="browser", as_of="2026-10-01T12:00:00Z",
                      min_samples=2, **kwargs)


def test_percentage_points_variance_units_and_short_call_sign():
    m = context_metrics(.3, [.2, .25], min_samples=2, forecast_volatility=.2,
                        reference_iv=.2, vega_per_vol_point=.09)
    assert m["iv_percentile_pct"] == 100
    assert m["iv_rank_pct"] == 100
    assert m["forecast_variance_spread_proxy"]["annualized_variance_spread"] == pytest.approx(.05)
    assert m["forecast_variance_spread_proxy"]["volatility_spread_percentage_points"] \
        == pytest.approx(10)
    assert m["iv_only_sensitivity_scenario"]["short_call_gross_iv_only_pnl_usd"] == 90
    adverse = context_metrics(.2, [], reference_iv=.3, vega_per_vol_point=.09,
                              contracts=2, multiplier=50)
    assert adverse["iv_only_sensitivity_scenario"]["short_call_gross_iv_only_pnl_usd"] == -90


def test_flat_history_ties_and_sample_gate_do_not_invent_statistics():
    m = context_metrics(.2, [.2, .2], min_samples=2)
    assert m["iv_percentile_pct"] == 0  # strictly below; ties are explicitly excluded
    assert m["iv_rank_pct"] is None
    m = context_metrics(.3, [.2], min_samples=2, vega_per_vol_point=.1)
    assert m["historical_median_iv"] == .2
    assert m["iv_percentile_pct"] is None
    assert m["iv_only_sensitivity_scenario"] is None  # median is not a calibrated reference


@pytest.mark.parametrize("kwargs", [dict(iv=float("nan")), dict(reference_iv=-.1),
                                   dict(forecast_volatility=True), dict(contracts=0),
                                   dict(multiplier=True), dict(vega_per_vol_point=-.1)])
def test_invalid_scalar_assumptions_are_rejected(kwargs):
    args = dict(iv=.2, history=[])
    args.update(kwargs)
    with pytest.raises(ValueError):
        context_metrics(**args)


def test_daily_weighting_reference_day_exclusion_and_source_mode_isolation(db):
    capture(db, 21, .2)
    late = capture(db, 21, .25, hour=19)
    second = capture(db, 22, .3)
    capture(db, 23, .35)
    capture(db, 23, .9, hour=19, source="other")
    capture(db, 23, .9, hour=19, mode="shadow")
    with pytest.raises(ValueError, match="Synthetic observations must use shadow"):
        capture(db, 23, .95, hour=20, quality="synthetic")
    before = db.read_bytes()
    result = report(db)
    assert result["status"] == "ok"
    assert len(result["daily_points"]) == 3
    assert result["distribution_point_ids"] == [late, second]
    assert result["metrics"]["historical_median_iv"] == pytest.approx(.275)
    assert result["metrics"]["iv_percentile_pct"] == 100
    assert db.read_bytes() == before


def test_no_fallback_or_selection_by_available_iv(db):
    capture(db, 21)
    capture(db, 22, .25)
    capture(db, 23, .3)
    capture(db, 23, 30, hour=19, changes={"iv_unit": "provider_display"})
    result = report(db)
    assert result["reference_point"]["snapshot"]["iv"] == 30
    assert result["status"] == "insufficient_comparable_iv"
    assert result["metrics"]["iv_percentile_pct"] is None
    assert result["metrics"]["historical_samples"] == 2
    assert "unverified_iv_unit" in result["reference_point"]["blockers"]


@pytest.mark.parametrize("changes,reason", [
    ({"quote_at": None}, "missing_quote_at"),
    ({"greeks_at": "2026-09-23T19:00:00Z"}, "future_or_stale_greeks_at"),
    ({"quote_at": "2026-09-23T17:00:00Z"}, "future_or_stale_quote_at"),
    ({"iv_unit": "provider_display"}, "unverified_iv_unit"),
    ({"iv": None}, "missing_or_invalid_iv"),
    ({"ask_u": 1}, "missing_or_invalid_bid_ask"),
])
def test_missing_units_times_or_invalid_prices_are_reported_not_imputed(db, changes, reason):
    capture(db, 23, changes=changes)
    result = report(db)
    assert reason in result["reference_point"]["blockers"]
    assert result["status"] == "insufficient_comparable_iv"


def test_target_choice_precedes_iv_validation_and_tolerance_is_bounded(db):
    # The exact target has unavailable IV; the less-comparable valid quote cannot replace it.
    alt = dict(contract=dict(symbol="QQQ", option_type="CALL", expiry_date="2026-10-16",
                             strike_u=800000000, multiplier=100),
               snapshot=dict(captured_at="2026-09-23T18:00:00Z",
                             quote_at="2026-09-23T18:00:00Z", greeks_at="2026-09-23T18:00:00Z",
                             iv=.3, iv_unit="fraction", delta=.08, bid_u=1000000, ask_u=1100000))
    capture(db, 23, changes={"iv": None}, extra=[alt])
    result = report(db)
    assert result["reference_point"]["delta"] == .075
    assert result["metrics"]["iv_percentile_pct"] is None
    assert report(db, dte_tolerance=0)["reference_point"] is None


def test_knowledge_cutoff_and_later_supersession_do_not_leak(db):
    original = capture(db, 21, .2)
    capture(db, 22, .3, recorded_at="2026-09-24T18:00:00Z")
    capture(db, 21, .8, recorded_at="2026-09-24T18:00:00Z", supersedes=original, key="revision")
    result = iv_history(db, symbol="QQQ", source="browser", as_of="2026-09-23T18:00:00Z",
                        min_samples=2)
    assert len(result["daily_points"]) == 1
    assert result["reference_point"]["record_id"] == original
    later = report(db)
    assert later["daily_points"][0]["snapshot"]["iv"] == .8


def test_calendar_gaps_weekends_and_observation_limit(db):
    capture(db, 20, .9)  # Sunday
    capture(db, 21, .2)
    capture(db, 23, .3)
    result = report(db, expected_dates=["2026-09-21", "2026-09-22", "2026-09-23"])
    assert result["coverage"]["missing_or_unusable_expected_dates"] == ["2026-09-22"]
    assert result["metrics"]["historical_samples"] == 1
    assert result["coverage"]["row_exclusion_reasons"]["closed_session"] == 1
    with pytest.raises(ValueError, match="limit"):
        report(db, limit=1)


def test_malformed_quotes_and_closed_session_flag(db):
    record = capture(db, 23, extra=[None, {"contract": None, "snapshot": None}])
    payload = copy.deepcopy(store.get_observation(db, record)["payload"])
    payload.update(idempotency_key="closed", supersedes_id=record)
    payload["inputs"]["closed_session"] = "2026-09-22"
    with patch.object(store, "utc_now", return_value="2026-09-23T18:01:00Z"):
        store.save_observation(db, payload)
    result = report(db)
    assert result["reference_point"] is None
    assert result["coverage"]["row_exclusion_reasons"]["malformed_quote"] == 2
    assert result["coverage"]["row_exclusion_reasons"]["closed_session"] == 1


def test_cli_exports_insufficient_data_and_protects_inputs(db, tmp_path, capsys):
    capture(db, 23, 20, changes={"iv_unit": "provider_display"})
    output, markdown = tmp_path / "iv.json", tmp_path / "iv.md"
    before = db.read_bytes()
    args = ["options", "iv-history", "--db", str(db), "--symbol", "QQQ", "--source", "browser",
            "--as-of", "2026-10-01T12:00:00Z"]
    assert main([*args, "--json-output", str(output), "--markdown-output", str(markdown)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "insufficient_comparable_iv"
    assert json.loads(output.read_text())["coverage"]["iv_units"] == {"provider_display": 1}
    assert "unverified_iv_unit" in markdown.read_text()
    assert db.read_bytes() == before
    assert main([*args, "--json-output", str(db)]) == 1
    assert "cannot overwrite" in capsys.readouterr().out
    assert main([*args, "--json-output", str(output), "--markdown-output", str(output)]) == 1
    assert "distinct" in capsys.readouterr().out
