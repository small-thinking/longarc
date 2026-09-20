"""Load immutable inputs, calculate, and append a reproducible audit record."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from longarc import __version__
from longarc.analytics import metrics
from longarc.storage import holdings, store


def _snapshot(path: Path, snapshot_id: str) -> dict[str, Any]:
    holdings.text(snapshot_id, "snapshot_id")
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        row = db.execute(
            "SELECT payload_json FROM holding_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
    if row is None:
        raise ValueError("Snapshot not found")
    return dict(json.loads(row["payload_json"]))


def _inputs(path: Path, inputs: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
    p = dict(inputs)
    if p.keys() - {"contract", "snapshot", "snapshot_id", "previous", "previous_snapshot_id",
                   "scenario", "replacement"}:
        raise ValueError("Unknown calculation input")
    if "snapshot_id" in p:
        if "contract" in p or "snapshot" in p:
            raise ValueError("Use snapshot_id OR contract/snapshot")
        if "previous" in p:
            raise ValueError("Stored history requires previous_snapshot_id")
        snapshot = _snapshot(path, p["snapshot_id"])
        if header["scope"] != "holding:" + snapshot["holding_id"]:
            raise ValueError("Stored snapshot scope must be holding:<holding_id>")
        if header["source"] != snapshot["source"] or header["quality"] != snapshot["quality"]:
            raise ValueError("Source/quality must match stored snapshot")
        p.update(contract=snapshot["contract"], snapshot=snapshot)
    if not isinstance(p.get("contract"), dict) or not isinstance(p.get("snapshot"), dict):
        raise ValueError("contract and snapshot objects required")
    if "snapshot_id" not in p and "holding_id" in p["snapshot"]:
        raise ValueError("Use snapshot_id for a holding snapshot")
    if "previous_snapshot_id" in p:
        if "previous" in p or "snapshot_id" not in p:
            raise ValueError("previous_snapshot_id requires snapshot_id and no previous input")
        previous = _snapshot(path, p["previous_snapshot_id"])
        if previous["holding_id"] != p["snapshot"]["holding_id"]:
            raise ValueError("Previous snapshot must belong to the same holding")
        if previous["quality"] != header["quality"]:
            raise ValueError("Previous snapshot quality mismatch")
        p["previous"] = {"contract": previous["contract"], "snapshot": previous}
    if p.get("previous") is not None:
        previous = p["previous"]
        if not isinstance(previous, dict) or set(previous) != {"contract", "snapshot"}:
            raise ValueError("previous requires contract and snapshot")
        if previous["contract"] != p["contract"]:
            raise ValueError("Previous contract mismatch")
        old, current = previous["snapshot"], p["snapshot"]
        if not isinstance(old, dict):
            raise ValueError("Previous snapshot must be an object")
        for key in ("captured_at", "quote_at", "greeks_at", "underlying_at"):
            if old.get(key) is not None and current.get(key) is not None:
                if holdings.timestamp(old[key]) > holdings.timestamp(current[key]):
                    raise ValueError("Previous snapshot must not be newer than current snapshot")
        if old.get("captured_at") is None or current.get("captured_at") is None:
            raise ValueError("History comparison requires both capture timestamps")
    for item in (p, p.get("previous"), p.get("replacement")):
        if item is None:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("snapshot"), dict):
            raise ValueError("Quote input requires a snapshot object")
        snap = item["snapshot"]
        if "contract" in snap and snap["contract"] != item.get("contract"):
            raise ValueError("Embedded snapshot contract mismatch")
        if "quality" in snap and snap["quality"] != header["quality"]:
            raise ValueError("Snapshot quality mismatch")
    return p


def calculate_and_log(path: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Explicit as_of and idempotency key make retries identical; never infer positions."""
    required = {"idempotency_key", "scope", "mode", "source", "quality", "as_of",
                "inputs", "evidence_ids"}
    if not isinstance(request, dict) or required - request.keys() or (
        request.keys() - required - {"policy_hash"}
    ):
        raise ValueError("Missing or unknown calculation request fields")
    if request["quality"] not in ("synthetic", "unverified", "missing"):
        raise ValueError("Calculator cannot certify source data as verified")
    # Hash the actual calculation and adapter source, including uncommitted development code.
    revision = hashlib.sha256(
        Path(metrics.__file__).read_bytes() + Path(__file__).read_bytes()
    ).hexdigest()
    event = store.validate({
        **{k: v for k, v in request.items() if k != "as_of"},
        "observed_at": request["as_of"], "kind": "calculation", "results": {},
        "code_version": f"{__version__}/calc-{revision}",
    })
    resolved = request["inputs"]
    try:
        resolved = _inputs(path, resolved, event)
        result = metrics.calculate(
            resolved["contract"], resolved["snapshot"], event["observed_at"],
            scenario=resolved.get("scenario"),
            previous=resolved["previous"]["snapshot"] if resolved.get("previous") else None,
            replacement=resolved.get("replacement"),
        )
    except (ValueError, TypeError, KeyError) as exc:
        result = {"status": "error", "metrics": {}, "warnings": [], "error": str(exc)}
        event["kind"] = "error"
    result["warnings"].append("No policy evaluation or trading approval; quantities are scenarios")
    event["inputs"] = {"as_of": event["observed_at"], **resolved}
    event["results"] = result
    event["evidence_ids"] = list(dict.fromkeys([
        *event["evidence_ids"],
        *(resolved[key] for key in ("snapshot_id", "previous_snapshot_id")
          if isinstance(resolved.get(key), str) and resolved[key]),
    ]))
    receipt = store.save_observation(path, event)
    return {**result, **{k: event[k] for k in ("source", "quality", "code_version", "policy_hash")},
            "as_of": event["observed_at"], "evidence_ids": event["evidence_ids"],
            "record_id": receipt["record_id"], "input_hash": receipt["input_hash"],
            "write_status": receipt["status"]}
