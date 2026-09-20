"""Evidence-backed short-call event ledger, using the existing observation schema.

Prices are actual per-share fills in integer microdollars; fees are actual total
execution fees (null means unknown). Quotes and estimates are not executions.
Closes explicitly identify one opening execution; split multi-lot fills before
entry. A roll is separate BTC and STO events sharing an episode_id. No broker
reconciliation, corrections, stock cost basis, taxes, or portfolio risk model is
provided. Realized drawdown excludes open option and stock mark-to-market risk.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from longarc.storage import store
from longarc.storage.holdings import contract, fields, integer, text, timestamp

FORMAT = "short-call-execution-v1"
REQUIRED = {
    "account",
    "mode",
    "external_execution_id",
    "action",
    "contract",
    "quantity",
    "price_u",
    "fees_u",
    "executed_at",
    "source",
    "evidence_ref",
    "episode_id",
}
OPTIONAL = {"opening_execution_id", "policy_hash", "decision_record_id"}


def _scope(account: str) -> str:
    return "executions:" + store.digest(account)


def _events(db: Any, account: str, mode: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT payload_json FROM observations WHERE scope=? AND mode=? ORDER BY rowid",
        (_scope(account), mode),
    )
    return [
        p["inputs"]["execution"]
        for row in rows
        if (p := json.loads(row["payload_json"]))["inputs"].get("format") == FORMAT
    ]


def record_execution(path: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Record a confirmed execution/event; replay same external ID is idempotent."""
    e = fields(request, REQUIRED, OPTIONAL)
    for key in ("account", "external_execution_id", "source", "evidence_ref", "episode_id"):
        text(e[key], key)
    if e["mode"] not in {"manual", "shadow"}:
        raise ValueError("mode must be manual or shadow")
    if e["action"] not in {"STO", "BTC", "EXPIRE", "ASSIGN"}:
        raise ValueError("Unsupported execution action")
    e["contract"] = contract(e["contract"])
    if e["contract"]["symbol"] != "QQQ":
        raise ValueError("This ledger supports QQQ calls only")
    for key in ("policy_hash", "decision_record_id"):
        if e[key] is not None:
            text(e[key], key)
    integer(e["quantity"], "quantity", 1)
    integer(e["price_u"], "price_u")
    if e["fees_u"] is not None:
        integer(e["fees_u"], "fees_u")
    e["executed_at"] = timestamp(e["executed_at"])
    if e["action"] in {"EXPIRE", "ASSIGN"} and e["price_u"] != 0:
        raise ValueError("EXPIRE/ASSIGN option settlement price must be zero")
    if e["action"] == "STO":
        if e["opening_execution_id"] is not None:
            raise ValueError("STO cannot reference an opening execution")
    else:
        text(e["opening_execution_id"], "opening_execution_id")
    local_date = (
        datetime.fromisoformat(e["executed_at"].replace("Z", "+00:00"))
        .astimezone(ZoneInfo("America/New_York"))
        .date()
        .isoformat()
    )
    if e["action"] == "EXPIRE" and local_date < e["contract"]["expiry_date"]:
        raise ValueError("Expiration confirmation cannot precede expiry date")
    identity = [e["account"], e["mode"], e["external_execution_id"]]
    p = store.validate(
        {
            "idempotency_key": "execution:" + store.digest(identity),
            "scope": _scope(e["account"]),
            "mode": e["mode"],
            "kind": "observation",
            "observed_at": e["executed_at"],
            "source": e["source"],
            "quality": "synthetic" if e["mode"] == "shadow" else "unverified",
            "code_version": FORMAT,
            "inputs": {"format": FORMAT, "execution": e},
            "results": {},
            "policy_hash": e["policy_hash"],
            "evidence_ids": [e["evidence_ref"]],
        }
    )
    content_hash = store.digest(p)
    with store.connect(path) as db, db:
        store.check_schema(db)
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT record_id, content_hash FROM observations WHERE idempotency_key=?",
            (p["idempotency_key"],),
        ).fetchone()
        if old:
            if old["content_hash"] != content_hash:
                raise ValueError("Execution ID already used with different content")
            return {"status": "existing", "record_id": old["record_id"], "readback_verified": True}
        events = _events(db, e["account"], e["mode"])
        if e["action"] != "STO":
            opening = next(
                (
                    x
                    for x in events
                    if x["external_execution_id"] == e["opening_execution_id"]
                    and x["action"] == "STO"
                ),
                None,
            )
            if opening is None:
                raise ValueError("Opening execution not found in account/mode")
            if opening["contract"] != e["contract"]:
                raise ValueError("Closing contract differs from opening")
            if opening["episode_id"] != e["episode_id"]:
                raise ValueError("Closing episode differs from opening")
            previous = [x for x in events if x["opening_execution_id"] == e["opening_execution_id"]]
            if e["executed_at"] < max(x["executed_at"] for x in [opening, *previous]):
                raise ValueError("Close precedes opening or previous close; import chronologically")
            if sum(x["quantity"] for x in previous) + e["quantity"] > opening["quantity"]:
                raise ValueError("Close would exceed remaining opening quantity")
        db.execute(
            "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                content_hash,
                p["idempotency_key"],
                p["scope"],
                p["mode"],
                p["kind"],
                p["observed_at"],
                store.utc_now(),
                p["source"],
                p["quality"],
                p["code_version"],
                p["policy_hash"],
                store.digest(p["inputs"]),
                content_hash,
                None,
                store.canonical(p),
            ),
        )
    saved = store.get_observation(path, content_hash)
    if saved["payload"] != p:
        raise ValueError("Execution readback verification failed")
    return {"status": "created", "record_id": content_hash, "readback_verified": True}


