"""Saved API inputs stay distinct from browser captures and remain usable offline."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from longarc.cli import main
from longarc.schwab import capture
from longarc.schwab.capture import ingest_chain, normalize_api_chain
from longarc.storage import store


def examples() -> tuple[dict, dict]:
    """Invented minimal API envelopes; tests never read private snapshots."""
    def millis(value: str) -> int:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)

    chain = {
        "provider": "schwab-api", "captured_at": "2026-11-01T20:00:00Z",
        "resource": "chain", "request": {"symbol": "QQQ"},
        "data": {
            "symbol": "QQQ", "isDelayed": False, "isChainTruncated": False,
            "numberOfContracts": 1,
            "callExpDateMap": {"2026-11-12:11": {"150.0": [{
                "putCall": "CALL", "symbol": "QQQ   261112C00150000",
                "strikePrice": 150.0, "expirationDate": "2026-11-12T21:00:00Z",
                "multiplier": 100.0, "nonStandard": False, "mini": False,
                "optionDeliverablesList": None, "deliverableNote": None,
                "bid": 1.25, "ask": 1.35, "last": 1.30,
                "bidSize": 8, "askSize": 9, "totalVolume": 17, "openInterest": 300,
                "volatility": 24.0, "delta": 0.32, "theta": -0.07,
                "gamma": 0.04, "vega": 0.12,
                "quoteTimeInLong": millis("2026-11-01T19:59:58Z"),
            }]}},
        },
    }
    quotes = {
        "provider": "schwab-api", "captured_at": "2026-11-01T20:00:01Z",
        "resource": "quotes", "request": {"symbols": ["QQQ"]},
        "data": {"QQQ": {"symbol": "QQQ", "quote": {
            "lastPrice": 142.5, "quoteTime": millis("2026-11-01T19:59:59Z"),
        }}},
    }
    return chain, quotes


def write_pair(tmp_path: Path, chain: dict, quotes: dict) -> tuple[Path, Path]:
    a, b = tmp_path / "chain.json", tmp_path / "quotes.json"
    a.write_text(json.dumps(chain))
    b.write_text(json.dumps(quotes))
    return a, b


def test_synthetic_api_fixture_maps_distinct_clocks_and_unknowns() -> None:
    chain, quotes = examples()
    result = normalize_api_chain(chain, quotes)
    assert result["symbol"] == "QQQ"
    assert result["results"]["status"] == "partial"
    row = result["results"]["quotes"][0]
    contract, snap = row["contract"], row["snapshot"]
    original = next(iter(next(iter(chain["data"]["callExpDateMap"].values())).values()))[0]
    assert contract["option_symbol"] == original["symbol"]
    assert contract["multiplier"] == 100
    assert contract["non_standard"] is False
    assert snap["bid_u"] == int(original["bid"] * 1_000_000)
    assert snap["iv"] == pytest.approx(original["volatility"] / 100)
    assert snap["iv_unit"] == "fraction"
    assert snap["quote_at"] is not None and snap["quote_at"] != snap["captured_at"]
    assert snap["underlying_at"] is not None and snap["underlying_at"] != snap["captured_at"]
    assert snap["greeks_at"] is None
    assert snap["probability_touch"] is None and snap["probability_otm"] is None
    assert "missing_greeks_at" in row["warnings"]
    assert "asynchronous_capture" in result["results"]["warnings"]
    assert result["results"]["requested_coverage_complete"] is False


def test_batched_underlying_quotes_select_exact_symbol() -> None:
    chain, quotes = examples()
    quotes["request"]["symbols"] = ["QQQ", "IAU"]
    quotes["data"]["IAU"] = copy.deepcopy(quotes["data"]["QQQ"])
    quotes["data"]["IAU"]["symbol"] = "IAU"
    quotes["data"]["IAU"]["quote"]["lastPrice"] = 1
    result = normalize_api_chain(chain, quotes)
    assert result["results"]["quotes"][0]["snapshot"]["underlying_price_u"] != 1_000_000
    quotes["data"]["QQQ"]["symbol"] = "IAU"
    with pytest.raises(ValueError, match="Underlying quote symbol conflicts"):
        normalize_api_chain(chain, quotes)
    quotes["data"]["QQQ"]["symbol"] = "QQQ"
    quotes["data"]["SPY"] = {"symbol": "SPY", "quote": {}}
    with pytest.raises(ValueError, match="response symbols conflict"):
        normalize_api_chain(chain, quotes)


def test_offline_cli_idempotence_readback_and_existing_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chain, quotes = examples()
    a, b = write_pair(tmp_path, chain, quotes)
    db = tmp_path / "observations.db"
    store.initialize(db)
    args = ["schwab", "ingest-chain", "--db", str(db), "--chain-file", str(a),
            "--quotes-file", str(b)]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["write_status"] == "created" and first["quote_count"] == 1
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["write_status"] == "existing"
    assert second["record_id"] == first["record_id"]
    saved = store.get_observation(db, first["record_id"])
    payload = saved["payload"]
    assert payload["source"] == "schwab_api"
    assert payload["scope"] == "options:QQQ"
    assert payload["inputs"]["format"] == "option-chain-v1"
    assert len(payload["inputs"]["api_evidence"]) == 2
    assert all(len(e["sha256"]) == 64 and e["file"] for e in payload["inputs"]["api_evidence"])
    assert (datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
            == datetime.fromisoformat(quotes["captured_at"].replace("Z", "+00:00")))
    assert payload["results"]["quotes"][0]["metrics"]["dte_calendar_days"] is not None
    # Existing history consumer recognizes the source and canonical format.
    from longarc.analytics.history import build_report
    history = build_report(db, symbol="QQQ", source="schwab_api")
    assert history["batches"][0]["record_id"] == first["record_id"]


def test_same_evidence_retry_keeps_original_after_adapter_version_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, quotes = examples()
    a, b = write_pair(tmp_path, chain, quotes)
    db = tmp_path / "observations.db"
    store.initialize(db)
    first = ingest_chain(db, a, b)
    saved = store.get_observation(db, first["record_id"])["payload"]
    assert saved["code_version"].startswith("schwab-api-chain-v1:")
    source = tmp_path / "changed-adapter.py"
    source.write_text("new adapter semantics")
    monkeypatch.setattr(capture, "__file__", str(source))
    second = ingest_chain(db, a, b)
    assert second["write_status"] == "existing"
    assert second["record_id"] == first["record_id"]
    retried = store.get_observation(db, second["record_id"])["payload"]
    assert retried["code_version"] == saved["code_version"]


def test_identity_conflicts_prevent_write(tmp_path: Path) -> None:
    db = tmp_path / "observations.db"
    store.initialize(db)
    for change in ("symbol", "strike", "expiry"):
        chain, quotes = examples()
        row = next(iter(next(iter(chain["data"]["callExpDateMap"].values())).values()))[0]
        if change == "symbol":
            row["symbol"] = row["symbol"].replace("QQQ", "SPY", 1)
        elif change == "strike":
            row["strikePrice"] += 1
        else:
            row["expirationDate"] = "2026-11-13T21:00:00+00:00"
        a, b = write_pair(tmp_path, chain, quotes)
        with pytest.raises(ValueError, match="identity conflicts"):
            ingest_chain(db, a, b)
    assert store.health(db)["record_count"] == 0


def test_partial_rows_sentinels_and_duplicate_identity() -> None:
    chain, quotes = examples()
    strikes = next(iter(chain["data"]["callExpDateMap"].values()))
    rows = next(iter(strikes.values()))
    rows[0]["delta"] = -999
    rows[0]["gamma"] = -999
    rows[0]["volatility"] = -999
    rows[0]["quoteTimeInLong"] = None
    rows.append(copy.deepcopy(rows[0]))
    rows.append({"putCall": "CALL"})
    chain["data"]["isDelayed"] = True
    chain["data"]["isChainTruncated"] = True
    result = normalize_api_chain(chain, quotes)["results"]
    assert len(result["quotes"]) == 1
    assert result["rejected"][0]["reason"] == "missing_or_invalid_option_symbol"
    snap = result["quotes"][0]["snapshot"]
    assert snap["delta"] is snap["gamma"] is snap["iv"] is snap["quote_at"] is None
    assert {"delayed_chain", "truncated_chain", "partial_collection"} <= set(result["warnings"])


def test_nonfinite_float_conversion_is_missing() -> None:
    assert capture._decimal("1e10000") is None


def test_ingest_rejects_overlapping_database_and_input_paths(tmp_path: Path) -> None:
    chain, quotes = examples()
    a, b = write_pair(tmp_path, chain, quotes)
    original_a, original_b = a.read_bytes(), b.read_bytes()
    with pytest.raises(ValueError, match="must be distinct"):
        ingest_chain(a, a, b)
    with pytest.raises(ValueError, match="must be distinct"):
        ingest_chain(b, a, b)
    with pytest.raises(ValueError, match="must be distinct"):
        ingest_chain(tmp_path / "observations.db", a, a)
    assert a.read_bytes() == original_a and b.read_bytes() == original_b
    assert not (tmp_path / "observations.db").exists()


def test_offline_cli_failure_paths_return_nonzero_without_tracebacks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chain, quotes = examples()
    a, b = write_pair(tmp_path, chain, quotes)
    db = tmp_path / "observations.db"
    args = ["schwab", "ingest-chain", "--db", str(db), "--chain-file", str(a),
            "--quotes-file", str(b)]
    assert main(args) == 1  # Missing database.
    assert "Traceback" not in capsys.readouterr().out
    db.write_bytes(b"")
    assert main(args) == 1  # Uninitialized database.
    assert "Traceback" not in capsys.readouterr().out
    db.unlink()
    store.initialize(db)
    a.write_text(json.dumps({"provider": "schwab-api", "resource": "chain"}))
    assert main(args) == 1  # Missing envelope fields.
    assert "Traceback" not in capsys.readouterr().out
    chain, quotes = examples()
    chain["data"]["callExpDateMap"] = {}
    write_pair(tmp_path, chain, quotes)
    assert main(args) == 1  # Stored error observation with no usable CALL rows.
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error" and result["quote_count"] == 0
    assert store.health(db)["record_count"] == 1
