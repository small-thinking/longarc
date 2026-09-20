from __future__ import annotations

import json
from pathlib import Path

import pytest

from longarc.analytics.journal import calculate_and_log
from longarc.cli import main
from longarc.storage import holdings, store

CONTRACT = dict(symbol="QQQ", option_type="CALL", expiry_date="2026-10-16",
                strike_u=740000000, multiplier=100)
SNAPSHOT = dict(captured_at="2026-09-20T16:00:00Z", quote_at="2026-09-20T15:59:00Z",
                greeks_at="2026-09-20T15:59:00Z", underlying_at="2026-09-20T15:59:00Z",
                bid_u=5000000, ask_u=5200000, underlying_price_u=720000000,
                delta=0.3, theta=-0.2)


def request():  # type: ignore[no-untyped-def]
    return dict(idempotency_key="calc-1", scope="fixture", mode="shadow", source="fixture",
                quality="synthetic", as_of="2026-09-20T16:00:00Z", evidence_ids=[],
                inputs=dict(contract=CONTRACT, snapshot=SNAPSHOT))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "test.sqlite3"
    store.initialize(path)
    return path


def test_calculate_log_retry_readback(db: Path) -> None:
    a = calculate_and_log(db, request())
    b = calculate_and_log(db, request())
    assert a["status"] == "ok"
    assert b["record_id"] == a["record_id"] and b["write_status"] == "existing"
    saved = store.get_observation(db, a["record_id"])
    assert saved["input_hash"] == store.digest(saved["payload"]["inputs"])
    assert saved["payload"]["results"]["metrics"] == a["metrics"]
    assert saved["code_version"].startswith("0.1.0/calc-")
    changed = request()
    changed["as_of"] = "2026-09-20T17:00:00Z"
    with pytest.raises(ValueError, match="different content"):
        calculate_and_log(db, changed)
    assert store.health(db)["record_count"] == 1


def test_no_action_checks_are_logged_and_invalid_data_preserved(db: Path) -> None:
    p = request()
    p["inputs"]["snapshot"] = {**SNAPSHOT, "bid_u": 6000000}
    result = calculate_and_log(db, p)
    assert result["status"] == "invalid"
    assert store.get_observation(db, result["record_id"])["kind"] == "calculation"
    p["idempotency_key"] = "bad-type"
    p["inputs"]["snapshot"] = {**SNAPSHOT, "bid_u": True}
    result = calculate_and_log(db, p)
    assert result["status"] == "error"
    assert store.get_observation(db, result["record_id"])["kind"] == "error"


def test_stored_snapshot_selection_and_lot_isolation(db: Path) -> None:
    def add_lot(key: str) -> str:
        return str(holdings.add_holding(db, dict(
            **CONTRACT, idempotency_key=key, account_alias="fixture", mode="shadow",
            opening_contracts=3, opening_premium_u=6000000,
            opened_at="2026-09-20T15:00:00Z", source="fixture",
        ))["holding_id"])

    def add_quote(key: str, lot: str, bid: int = 5000000) -> str:
        return str(holdings.add_snapshot(db, dict(
            **{**SNAPSHOT, "bid_u": bid}, idempotency_key=key, holding_id=lot, contract=CONTRACT,
            source="fixture", quality="synthetic",
        ))["snapshot_id"])

    a, b = add_lot("a"), add_lot("b")
    qa, qb = add_quote("qa", a), add_quote("qb", b)
    p = request()
    p.update(scope="holding:" + a, inputs={"snapshot_id": qa})
    result = calculate_and_log(db, p)
    assert result["status"] == "ok"
    saved = store.get_observation(db, result["record_id"])["payload"]["inputs"]
    assert saved["snapshot"]["holding_id"] == a
    assert "scenario" not in saved  # Opening quantity is never used implicitly.
    assert saved["snapshot"]["underlying_at"] == "2026-09-20T15:59:00.000000Z"
    p.update(idempotency_key="wrong-lot", inputs={"snapshot_id": qa, "previous_snapshot_id": qb})
    assert calculate_and_log(db, p)["status"] == "error"
    p.update(idempotency_key="inline-bypass", inputs={"snapshot_id": qa, "previous": {
        "contract": CONTRACT, "snapshot": {**SNAPSHOT, "holding_id": b}}})
    assert calculate_and_log(db, p)["status"] == "error"
    p.update(idempotency_key="trend", inputs={"snapshot_id": add_quote("qa2", a, 5100000),
                                              "previous_snapshot_id": qa})
    assert calculate_and_log(db, p)["status"] == "ok"


def test_previous_contract_and_chronology_checked(db: Path) -> None:
    p = request()
    p["inputs"]["previous"] = {"contract": {**CONTRACT, "symbol": "SPY"}, "snapshot": SNAPSHOT}
    assert calculate_and_log(db, p)["status"] == "error"
    p["idempotency_key"] = "future-previous"
    p["inputs"]["previous"] = {"contract": CONTRACT, "snapshot": {
        **SNAPSHOT, "captured_at": "2026-09-21T16:00:00Z"}}
    assert calculate_and_log(db, p)["status"] == "error"


def test_cli_error_and_audited_invalid_exit(db: Path, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "request.json"
    p = request()
    p["inputs"]["snapshot"] = {**SNAPSHOT, "ask_u": 1}
    f.write_text(json.dumps(p))
    assert main(["calc", "--db", str(db), "--file", str(f)]) == 1
    assert json.loads(capsys.readouterr().out)["record_id"]
    f.write_text('[]')
    assert main(["calc", "--db", str(db), "--file", str(f)]) == 1
    assert json.loads(capsys.readouterr().out)["record_id"] is None


def test_inline_identity_mismatch_is_logged_as_error(db: Path) -> None:
    for field in ("snapshot", "previous", "replacement"):
        p = request()
        p["idempotency_key"] = field
        mismatched = {**SNAPSHOT, "contract": {**CONTRACT, "strike_u": 750000000}}
        if field == "snapshot":
            p["inputs"][field] = mismatched
        else:
            p["inputs"][field] = {"contract": CONTRACT, "snapshot": mismatched}
        assert calculate_and_log(db, p)["status"] == "error"
