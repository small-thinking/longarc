from pathlib import Path

import pytest

from longarc.analytics.history import build_report, render_markdown
from longarc.storage import store


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "history.sqlite3"
    store.initialize(path)
    return path


def add(db, key, day, *, source="feed", mode="observe", bid=1000000, ask=1200000,
        delta=0.1, stamp=None, multiplier=100, status="ok", supersedes=None,
        complete=True, missing_expiries=None, missing_contracts=None):
    captured = f"2026-09-{day:02d}T18:00:00Z"
    quote = {"contract": dict(symbol="QQQ", option_type="CALL", expiry_date="2026-10-16",
                             strike_u=765000000, multiplier=multiplier),
             "snapshot": dict(captured_at=captured, bid_u=bid, ask_u=ask, delta=delta,
                              quote_at=stamp, greeks_at=stamp)}
    event = dict(idempotency_key=key, scope="options:QQQ", mode=mode,
                 kind="error" if status == "error" else "observation", observed_at=captured,
                 source=source, quality="synthetic" if mode == "shadow" else "unverified",
                 code_version="test", inputs={"format": "option-chain-v1"},
                 results={"status": status, "quotes": [] if status == "error" else [quote],
                          "complete": complete and status == "ok",
                          "missing_expiries": missing_expiries or [],
                          "missing_contracts": missing_contracts or []},
                 evidence_ids=[])
    if supersedes:
        event["supersedes_id"] = supersedes
    return store.save_observation(db, event)["record_id"]


def test_sparse_intervals_and_session_gaps(db):
    add(db, "a", 21)
    add(db, "b", 24, bid=500000, ask=700000, delta=0.06)
    report = build_report(db, "QQQ", expected_dates=["2026-09-21", "2026-09-23", "2026-09-24"])
    change = report["series"][0]["changes"][0]
    assert change["elapsed_hours"] == 72
    assert change["mid_change_u"] == -500000
    assert change["delta_change"] == pytest.approx(-0.04)
    assert report["coverage"][0]["missing_batch_dates"] == ["2026-09-23"]
    assert "No interpolation" in render_markdown(report)
    stats = report["cross_contract_statistics"][0]
    assert stats["distinct_contracts"] == 1 and stats["delta_change"]["count"] == 1


def test_bad_prices_missing_delta_and_zero_denominator(db):
    add(db, "zero", 21, bid=0, ask=0)
    add(db, "crossed", 22, bid=2000000, ask=1000000, delta=None)
    add(db, "missing", 23, bid=None, ask=1000000)
    add(db, "bad", 24, bid=True, ask=1000000, delta=2)
    report = build_report(db, "QQQ")
    points = report["series"][0]["points"]
    assert points[0]["mid_u"] == 0
    assert all(p["mid_u"] is None for p in points[1:])
    assert points[-1]["delta"] is None
    assert all(c["mid_change_fraction"] is None for c in report["series"][0]["changes"])


def test_source_mode_multiplier_and_date_isolation(db):
    add(db, "a", 21)
    add(db, "b", 22, source="other")
    add(db, "c", 23, mode="shadow")
    add(db, "d", 24, multiplier=None)
    report = build_report(db, "QQQ")
    assert len(report["series"]) == 3
    assert not report["cross_contract_statistics"]
    report = build_report(db, "QQQ", start="2026-09-22", end="2026-09-24", source="feed")
    assert len(report["series"]) == 1
    assert report["series"][0]["points"][0]["date"] == "2026-09-24"
    assert len(build_report(db, "QQQ", mode="shadow")["series"]) == 1


def test_repeat_unknown_is_retained_known_is_deduplicated(db):
    add(db, "a", 21)
    add(db, "b", 22)
    report = build_report(db, "QQQ")
    assert len(report["series"][0]["points"]) == 2
    assert report["series"][0]["points"][1]["repeated_values"]
    stamp = "2026-09-23T17:59:00Z"
    add(db, "c", 23, stamp=stamp)
    add(db, "d", 24, stamp=stamp)
    report = build_report(db, "QQQ")
    assert report["duplicate_source_snapshots"] == 1
    assert len(report["series"][0]["points"]) == 3


def test_supersession_errors_and_visible_limit(db):
    old = add(db, "a", 21)
    add(db, "b", 21, bid=300000, supersedes=old)
    add(db, "error", 22, status="error")
    add(db, "c", 23)
    report = build_report(db, "QQQ")
    assert len(report["series"][0]["points"]) == 2
    assert report["error_batches"] == 1
    assert report["coverage"][0]["dates_without_valid_quotes"] == ["2026-09-22"]
    report = build_report(db, "QQQ", limit=1)
    assert report["truncated"] and report["rows_read"] == 1
    assert any("incomplete" in w for w in report["warnings"])
    with pytest.raises(ValueError):
        build_report(db, "QQQ", start="2026-09-25", end="2026-09-20")


def test_zero_start_price_and_empty_requested_range(db):
    add(db, "zero", 21, bid=0, ask=0)
    add(db, "positive", 22)
    change = build_report(db, "QQQ")["series"][0]["changes"][0]
    assert change["mid_change_u"] == 1100000
    assert change["mid_change_fraction"] is None
    report = build_report(db, "QQQ", source="absent", start="2026-09-21", end="2026-09-22")
    assert not report["series"]
    assert report["coverage"][0]["missing_batch_dates"] == ["2026-09-21", "2026-09-22"]


def test_future_and_invalid_source_times_exclude_metrics(db):
    add(db, "valid", 21)
    add(db, "future", 22, stamp="2026-09-24T18:00:00Z")
    add(db, "malformed", 23, stamp="not-a-time")
    report = build_report(db, "QQQ")
    points = report["series"][0]["points"]
    assert points[0]["mid_u"] is not None and points[0]["delta"] is not None
    assert all(p["mid_u"] is None and p["delta"] is None for p in points[1:])
    assert all(not p["source_time_missing"] for p in points[1:])
    assert all(s["delta_change"]["count"] == 0 for s in report["cross_contract_statistics"])
    assert report == build_report(db, "QQQ")


def test_contract_gaps_empty_sessions_and_partial_batches(db):
    add(db, "first", 21)
    add(db, "other-contract", 22, multiplier=10)
    add(db, "partial", 24, complete=False, status="partial", bid=500000,
        missing_expiries=["2026-10-23"], missing_contracts=["QQQ 775 CALL"])
    report = build_report(db, "QQQ")
    series = next(s for s in report["series"] if s["contract"]["multiplier"] == 100)
    assert series["missing_dates"] == ["2026-09-22", "2026-09-23"]
    assert report["incomplete_batches"] == 1
    assert report["batches"][-1]["missing_expiries"] == ["2026-10-23"]
    assert report["coverage"][0]["dates_without_complete_batches"] == [
        "2026-09-23", "2026-09-24"]
    rendered = render_markdown(report)
    assert "$765.0000" in rendered and "$-0.2500" in rendered
    assert "microdollars" not in rendered
    assert "1 dates" in rendered and "QQQ 775 CALL" in rendered
    report = build_report(db, "QQQ", expected_dates=[])
    assert all(not s["missing_dates"] for s in report["series"])
    assert not report["coverage"][0]["missing_batch_dates"]


def test_empty_database_bounds(db):
    report = build_report(db, "QQQ", source="feed", start="2026-09-21", end="2026-09-22")
    assert report["coverage"][0]["missing_batch_dates"] == ["2026-09-21", "2026-09-22"]
    assert build_report(db, "QQQ")["series"] == []
    assert build_report(db, "QQQ", source="feed", start="2026-09-21")["series"] == []
