import copy

import pytest

from longarc.analytics.audit import data_audit
from longarc.storage import store


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "audit.sqlite3"
    store.initialize(path)
    return path


def capture(db, key, *, symbol="QQQ", mode="observe", source="browser", day=21,
            stamped=False, supersedes=None, change=None):
    stamp = f"2026-09-{day:02d}T18:00:00Z"
    snap = dict(captured_at=stamp, bid_u=1000000, ask_u=1200000, delta=0.08,
                theta=-0.1, underlying_price_u=700000000,
                quote_at=stamp if stamped else None, greeks_at=stamp if stamped else None,
                underlying_at=stamp if stamped else None)
    if change:
        snap.update(change)
    quote = dict(contract=dict(symbol=symbol, option_type="CALL", expiry_date="2026-10-16",
                               strike_u=790000000, multiplier=100), snapshot=snap)
    p = dict(idempotency_key=key, scope=f"options:{symbol}", mode=mode, kind="observation",
             observed_at=stamp, source=source, quality="synthetic" if mode == "shadow"
             else "unverified", code_version="test", inputs={"format": "option-chain-v1"},
             results={"quotes": [quote], "complete": False}, evidence_ids=[],
             supersedes_id=supersedes)
    return store.save_observation(db, p)["record_id"]


def test_inventory_separates_revisions_modes_sources_and_no_writes(db):
    old = capture(db, "old")
    capture(db, "new", supersedes=old, stamped=True)
    capture(db, "iau", symbol="IAU")
    capture(db, "shadow", mode="shadow")
    capture(db, "other", source="other")
    before = db.read_bytes()
    report = data_audit(db, symbol="QQQ")
    assert db.read_bytes() == before
    assert report["inventory"]["table_rows"]["observations"] == 5
    assert report["inventory"]["current_observations"] == 4
    assert report["inventory"]["superseded_observations"] == 1
    assert len(report["quote_groups"]) == 3
    live = next(g for g in report["quote_groups"] if g["mode"] == "observe"
                and g["source"] == "browser")
    assert live["replay_quote_prerequisites_met_rows"] == 1
    assert live["batch_evidence"][0]["record_id"] != old
    shadow = next(g for g in report["quote_groups"] if g["mode"] == "shadow")
    assert shadow["replay_quote_prerequisites_met_rows"] == 0


def test_missing_times_are_not_deduplicated_or_replay_eligible(db):
    capture(db, "first")
    capture(db, "repeat", day=22)
    g = data_audit(db)["quote_groups"][0]
    assert g["quote_rows"] == 2 and g["contract_identities"] == 1
    assert g["duplicate_source_snapshots"] == 0
    assert g["potential_repeats_with_unusable_source_times"] == 1
    assert g["field_missing"]["quote_at"] == dict(count=2, denominator=2, fraction=1)
    assert g["descriptive_price_rows"] == 2
    assert g["replay_quote_prerequisites_met_rows"] == 0
    assert g["missing_capture_dates"] is None
    assert g["source_quote_dates"] == []


def test_explicit_session_coverage_and_capture_date_filter(db):
    capture(db, "a", day=20)
    capture(db, "b", day=21)
    g = data_audit(db, start="2026-09-21", end="2026-09-23",
                   expected_dates=["2026-09-21", "2026-09-22", "2026-09-23"])["quote_groups"][0]
    assert g["quote_rows"] == 1
    assert g["missing_capture_dates"] == ["2026-09-22", "2026-09-23"]


def test_bad_quotes_do_not_inflate_valid_rows_or_hide_malformed_items(db):
    capture(db, "crossed", change={"bid_u": 2000000})
    capture(db, "malformed", change={"delta": 5})
    capture(db, "stale", stamped=True, change={"quote_at": "2026-09-21T17:00:00Z"})
    capture(db, "future", stamped=True, change={"greeks_at": "2026-09-21T19:00:00Z"})
    g = data_audit(db)["quote_groups"][0]
    assert g["quote_rows"] == 4
    assert len(g["malformed_quotes"]) == 1
    assert g["field_missing"]["quote_at"]["denominator"] == 3
    assert g["replay_quote_blockers"]["invalid_or_missing_bid_ask"] == 1
    assert g["replay_quote_blockers"]["future_or_stale_quote_at"] == 1
    assert g["replay_quote_blockers"]["future_or_stale_greeks_at"] == 1
    assert g["replay_quote_prerequisites_met_rows"] == 0


def test_exact_source_duplicate_and_closed_session(db):
    original = capture(db, "a", stamped=True)
    p = copy.deepcopy(store.get_observation(db, original)["payload"])
    p["idempotency_key"] = "b"
    p["inputs"]["closed_session"] = "2026-09-18"
    store.save_observation(db, p)
    g = data_audit(db)["quote_groups"][0]
    assert g["duplicate_source_snapshots"] == 1
    assert g["closed_session_batches"] == 1
    assert g["replay_quote_prerequisites_met_rows"] == 1


def test_repeated_stale_source_stamps_still_identify_same_snapshot(db):
    capture(db, "a", stamped=True)
    old_stamp = "2026-09-21T18:00:00Z"
    capture(db, "b", day=22, change={"quote_at": old_stamp, "greeks_at": old_stamp,
                                    "underlying_at": old_stamp})
    g = data_audit(db)["quote_groups"][0]
    assert g["duplicate_source_snapshots"] == 1
    assert g["replay_quote_prerequisites_met_rows"] == 1
    assert g["replay_quote_blockers"]["future_or_stale_quote_at"] == 1


def test_empty_limit_and_invalid_filters(db):
    report = data_audit(db)
    assert report["inventory"]["first_observed_at"] is None
    assert report["quote_groups"] == []
    empty = data_audit(db, symbol="QQQ", expected_dates=["2026-09-21"])
    assert empty["observe_capture_coverage"][0]["missing_expected_dates"] == ["2026-09-21"]
    capture(db, "a")
    capture(db, "b")
    with pytest.raises(ValueError, match="limit"):
        data_audit(db, limit=1)
    with pytest.raises(ValueError):
        data_audit(db, start="2026-09-22", end="2026-09-21")
    with pytest.raises(ValueError):
        data_audit(db, expected_dates="2026-09-21")


def test_execution_counts_are_separate_from_holding_table(db):
    p = dict(idempotency_key="fill", scope="executions:example", mode="manual",
             kind="observation", observed_at="2026-09-21T18:00:00Z", source="broker",
             quality="unverified", code_version="test", evidence_ids=[],
             inputs={"format": "short-call-execution-v1", "execution": {
                 "action": "STO", "contract": {"symbol": "QQQ"}}}, results={})
    store.save_observation(db, p)
    report = data_audit(db)
    assert report["inventory"]["table_rows"]["holding_lots"] == 0
    assert report["execution_events"] == [dict(symbol="QQQ", mode="manual", action="STO", count=1)]


@pytest.mark.parametrize("event", [None, [], {"contract": None},
                                  {"contract": {"symbol": "QQQ"}, "action": "invalid"}])
def test_malformed_execution_is_reported_without_aborting_audit(db, event):
    store.save_observation(db, dict(idempotency_key="malformed", scope="executions:example",
        mode="manual", kind="observation", observed_at="2026-09-21T18:00:00Z",
        source="fixture", quality="unverified", code_version="fixture", evidence_ids=[],
        inputs={"format": "short-call-execution-v1", "execution": event}, results={}))
    report = data_audit(db)
    assert not report["execution_events"]
    assert len(report["malformed_execution_records"]) == 1
