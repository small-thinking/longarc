from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from longarc.cli import main
from longarc.storage.store import (
    connect,
    copy_database,
    get_observation,
    health,
    initialize,
    list_observations,
    save_observation,
)


def observation(**changes):  # type: ignore[no-untyped-def]
    return {
        "idempotency_key": "fixture-1",
        "scope": "test",
        "mode": "shadow",
        "kind": "no_action",
        "observed_at": "2026-09-20T08:00:00Z",
        "source": "synthetic_fixture",
        "quality": "synthetic",
        "code_version": "test",
        "inputs": {"bid_u": 125000, "ask_u": None},
        "results": {"reason": "data_missing"},
        "evidence_ids": [],
        **changes,
    }


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "db.sqlite3"
    initialize(path)
    return path


def test_reinitialize_preserves_records_and_nulls(db: Path) -> None:
    saved = save_observation(db, observation())
    assert initialize(db)["record_count"] == 1
    record = get_observation(db, saved["record_id"])
    assert record["payload"]["inputs"] == {"bid_u": 125000, "ask_u": None}
    assert record["observed_at"] != record["recorded_at"]
    assert health(db)["journal_mode"] == "wal"


def test_idempotency_retries_and_conflicts(db: Path) -> None:
    first = save_observation(db, observation())
    again = save_observation(db, observation())
    assert again["record_id"] == first["record_id"]
    assert again["status"] == "existing"
    with pytest.raises(ValueError, match="different content"):
        save_observation(db, observation(results={"changed": True}))
    assert health(db)["record_count"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_at": "2026-09-20T08:00:00"},
        {"inputs": {"bad": float("nan")}},
        {"mode": "observe"},
        {"scope": ""},
        {"quality": "verified"},
        {"policy_hash": "draft"},
        {"kind": "fill"},
        {"results": []},
    ],
)
def test_invalid_observations_never_write(db: Path, changes) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        save_observation(db, observation(**changes))
    assert health(db)["record_count"] == 0


def test_corrections_append_and_isolate_scope(db: Path) -> None:
    first = save_observation(db, observation())["record_id"]
    with pytest.raises(ValueError, match="same scope"):
        save_observation(
            db, observation(idempotency_key="cross", supersedes_id=first, scope="other")
        )
    fixed = save_observation(
        db, observation(idempotency_key="fix", supersedes_id=first, results={"corrected": True})
    )["record_id"]
    assert get_observation(db, fixed)["supersedes_id"] == first
    assert get_observation(db, first)["payload"]["results"] == {"reason": "data_missing"}
    assert len(list_observations(db, "test")) == 2
    with connect(db) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM observations")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE observations SET scope='changed'")


def test_migration_mismatch_fails_closed(db: Path) -> None:
    with connect(db) as connection, connection:
        connection.execute("UPDATE schema_migrations SET checksum='wrong'")
    with pytest.raises(ValueError, match="modified schema"):
        initialize(db)
    with pytest.raises(ValueError, match="modified schema"):
        save_observation(db, observation())


def test_backup_includes_wal_and_restores_identical_records(db: Path, tmp_path: Path) -> None:
    with connect(db) as keeper:
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        rid = save_observation(db, observation())["record_id"]
        backup = tmp_path / "backup.sqlite3"
        restore = tmp_path / "restored.sqlite3"
        copy_database(db, backup)
        copy_database(backup, restore)
        assert get_observation(restore, rid) == get_observation(db, rid)
        with pytest.raises(FileExistsError):
            copy_database(backup, db)
        assert health(db)["record_count"] == 1


def test_missing_database_not_created_by_reads_or_save(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        health(path)
    with pytest.raises(sqlite3.OperationalError):
        save_observation(path, observation())
    assert not path.exists()


def test_cli_round_trip_and_errors(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "cli.sqlite3")
    fixture = tmp_path / "input.json"
    fixture.write_text(json.dumps(observation()))
    assert main(["db", "init", "--db", path]) == 0
    assert main(["db", "save", "--db", path, "--file", str(fixture)]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "ok"
    assert result["quality"] == "synthetic"
    assert result["as_of"] == "2026-09-20T08:00:00.000000Z"
    assert main(["db", "get", "--db", path, "--id", result["result"]["record_id"]]) == 0
    fetched = json.loads(capsys.readouterr().out)
    assert fetched["result"]["payload"]["inputs"]["ask_u"] is None
    assert fetched["as_of"] == result["as_of"]
    assert main(["db", "get", "--db", path, "--id", "missing"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_failed_process_rolls_back_uncommitted_write(db: Path) -> None:
    import subprocess
    import sys

    rid = save_observation(db, observation())["record_id"]
    script = """
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute('BEGIN IMMEDIATE')
c.execute("UPDATE schema_migrations SET checksum='uncommitted'")
os._exit(7)
"""
    process = subprocess.run([sys.executable, "-c", script, str(db)], check=False)
    assert process.returncode == 7
    assert health(db)["record_count"] == 1
    assert get_observation(db, rid)["record_id"] == rid


def test_concurrent_retries_write_once(db: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: save_observation(db, observation()), range(4)))
    assert len({r["record_id"] for r in results}) == 1
    assert sum(r["status"] == "created" for r in results) == 1
    assert health(db)["record_count"] == 1
