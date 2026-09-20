"""Transactional, append-only observation journal with explicit provenance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
) STRICT;
CREATE TABLE observations (
    record_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('observe','shadow','manual')),
    kind TEXT NOT NULL CHECK(kind IN ('observation','no_action','error','calculation')),
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL,
    quality TEXT NOT NULL CHECK(quality IN ('synthetic','unverified','verified','missing')),
    code_version TEXT NOT NULL,
    policy_hash TEXT,
    input_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_id TEXT UNIQUE REFERENCES observations(record_id),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    CHECK(mode != 'shadow' OR quality = 'synthetic')
) STRICT;
CREATE INDEX observation_time ON observations(scope, observed_at, recorded_at);
CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'observations are append-only'); END;
CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'observations are append-only'); END;
"""
CHECKSUM = hashlib.sha256(SCHEMA.encode()).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@contextmanager
def connect(
    path: Path, *, create: bool = False, readonly: bool = False
) -> Iterator[sqlite3.Connection]:
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    mode = "ro" if readonly else ("rwc" if create else "rw")
    db = sqlite3.connect(path.resolve().as_uri() + f"?mode={mode}", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        if not readonly:
            db.execute("PRAGMA synchronous=FULL")
        yield db
    finally:
        db.close()


def check_schema(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    if [(r[0], r[1]) for r in rows] != [(1, CHECKSUM)]:
        raise ValueError("Unsupported or modified schema migration; refusing access")


def initialize(path: Path) -> dict[str, Any]:
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise ValueError("SQLite >= 3.37 required")
    with connect(path, create=True) as db:
        if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
            check_schema(db)
        else:
            # Explicit transaction includes the entire migration and its receipt.
            try:
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                db.execute("INSERT INTO schema_migrations VALUES (1, ?, ?)", (CHECKSUM, utc_now()))
                db.commit()
            except Exception:
                db.rollback()
                raise
        db.execute("PRAGMA journal_mode=WAL")
    return health(path)


def health(path: Path) -> dict[str, Any]:
    with connect(path, readonly=True) as db:
        check_schema(db)
        integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
        fk = list(db.execute("PRAGMA foreign_key_check"))
        if integrity != ["ok"] or fk:
            raise ValueError("Database integrity check failed")
        return {
            "status": "ok",
            "database": str(path.resolve()),
            "schema_version": 1,
            "sqlite_version": sqlite3.sqlite_version,
            "integrity": "ok",
            "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
            "record_count": db.execute("SELECT count(*) FROM observations").fetchone()[0],
        }


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "idempotency_key",
        "scope",
        "mode",
        "kind",
        "observed_at",
        "source",
        "quality",
        "code_version",
        "inputs",
        "results",
        "evidence_ids",
    }
    optional = {"policy_hash", "supersedes_id"}
    if required - payload.keys() or payload.keys() - required - optional:
        raise ValueError("Missing or unknown observation fields")
    p = dict(payload)
    for key in required - {"inputs", "results", "evidence_ids"}:
        if not isinstance(p[key], str) or not p[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    for key, allowed in {
        "mode": {"observe", "shadow", "manual"},
        "kind": {"observation", "no_action", "error", "calculation"},
        "quality": {"synthetic", "unverified", "verified", "missing"},
    }.items():
        if p[key] not in allowed:
            raise ValueError(f"Invalid {key}")
    if (p["mode"] == "shadow") != (p["quality"] == "synthetic"):
        raise ValueError("Synthetic observations must use shadow mode and vice versa")
    if not isinstance(p["inputs"], dict) or not isinstance(p["results"], dict):
        raise ValueError("inputs and results must be objects")
    if not isinstance(p["evidence_ids"], list) or any(
        not isinstance(x, str) or not x.strip() for x in p["evidence_ids"]
    ):
        raise ValueError("evidence_ids must be a list of nonempty references")
    if p["quality"] == "verified" and not p["evidence_ids"]:
        raise ValueError("Verified observations require evidence references")
    for key in optional:
        p.setdefault(key, None)
        if p[key] is not None and (not isinstance(p[key], str) or not p[key].strip()):
            raise ValueError(f"Invalid {key}")
    if p["policy_hash"] is not None and (
        len(p["policy_hash"]) != 64 or any(c not in "0123456789abcdef" for c in p["policy_hash"])
    ):
        raise ValueError("policy_hash must be a lowercase SHA-256 hash or null")
    dt = datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("observed_at requires an explicit timezone")
    p["observed_at"] = dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    canonical(p)  # Reject NaN/Infinity and non-JSON values before opening a transaction.
    return p


def save_observation(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    p = validate(payload)
    content_hash = digest(p)
    with connect(path) as db:
        check_schema(db)
        with db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM observations WHERE idempotency_key=?", (p["idempotency_key"],)
            ).fetchone()
            if existing:
                if existing["content_hash"] != content_hash:
                    raise ValueError("Idempotency key already used with different content")
                return {
                    "status": "existing",
                    "record_id": existing["record_id"],
                    "input_hash": existing["input_hash"],
                }
            if p["supersedes_id"]:
                old = db.execute(
                    "SELECT scope, mode FROM observations WHERE record_id=?", (p["supersedes_id"],)
                ).fetchone()
                if old is None or (old["scope"], old["mode"]) != (p["scope"], p["mode"]):
                    raise ValueError(
                        "Correction must reference an existing record in same scope/mode"
                    )
            record_id = content_hash
            input_hash = digest(p["inputs"])
            db.execute(
                """INSERT INTO observations VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    p["idempotency_key"],
                    p["scope"],
                    p["mode"],
                    p["kind"],
                    p["observed_at"],
                    utc_now(),
                    p["source"],
                    p["quality"],
                    p["code_version"],
                    p["policy_hash"],
                    input_hash,
                    content_hash,
                    p["supersedes_id"],
                    canonical(p),
                ),
            )
        return {"status": "created", "record_id": record_id, "input_hash": input_hash}


def get_observation(path: Path, record_id: str) -> dict[str, Any]:
    with connect(path, readonly=True) as db:
        check_schema(db)
        row = db.execute("SELECT * FROM observations WHERE record_id=?", (record_id,)).fetchone()
        if row is None:
            raise ValueError("Observation not found")
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result


def list_observations(path: Path, scope: str, limit: int = 20) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    with connect(path, readonly=True) as db:
        check_schema(db)
        return [
            dict(r)
            for r in db.execute(
                "SELECT record_id, kind, mode, observed_at, recorded_at, quality, supersedes_id "
                "FROM observations WHERE scope=? ORDER BY recorded_at DESC, record_id LIMIT ?",
                (scope, limit),
            )
        ]


def copy_database(source: Path, destination: Path) -> dict[str, Any]:
    """Consistent backup/restore into a NEW file; never overwrite a live database."""
    health(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents accidentally overwriting the source via an alias.
    with destination.open("xb"):
        pass
    try:
        with connect(source, readonly=True) as src, connect(destination) as dest:
            src.backup(dest)
        return health(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
