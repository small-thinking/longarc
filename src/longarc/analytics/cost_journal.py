"""Audited cost scenarios, separate from fills and current holdings."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from longarc.analytics import costs
from longarc.storage import store


def estimate_and_log(
    path: Path, request: dict[str, Any], schedule: dict[str, Any],
) -> dict[str, Any]:
    required = {"idempotency_key", "as_of", "source", "mode", "scenario", "evidence_ids"}
    if set(request) != required:
        raise ValueError("Cost request requires " + ", ".join(sorted(required)))
    event = store.validate({
        "idempotency_key": request["idempotency_key"], "observed_at": request["as_of"],
        "scope": "options:QQQ:costs", "mode": request["mode"], "kind": "calculation",
        "quality": "synthetic" if request["mode"] == "shadow" else "unverified",
        "source": request["source"], "code_version": "costs-v1:" + hashlib.sha256(
            Path(costs.__file__).read_bytes() + Path(__file__).read_bytes()).hexdigest(),
        "inputs": {"schedule": schedule, "scenario": request["scenario"]},
        "results": {}, "evidence_ids": request["evidence_ids"],
    })
    try:
        result = costs.calculate_costs(schedule, **request["scenario"])
    except (TypeError, ValueError) as exc:
        result = {"status": "error", "error": str(exc), "warnings": []}
        event["kind"] = "error"
    result["warnings"].append("Fee schedule is a dated estimate; not a fill or reconciled P&L")
    event["results"] = result
    receipt = store.save_observation(path, event)
    saved = store.get_observation(path, receipt["record_id"])
    if saved["payload"]["results"] != result:
        raise ValueError("Cost readback mismatch")
    return {**result, "record_id": receipt["record_id"], "write_status": receipt["status"],
            "readback_verified": True}
