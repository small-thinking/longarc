"""Synthetic evidence for legacy-to-canonical ingestion and sparse-history safety."""
from pathlib import Path

import pytest

from longarc.analytics.history import build_report
from longarc.cli import main
from longarc.data.option_capture import normalize_capture
from longarc.data.option_import import HEADERS, ingest_observation, selected_capture
from longarc.storage import store


def legacy():
    return {"format": "manual-schwab-selected-rows-v1",
            "captured_at": "2026-09-20T20:00:00Z", "quote_at": None, "greeks_at": None,
            "tables": [{"label": "Oct.  16, 2026 (26 days)", "headers": HEADERS,
                        "rows": [["105", "1", "1.1", "1", "0", "10%", "95%", ".08",
                                  "-.02", "5", "200", "20%", ".01", ".1", "1", "1", ""]]}]}


def save(db, raw, *, tenor=False, mode="observe"):
    payload = {"idempotency_key": store.digest(raw), "scope": "research:QQQ:tenor14-vs28"
               if tenor else "account:Example:QQQ", "mode": mode, "kind": "observation",
               "observed_at": raw["captured_at"], "source": "manual_browser",
               "quality": "synthetic" if mode == "shadow" else "unverified",
               "code_version": "manual-tenor-comparison-v1" if tenor else "manual-evidence-v1",
               "inputs": {"raw" if tenor else "quotes": raw}, "results": {}, "evidence_ids": []}
    return store.save_observation(db, payload)["record_id"]


def test_import_preserves_provenance_unknowns_and_idempotence(tmp_path: Path):
    db = tmp_path / "test.sqlite3"
    store.initialize(db)
    old_id = save(db, legacy())
    before = store.get_observation(db, old_id)
    first = ingest_observation(db, old_id, closed_session="2026-09-18")
    assert ingest_observation(db, old_id, closed_session="2026-09-18")["write_status"] == "existing"
    assert store.get_observation(db, old_id) == before
    saved = store.get_observation(db, first["record_id"])["payload"]
    assert saved["evidence_ids"] == [old_id]
    assert saved["observed_at"] == before["observed_at"]
    assert saved["results"]["requested_coverage_complete"] is False
    snap = saved["results"]["quotes"][0]["snapshot"]
    assert snap["quote_at"] is None and snap["underlying_price_u"] is None
    assert snap["probability_otm"] == .95 and snap["probability_touch"] == .1
    assert main(["options", "ingest", "--db", str(db), "--observation-id", old_id,
                 "--closed-session", "2026-09-18"]) == 0


def test_tenor_and_closed_history_do_not_create_market_changes(tmp_path: Path):
    db = tmp_path / "test.sqlite3"
    store.initialize(db)
    original = legacy()
    ingest_observation(db, save(db, original), closed_session="2026-09-18")
    raw = {"captured_at": "2026-09-20T21:00:00Z", "rows": [
        {"expiry": "2026-10-16", "raw": original["tables"][0]["rows"][0]}]}
    ingest_observation(db, save(db, raw, tenor=True), closed_session="2026-09-18")
    report = build_report(db, "QQQ")
    assert len(report["series"][0]["points"]) == 2
    assert report["series"][0]["changes"] == []
    assert report["cross_contract_statistics"] == []
    raw["captured_at"] = "2026-09-20T22:00:00Z"
    ingest_observation(db, save(db, raw, tenor=True))
    assert build_report(db, "QQQ")["series"][0]["changes"] == []


def test_modes_and_unsupported_inputs_fail_closed(tmp_path: Path):
    db = tmp_path / "test.sqlite3"
    store.initialize(db)
    record_id = save(db, legacy(), mode="shadow")
    with pytest.raises(ValueError, match="preserve"):
        ingest_observation(db, record_id, mode="observe")
    saved = ingest_observation(db, record_id)
    assert store.get_observation(db, saved["record_id"])["mode"] == "shadow"
    with pytest.raises(ValueError):
        selected_capture({"format": "unknown"})
    raw = legacy()
    raw["tables"][0]["label"] = "ambiguous"
    with pytest.raises(ValueError):
        selected_capture(raw)


def test_ambiguous_probability_units_remain_unknown():
    raw = legacy()
    raw["tables"][0]["rows"][0][6] = "95"
    quote = normalize_capture(selected_capture(raw))["quotes"][0]
    assert quote["snapshot"]["probability_otm"] is None
    assert "invalid_probability_otm" in quote["warnings"]


def test_explicit_source_closed_session_is_preserved(tmp_path: Path):
    db = tmp_path / "test.sqlite3"
    store.initialize(db)
    record_id = save(db, legacy())
    payload = store.get_observation(db, record_id)["payload"]
    payload["idempotency_key"] = "with-session"
    payload["results"] = {"closed_session": "2026-09-18"}
    source_id = store.save_observation(db, payload)["record_id"]
    result = ingest_observation(db, source_id)
    assert store.get_observation(db, result["record_id"])["payload"]["inputs"][
        "closed_session"] == "2026-09-18"
    with pytest.raises(ValueError, match="conflicts"):
        ingest_observation(db, source_id, closed_session="2026-09-17")
