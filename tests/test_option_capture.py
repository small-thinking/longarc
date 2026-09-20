from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from longarc.cli import main
from longarc.data.option_capture import ingest_capture, normalize_capture
from longarc.storage import store


def capture() -> dict:
    # Synthetic cells reproduce the observed public UI shape, not private quotes.
    return {
        "format": "schwab-browser-chain-v1", "symbol": "QQQ",
        "captured_at": "2026-09-21T19:46:00Z",
        "requested": {"expiries": ["2026-10-16", "2026-10-23"],
                      "contracts": [{"expiry_date": "2026-10-16", "strike_u": 105000000}]},
        "slices": [{"expiry_date": "2026-10-16", "captured_at": "2026-09-21T19:45:00Z",
                    "underlying_price": "100.00", "quote_at": None, "greeks_at": None,
                    "headers": ["Strike", "Bid", "Ask", "Last", "Change", "Prob.Touching",
                                "Prob.OTM", "Delta", "Theta", "Volume", "Open Interest", "Menu"],
                    "rows": [["105.00", "", "1.00\nSelect", "1.10\nSelect", "1.08", "0.01",
                              "10%", "95%", "0.08", "-0.02", "5", "200", "", ""],
                             ["106.00", "", "0", "0", "0", "0", "-", "-", "-", "-",
                              "0", "0", "", ""]]}], "errors": [],
    }


def test_capture_retains_sparse_fields_and_reports_missing_coverage() -> None:
    report = normalize_capture(capture())
    assert report["status"] == "partial" and not report["complete"]
    assert len(report["quotes"]) == 1
    snap = report["quotes"][0]["snapshot"]
    assert snap["bid_u"] == 1000000 and snap["delta"] == .08
    assert snap["quote_at"] is None and snap["greeks_at"] is None
    assert snap["iv"] is None and snap["open_interest"] == 200
    assert report["missing_expiries"] == ["2026-10-23"]
    assert report["rejected"][0]["reason"] == "empty_placeholder"


def test_malformed_slice_bad_field_and_crossed_quotes_do_not_discard_good_data() -> None:
    raw = capture()
    extra = copy.deepcopy(raw["slices"][0])
    extra["expiry_date"] = "2026-10-23"
    extra["rows"][0][2] = "2.00"
    extra["rows"][0][8] = "NaN"
    raw["slices"].extend([extra, {"expiry_date": "bad"}])
    result = normalize_capture(raw)
    assert len(result["quotes"]) == 2
    assert "crossed_quote" in result["quotes"][1]["warnings"]
    assert result["quotes"][1]["snapshot"]["delta"] is None
    assert result["quotes"][1]["metrics"]["midpoint_u"] is None


def test_shifted_table_is_not_silently_mapped_to_wrong_prices() -> None:
    raw = capture()
    raw["slices"][0]["rows"][0].insert(2, "unexpected")
    result = normalize_capture(raw)
    assert result["status"] == "error" and not result["quotes"]
    assert result["rejected"][0]["reason"] == "column_mismatch"


def test_overlapping_windows_select_latest_and_keep_raw_evidence(tmp_path: Path) -> None:
    raw = capture()
    later = copy.deepcopy(raw["slices"][0])
    later["captured_at"] = raw["captured_at"]
    later["rows"][0][2] = "1.05"
    raw["slices"].append(later)
    db = tmp_path / "db.sqlite3"
    store.initialize(db)
    first = ingest_capture(db, raw, mode="shadow")
    second = ingest_capture(db, raw, mode="shadow")
    assert first["record_id"] == second["record_id"]
    assert second["write_status"] == "existing" and second["readback_verified"]
    payload = store.get_observation(db, first["record_id"])["payload"]
    assert payload["inputs"]["raw_capture"] == raw
    assert payload["results"]["quotes"][0]["snapshot"]["bid_u"] == 1050000


def test_failure_only_capture_is_stored_and_cli_signals_error(tmp_path: Path, capsys) -> None:
    raw = capture()
    raw["slices"] = []
    raw["errors"] = [{"code": "setup_failed_check_login_and_page_structure"}]
    db = tmp_path / "db.sqlite3"
    store.initialize(db)
    f = tmp_path / "capture.json"
    f.write_text(json.dumps(raw))
    assert main(["options", "ingest", "--db", str(db), "--file", str(f),
                 "--mode", "shadow"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert store.get_observation(db, result["record_id"])["kind"] == "error"
    report = tmp_path / "report.md"
    assert main(["options", "history", "--db", str(db), "--mode", "shadow",
                 "--start", "2026-09-21", "--end", "2026-09-22",
                 "--markdown-output", str(report)]) == 0
    assert "2026-09-22" in report.read_text()


def test_naive_time_rejected_without_fabricated_capture_time() -> None:
    raw = capture()
    raw["captured_at"] = "2026-09-21T12:00:00"
    with pytest.raises(ValueError):
        normalize_capture(raw)