def performance(path: Path, account: str, mode: str) -> dict[str, Any]:
    """Return closed-option results and remaining quantities, never portfolio returns."""
    text(account, "account")
    if mode not in {"manual", "shadow"}:
        raise ValueError("mode must be manual or shadow")
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        events = _events(db, account, mode)
    openings = {e["external_execution_id"]: e for e in events if e["action"] == "STO"}
    remaining = {key: e["quantity"] for key, e in openings.items()}
    allocated_totals = dict.fromkeys(openings, Decimal(0))
    closes = []
    cumulative = Decimal(0)
    gross_total = Decimal(0)
    peak = Decimal(0)
    drawdown = Decimal(0)
    complete = True
    episodes: dict[str, dict[str, Any]] = {}
    for e in sorted(events, key=lambda x: x["executed_at"]):
        if e["action"] == "STO":
            continue
        opening = openings[e["opening_execution_id"]]
        remaining[e["opening_execution_id"]] -= e["quantity"]
        gross = Decimal(
            (opening["price_u"] - e["price_u"]) * e["quantity"] * e["contract"]["multiplier"]
        )
        fees_known = opening["fees_u"] is not None and e["fees_u"] is not None
        key = e["opening_execution_id"]
        allocated = None
        if opening["fees_u"] is not None:
            # Round cumulative allocation, not each fill independently: this avoids
            # negative residual fees after many tiny partial closes.
            closed_quantity = opening["quantity"] - remaining[key]
            target = (Decimal(opening["fees_u"]) * closed_quantity / opening["quantity"]).quantize(
                Decimal(1)
            )
            allocated = target - allocated_totals[key]
            allocated_totals[key] = target
        net = gross - allocated - e["fees_u"] if fees_known and allocated is not None else None
        gross_total += gross
        complete = complete and net is not None
        if net is not None:
            cumulative += net
        if complete:
            peak = max(peak, cumulative)
            drawdown = max(drawdown, peak - cumulative)
        closes.append(
            {
                "external_execution_id": e["external_execution_id"],
                "opening_execution_id": e["opening_execution_id"],
                "episode_id": e["episode_id"],
                "executed_at": e["executed_at"],
                "action": e["action"],
                "quantity": e["quantity"],
                "gross_option_pnl_u": str(gross),
                "allocated_opening_fees_u": str(allocated) if allocated is not None else None,
                "net_option_pnl_u": str(net) if net is not None else None,
                "cumulative_net_option_pnl_u": str(cumulative) if complete else None,
                "stock_pnl_unknown": e["action"] == "ASSIGN",
                "opening_policy_hash": opening["policy_hash"],
                "opening_decision_record_id": opening["decision_record_id"],
                "policy_hash": e["policy_hash"],
                "decision_record_id": e["decision_record_id"],
            }
        )
        ep = episodes.setdefault(
            e["episode_id"],
            {"gross": Decimal(0), "net": Decimal(0), "complete": True, "closed_quantity": 0},
        )
        ep["gross"] += gross
        ep["net"] += net if net is not None else 0
        ep["complete"] = ep["complete"] and net is not None
        ep["closed_quantity"] += e["quantity"]
    return {
        "account": account,
        "mode": mode,
        "execution_count": len(events),
        "realized_gross_option_pnl_u": str(gross_total),
        "realized_net_option_pnl_u": str(cumulative) if complete else None,
        "fees_complete": complete,
        "realized_net_max_drawdown_u": str(drawdown) if complete else None,
        "closed_events": closes,
        "open_lots": [
            {
                "opening_execution_id": key,
                "remaining_quantity": qty,
                "contract": openings[key]["contract"],
                "episode_id": openings[key]["episode_id"],
                "policy_hash": openings[key]["policy_hash"],
                "decision_record_id": openings[key]["decision_record_id"],
            }
            for key, qty in remaining.items()
            if qty
        ],
        "episodes": [
            {
                "episode_id": key,
                "closed_quantity": ep["closed_quantity"],
                "policy_hashes": sorted(
                    {
                        x["policy_hash"]
                        for x in events
                        if x["episode_id"] == key and x["policy_hash"]
                    }
                ),
                "realized_gross_option_pnl_u": str(ep["gross"]),
                "realized_net_option_pnl_u": str(ep["net"]) if ep["complete"] else None,
            }
            for key, ep in episodes.items()
        ],
        "limitations": [
            "Realized option P&L only; excludes open option and stock market risk",
            "Assignment premium is option bookkeeping, not tax allocation; stock P&L unknown",
            "Taxes and broker reconciliation are unavailable",
            "Opening fees allocated pro rata rounded to microdollars; final close gets residual",
            "No return percentage or strategy effectiveness inferred from small samples",
        ],
    }
