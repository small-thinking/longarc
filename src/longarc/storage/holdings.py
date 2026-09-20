"""Provisional short-call opening lots and repeated market observations."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from longarc.storage.store import canonical, check_schema, connect, digest, utc_now

CONTRACT = ("symbol", "option_type", "expiry_date", "strike_u", "multiplier")
LOT_REQUIRED = {
    "idempotency_key",
    "account_alias",
    "mode",
    *CONTRACT,
    "opening_contracts",
    "opening_premium_u",
    "opened_at",
    "source",
}
LOT_OPTIONAL = {"opening_fees_u", "evidence_ref", "external_execution_id"}
SNAP_REQUIRED = {"idempotency_key", "holding_id", "contract", "captured_at", "source", "quality"}
SNAP_OPTIONAL = {
    "quote_at",
    "greeks_at",
    "underlying_at",
    "underlying_price_u",
    "bid_u",
    "ask_u",
    "last_u",
    "delta",
    "theta",
    "volume",
    "prob_touching",
    "prob_otm",
    "change_u",
    "evidence_ref",
    "quality_notes",
}


def timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO string with timezone")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def integer(value: Any, name: str, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError(f"{name} must be a bounded integer >= {minimum}")


def text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def fields(payload: dict[str, Any], required: set[str], optional: set[str]) -> dict[str, Any]:
    if required - payload.keys() or payload.keys() - required - optional:
        raise ValueError("Missing or unknown fields")
    canonical(payload)
    return {**dict.fromkeys(optional), **payload}


def contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(CONTRACT):
        raise ValueError("Complete explicit contract identity required")
    p = dict(value)
    text(p["symbol"], "symbol")
    if p["symbol"] != p["symbol"].strip().upper():
        raise ValueError("Use canonical uppercase symbol")
    if p["option_type"] != "CALL":
        raise ValueError("This slice supports short CALL opening lots only")
    if not isinstance(p["expiry_date"], str):
        raise ValueError("expiry_date must be YYYY-MM-DD")
    p["expiry_date"] = date.fromisoformat(p["expiry_date"]).isoformat()
    integer(p["strike_u"], "strike_u", 1)
    integer(p["multiplier"], "multiplier", 1)
    return p


def _insert(
    db: Any, table: str, id_column: str, p: dict[str, Any], columns: tuple[str, ...]
) -> dict[str, Any]:
    # Table/column names are internal constants; payload values are bound parameters.
    content_hash = digest(p)
    old = db.execute(
        f"SELECT {id_column}, content_hash FROM {table} WHERE idempotency_key=?",
        (p["idempotency_key"],),
    ).fetchone()
    if old:
        if old["content_hash"] != content_hash:
            raise ValueError("Idempotency key already used with different content")
        return {"status": "existing", id_column: old[id_column]}
    names = (id_column, *columns, "recorded_at", "content_hash", "payload_json")
    values = (content_hash, *(p[c] for c in columns), utc_now(), content_hash, canonical(p))
    db.execute(
        f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", values
    )
    return {"status": "created", id_column: content_hash}


def add_holding(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    p = fields(payload, LOT_REQUIRED, LOT_OPTIONAL)
    p.update(contract({k: p[k] for k in CONTRACT}))
    for key in ("idempotency_key", "account_alias", "source"):
        text(p[key], key)
    for key in LOT_OPTIONAL - {"opening_fees_u"}:
        if p[key] is not None:
            text(p[key], key)
    if p["mode"] not in ("manual", "shadow"):
        raise ValueError("mode must be manual or shadow")
    for key in ("opening_contracts", "opening_premium_u", "opening_fees_u"):
        if p[key] is not None:
            integer(p[key], key, 1 if key == "opening_contracts" else 0)
    if p["opening_contracts"] is None or p["opening_premium_u"] is None:
        raise ValueError("Opening quantity and premium are required")
    p["opened_at"] = timestamp(p["opened_at"])
    columns = (
        "idempotency_key",
        "account_alias",
        "mode",
        *CONTRACT,
        "opening_contracts",
        "opening_premium_u",
        "opening_fees_u",
        "opened_at",
        "source",
        "evidence_ref",
        "external_execution_id",
    )
    with connect(path) as db, db:
        check_schema(db)
        db.execute("BEGIN IMMEDIATE")
        return _insert(db, "holding_lots", "holding_id", p, columns)


def add_snapshot(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    p = fields(payload, SNAP_REQUIRED, SNAP_OPTIONAL)
    if "underlying_at" not in payload:
        p.pop("underlying_at")  # Preserve canonical hashes of pre-existing snapshot retries.
    p["contract"] = contract(p["contract"])
    for key in ("idempotency_key", "holding_id", "source"):
        text(p[key], key)
    p["captured_at"] = timestamp(p["captured_at"])
    for key in ("quote_at", "greeks_at", "underlying_at"):
        if p.get(key) is not None:
            p[key] = timestamp(p[key])
    for key in ("underlying_price_u", "bid_u", "ask_u", "last_u", "volume"):
        if p[key] is not None:
            integer(p[key], key)
    if p["change_u"] is not None:
        integer(p["change_u"], "change_u", -(2**63))
    for key in ("delta", "theta", "prob_touching", "prob_otm"):
        v = p[key]
        if v is not None and (type(v) not in (float, int) or not math.isfinite(v)):
            raise ValueError(f"{key} must be finite or null")
        if v is not None and key != "theta" and not 0 <= v <= 1:
            raise ValueError(f"{key} must be in [0,1]")
    if p["quality"] not in ("synthetic", "unverified", "missing"):
        raise ValueError("Snapshot quality is not independently verified")
    if p["evidence_ref"] is not None:
        text(p["evidence_ref"], "evidence_ref")
    if p["quality_notes"] is None:
        p["quality_notes"] = []
    if not isinstance(p["quality_notes"], list):
        raise ValueError("quality_notes must be an array")
    for note in p["quality_notes"]:
        text(note, "quality note")
    columns = (
        "idempotency_key",
        "holding_id",
        "captured_at",
        "quote_at",
        "greeks_at",
        "source",
        "quality",
        "underlying_price_u",
        "bid_u",
        "ask_u",
        "last_u",
        "delta",
        "theta",
        "volume",
    )
    with connect(path) as db, db:
        check_schema(db)
        db.execute("BEGIN IMMEDIATE")
        lot = db.execute(
            "SELECT * FROM holding_lots WHERE holding_id=?", (p["holding_id"],)
        ).fetchone()
        if lot is None:
            raise ValueError("Holding not found")
        if p["contract"] != {k: lot[k] for k in CONTRACT}:
            raise ValueError("Snapshot contract does not match holding")
        if (lot["mode"] == "shadow") != (p["quality"] == "synthetic"):
            raise ValueError("Snapshot quality does not match holding mode")
        return _insert(db, "holding_snapshots", "snapshot_id", p, columns)


def list_holdings(path: Path, account: str, mode: str) -> list[dict[str, Any]]:
    with connect(path, readonly=True) as db:
        check_schema(db)
        return [
            dict(r)
            for r in db.execute(
                "SELECT holding_id, account_alias, mode, symbol, expiry_date, strike_u, "
                "opening_contracts, opened_at, 'not_reconciled' AS position_status "
                "FROM holding_lots WHERE account_alias=? AND mode=? ORDER BY opened_at, holding_id",
                (account, mode),
            )
        ]


def history(path: Path, holding_id: str, limit: int = 100) -> dict[str, Any]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    with connect(path, readonly=True) as db:
        check_schema(db)
        lot = db.execute("SELECT * FROM holding_lots WHERE holding_id=?", (holding_id,)).fetchone()
        if lot is None:
            raise ValueError("Holding not found")
        snapshots = []
        for row in db.execute(
            "SELECT snapshot_id, captured_at, recorded_at, payload_json FROM holding_snapshots "
            "WHERE holding_id=? ORDER BY captured_at DESC, recorded_at DESC, snapshot_id LIMIT ?",
            (holding_id, limit),
        ):
            snapshots.append(
                {
                    "snapshot_id": row["snapshot_id"],
                    "recorded_at": row["recorded_at"],
                    **json.loads(row["payload_json"]),
                }
            )
        total = db.execute(
            "SELECT count(*) FROM holding_snapshots WHERE holding_id=?", (holding_id,)
        ).fetchone()[0]
        return {
            "holding_id": holding_id,
            "opening": json.loads(lot["payload_json"]),
            "snapshots": snapshots,
            "position_status": "not_reconciled",
            "total_snapshots": total,
            "truncated": total > len(snapshots),
        }
