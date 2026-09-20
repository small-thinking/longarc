from __future__ import annotations

import json
from pathlib import Path

import pytest

from longarc.cli import main
from longarc.storage import holdings, store


def lot(**changes):  # type: ignore[no-untyped-def]
    return dict(
        idempotency_key="lot-1",
        account_alias="fixture",
        mode="shadow",
        symbol="QQQ",
        option_type="CALL",
        expiry_date="2026-10-16",
        strike_u=740000000,
        multiplier=100,
        opening_contracts=1,
        opening_premium_u=6000000,
        opening_fees_u=None,
        opened_at="2026-09-20T15:00:00Z",
        source="synthetic",
        **changes,
    )


def quote(holding_id, **changes):  # type: ignore[no-untyped-def]
    base = dict(
        idempotency_key="quote-1",
        holding_id=holding_id,
        contract={k: lot()[k] for k in holdings.CONTRACT},
        captured_at="2026-09-20T16:00:00Z",
        source="synthetic",
        quality="synthetic",
        bid_u=5670000,
        ask_u=5750000,
        delta=0.29,
        theta=-0.199,
    )
    return {**base, **changes}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "test.sqlite3"
    store.initialize(path)
    return path


def test_multiple_lots_and_repeated_checks_without_actions(db: Path) -> None:
    a = holdings.add_holding(db, lot())["holding_id"]
    other = lot()
    other.update(idempotency_key="lot-2", opening_premium_u=5500000)
    b = holdings.add_holding(db, other)["holding_id"]
    assert a != b
    rows = holdings.list_holdings(db, "fixture", "shadow")
    assert len(rows) == 2
    assert all(r["position_status"] == "not_reconciled" for r in rows)
    first = holdings.add_snapshot(db, quote(a))
    assert holdings.add_snapshot(db, quote(a))["status"] == "existing"
    holdings.add_snapshot(
        db, quote(a, idempotency_key="quote-2", delta=0.3, captured_at="2026-09-20T17:00:00Z")
    )
    history = holdings.history(db, a)
    assert [q["delta"] for q in history["snapshots"]] == [0.3, 0.29]
    assert history["snapshots"][1]["snapshot_id"] == first["snapshot_id"]
    assert history["snapshots"][0]["quote_at"] is None
    assert history["snapshots"][0]["underlying_price_u"] is None
    assert history["position_status"] == "not_reconciled"
    limited = holdings.history(db, a, limit=1)
    assert limited["total_snapshots"] == 2 and limited["truncated"] is True
    assert holdings.history(db, b)["snapshots"] == []


def test_wrong_contract_unknown_holding_and_conflict_rejected(db: Path) -> None:
    h = holdings.add_holding(db, lot())["holding_id"]
    holdings.add_snapshot(db, quote(h))
    with pytest.raises(ValueError, match="different content"):
        holdings.add_snapshot(db, quote(h, delta=0.1))
    with pytest.raises(ValueError, match="contract"):
        holdings.add_snapshot(db, quote(h, contract={**quote(h)["contract"], "symbol": "SPY"}))
    with pytest.raises(ValueError, match="not found"):
        holdings.add_snapshot(db, quote("unknown"))
    with pytest.raises(ValueError, match="mode"):
        holdings.add_snapshot(db, quote(h, quality="unverified"))
    assert len(holdings.history(db, h)["snapshots"]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"opening_contracts": True},
        {"opening_premium_u": 1.1},
        {"opened_at": "2026-09-20T12:00:00"},
        {"multiplier": None},
        {"strike_u": -1},
    ],
)
def test_opening_input_validation(db: Path, changes) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        holdings.add_holding(db, {**lot(), **changes})
    assert holdings.list_holdings(db, "fixture", "shadow") == []


@pytest.mark.parametrize(
    "changes",
    [
        {"delta": float("nan")},
        {"prob_otm": 90.42},
        {"bid_u": 5.67},
        {"quality": "verified"},
        {"quote_at": "2026-09-20T12:00:00"},
    ],
)
def test_quote_validation(db: Path, changes) -> None:  # type: ignore[no-untyped-def]
    h = holdings.add_holding(db, lot())["holding_id"]
    with pytest.raises(ValueError):
        holdings.add_snapshot(db, quote(h, **changes))
    assert holdings.history(db, h)["snapshots"] == []


def test_v1_migration_preserves_original_data(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with store.connect(path, create=True) as db:
        db.executescript(store.SCHEMA)
        db.execute(
            "INSERT INTO schema_migrations VALUES (1, ?, ?)", (store.CHECKSUM, store.utc_now())
        )
        db.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "original",
                "key",
                "test",
                "shadow",
                "no_action",
                "old",
                "old",
                "fixture",
                "synthetic",
                "test",
                None,
                "input",
                "content",
                None,
                "{}",
            ),
        )
        db.commit()
    assert store.health(path)["schema_version"] == 1
    backup = tmp_path / "v1-backup.sqlite3"
    store.copy_database(path, backup)
    assert store.initialize(path)["schema_version"] == 2
    assert store.initialize(path)["record_count"] == 1
    assert store.get_observation(path, "original")["content_hash"] == "content"
    assert store.health(backup)["schema_version"] == 1


def test_backup_restores_lots_and_quotes(db: Path, tmp_path: Path) -> None:
    h = holdings.add_holding(db, lot())["holding_id"]
    holdings.add_snapshot(db, quote(h))
    restored = tmp_path / "restore.sqlite3"
    store.copy_database(db, restored)
    assert holdings.history(db, h) == holdings.history(restored, h)


def test_cli_holding_and_history(db: Path, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    payload = tmp_path / "lot.json"
    payload.write_text(json.dumps(lot()))
    assert main(["db", "holding-add", "--db", str(db), "--file", str(payload)]) == 0
    h = json.loads(capsys.readouterr().out)["result"]["holding_id"]
    assert main(["db", "history", "--db", str(db), "--id", h]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["position_status"] == "not_reconciled"


def test_optional_underlying_time_preserves_legacy_payload_hash(db: Path) -> None:
    h = holdings.add_holding(db, lot())["holding_id"]
    first = holdings.add_snapshot(db, quote(h))
    stored = holdings.history(db, h)["snapshots"][0]
    assert "underlying_at" not in stored
    assert holdings.add_snapshot(db, quote(h))["snapshot_id"] == first["snapshot_id"]
    with pytest.raises(ValueError, match="timezone"):
        holdings.add_snapshot(db, quote(h, idempotency_key="new", underlying_at="2026-09-20"))
