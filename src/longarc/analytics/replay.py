"""Observed-checkpoint shadow replay using the same advisory policy as live reviews.

No interpolation, intraday trigger inference, broker actions or invented settlements.
Each request fixes one entry candidate and supplies subsequent observation IDs.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from longarc.analytics import costs, decisions
from longarc.analytics.metrics import _time
from longarc.core import symbols
from longarc.storage import store

FORMAT = "policy-replay-v1"
CHECKS = {"market_open", "dividend_window_clear", "quote_usable", "greeks_usable",
          "underlying_usable", "standard_contract", "reentry_cooldown_clear"}


def _engine_version() -> str:
    return FORMAT + ":" + hashlib.sha256(
        Path(__file__).read_bytes() + Path(decisions.__file__).read_bytes()
        + Path(costs.__file__).read_bytes() + Path(symbols.__file__).read_bytes()).hexdigest()


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _identity(contract: dict[str, Any], symbol: str = "QQQ") -> tuple[str, int]:
    from datetime import date
    if (contract.get("symbol", symbol) != symbol
            or contract.get("option_type", "CALL") != "CALL"):
        raise ValueError("Replay requires a CALL identity with the same underlying")
    return (date.fromisoformat(contract["expiry_date"]).isoformat(),
            _integer(contract["strike_u"], "strike_u", 1))


def _quote(payload: dict[str, Any], identity: tuple[str, int],
           symbol: str) -> dict[str, Any] | None:
    return next((q for q in payload["results"].get("quotes", [])
                 if q["contract"]["symbol"] == symbol
                 and _identity(q["contract"], symbol) == identity
                 and q["contract"]["option_type"] == "CALL"), None)


def replay(path: Path, request: dict[str, Any], policy: dict[str, Any],
           schedule: dict[str, Any]) -> dict[str, Any]:
    """One episode, fixed size; a roll closes one leg and opens the next obligation.

    All execution/coverage assumptions are explicit. Source flags are attestations,
    additionally gated by real source timestamps and bid/ask validation here.
    """
    episode = request["episode_id"]
    if not isinstance(episode, str) or not episode.strip():
        raise ValueError("episode_id required")
    a = request["assumptions"]
    for field in ("contracts", "multiplier", "max_gap_hours", "max_quote_age_seconds",
                  "max_greeks_age_seconds", "max_underlying_age_seconds"):
        _integer(a[field], field, 1)
    for field in ("covered_shares", "opening_slippage_u", "closing_slippage_u",
                  "opening_extra_fees_u", "closing_extra_fees_u"):
        _integer(a[field], field)
    if type(a["allow_roll"]) is not bool:
        raise ValueError("allow_roll must be boolean")
    frames = request["observations"]
    if not isinstance(frames, list) or not frames:
        raise ValueError("At least one observation required")
    symbol = symbols.canonical_symbol(request["contract"].get("symbol", "QQQ"))
    symbols.require_policy_symbol(symbol, policy)
    identity = _identity(request["contract"], symbol)
    sources = [store.get_observation(path, f["record_id"])["payload"] for f in frames]
    modes = {s["mode"] for s in sources}
    if len(modes) != 1 or not modes <= {"observe", "shadow"}:
        raise ValueError("Source modes must be uniformly observe or shadow")
    previous = None
    for source in sources:
        if (source["scope"] != f"options:{symbol}"
                or source["inputs"].get("symbol") != symbol
                or source["inputs"].get("format") != "option-chain-v1"
                or any(q["contract"].get("symbol") != symbol
                       for q in source["results"].get("quotes", []))):
            raise ValueError("Replay requires canonical chain evidence for the same symbol")
        now = _time(source["observed_at"])
        if previous is not None and now <= previous:
            raise ValueError("Observations must be strictly chronological, without duplicates")
        previous = now
    assumptions_hash = store.digest({"assumptions": a, "fees": schedule,
                                     "source_mode": next(iter(modes)),
                                     "engine_version": _engine_version()})
    result: dict[str, Any] = {
        "episode_id": episode, "symbol": symbol, "policy_hash": store.digest(policy),
        "engine_version": _engine_version(),
        "assumptions_hash": assumptions_hash, "status": "no_entry", "mode": "shadow",
        "source_mode": next(iter(modes)), "started_at": None, "ended_at": None,
        "net_option_pnl_u": None, "observed_pnl_u": None, "open_liquidation_pnl_u": None,
        "evidence_ids": [f["record_id"] for f in frames], "decisions": [], "legs": [],
        "gaps": [], "watch_contracts": [],
        "limitations": ["Hypothetical bid-minus/ask-plus-slippage fills, not executions",
                        "Policy evaluated only at observed checkpoints; intraday triggers unknown",
                        "Freshness/dividend/calendar checks still depend on supplied evidence",
                        "No assignment model or guarantee of retaining shares",
                        "Missing paths excluded from estimates; eligible samples may be biased",
                        "Option overlay only, excludes stock P&L and taxes"],
    }
    opening: dict[str, Any] | None = None
    total = 0
    rolls = 0
    previous = None

    def facts(source: dict[str, Any], frame: dict[str, Any], key: tuple[str, int],
              replacement: bool = False) -> dict[str, Any]:
        checks = frame.get("replacement_checks" if replacement else "checks", {})
        if set(checks) - CHECKS or any(v is not None and type(v) is not bool
                                     for v in checks.values()):
            raise ValueError("Only explicit boolean source checks are accepted")
        q = _quote(source, key, symbol)
        snap = q["snapshot"] if q else {}
        now = _time(source["observed_at"])
        f = {**snap, **checks, "symbol": symbol, "option_type": "CALL",
             "expiry_date": key[0], "strike_u": key[1], "contracts": a["contracts"],
             "multiplier": a["multiplier"], "position_contracts": a["contracts"] if opening else 0,
             "position_verified": True, "orders_clear": True, "sizing_approved": True,
             "coverage_verified": a["covered_shares"] >= a["contracts"] * a["multiplier"],
             "episode_roll_count": rolls}
        if source["inputs"].get("closed_session"):
            f["market_open"] = False
        for kind in ("quote", "greeks", "underlying"):
            stamp = snap.get(kind + "_at")
            try:
                age = (now - _time(stamp)).total_seconds() if stamp else None
            except (ValueError, TypeError):
                age = None
            f[kind + "_usable"] = (checks.get(kind + "_usable") is True
                                    and age is not None
                                    and 0 <= age <= a["max_" + kind + "_age_seconds"])
        if q and q["contract"].get("multiplier") not in (None, a["multiplier"]):
            raise ValueError("Source multiplier conflicts with replay assumption")
        return f

    def priced(f: dict[str, Any]) -> bool:
        return (f.get("market_open") is True and f.get("quote_usable") is True
                and type(f.get("bid_u")) is int and type(f.get("ask_u")) is int
                and 0 <= f["bid_u"] <= f["ask_u"])

    def cost(f: dict[str, Any], premium: int) -> dict[str, Any]:
        return dict(costs.calculate_costs(
            schedule, contracts=a["contracts"], multiplier=a["multiplier"],
            opening_premium_u=premium, closing_bid_u=f["bid_u"], closing_ask_u=f["ask_u"],
            closing_slippage_u=a["closing_slippage_u"],
            opening_extra_fees_u=a["opening_extra_fees_u"],
            closing_extra_fees_u=a["closing_extra_fees_u"],
        )["metrics"])

    for frame, source in zip(frames, sources, strict=True):
        now = source["observed_at"]
        if previous is not None and (_time(now) - _time(previous)).total_seconds() > \
                a["max_gap_hours"] * 3600:
            result["gaps"].append({"as_of": now, "reason": "sampling_gap"})
        previous = now
        f = facts(source, frame, identity)
        close = None
        if opening:
            f["opening_premium_u"] = opening["premium_u"]
            f["opening_executed_at"] = opening["opened_at"]
            if priced(f):
                close = cost(f, opening["premium_u"])
                f["allocated_opening_fees_u"] = int(Decimal(close["opening_fees_u"]))
                f["estimated_closing_total_fees_u"] = int(Decimal(close["closing_fees_u"]))
        decision_request: dict[str, Any] = {"as_of": now, "facts": f}
        replacement = None
        replacement_key = None
        if opening and a["allow_roll"] and frame.get("replacement"):
            replacement_key = _identity(frame["replacement"], symbol)
            replacement = facts(source, frame, replacement_key, True)
            if priced(replacement):
                rc = cost(replacement, replacement["bid_u"])
                replacement["opening_total_fees_u"] = int(Decimal(rc["opening_fees_u"]))
            decision_request["replacement"] = replacement
        decision = decisions.evaluate(decision_request, policy)
        action = decision["action"]
        result["decisions"].append({"as_of": now, "record_id": frame["record_id"],
                                    "contract": list(identity), **decision})
        if opening is None:
            if action != "STO_CANDIDATE" or not priced(f):
                if (not priced(f) or decision["unknown_checks"]
                        or any(f.get(k) is not True for k in CHECKS)):
                    result["status"] = "incomplete"
                    result["gaps"].append({"as_of": now, "reason": "entry_evidence_incomplete"})
                break  # Fixed entry checkpoint: no hindsight selection of a later opening.
            premium = f["bid_u"] - a["opening_slippage_u"]
            if premium <= 0:
                raise ValueError("Opening slippage consumes premium")
            opening = {"contract": list(identity), "premium_u": premium, "opened_at": now}
            result.update(status="open", started_at=now)
            continue
        if action in {"INSUFFICIENT_DATA", "RECONCILE"}:
            result["gaps"].append({"as_of": now, "reason": action})
        if close:
            result["open_liquidation_pnl_u"] = total + int(Decimal(close["net_option_pnl_u"]))
        else:
            result["open_liquidation_pnl_u"] = None
        if action in {"BTC_RISK", "BTC_PROFIT", "ROLL_CANDIDATE"}:
            if close is None:
                result["gaps"].append({"as_of": now, "reason": "exit_not_executable"})
                continue
            if action == "BTC_PROFIT" and Decimal(close["net_option_pnl_u"]) <= 0:
                result["decisions"][-1]["fill_blocked"] = "profit_after_slippage_nonpositive"
                continue
            new_premium = None
            if action == "ROLL_CANDIDATE":
                if replacement is None or not priced(replacement):
                    result["gaps"].append({"as_of": now, "reason": "roll_not_executable"})
                    continue
                new_premium = replacement["bid_u"] - a["opening_slippage_u"]
                credit = ((new_premium - f["ask_u"] - a["closing_slippage_u"])
                          * a["contracts"] * a["multiplier"]
                          - int(Decimal(close["closing_fees_u"]))
                          - replacement["opening_total_fees_u"])
                if new_premium <= 0 or credit < 0:
                    result["decisions"][-1]["fill_blocked"] = "roll_credit_after_slippage_negative"
                    continue
            leg_net = int(Decimal(close["net_option_pnl_u"]))
            total += leg_net
            result["legs"].append({**opening, "closed_at": now, "action": action,
                                   "costs": close, "net_option_pnl_u": leg_net})
            if new_premium is not None and replacement_key is not None:
                identity = replacement_key
                opening = {"contract": list(identity), "premium_u": new_premium, "opened_at": now}
                rolls += 1
                result["open_liquidation_pnl_u"] = None
            else:
                result.update(status="closed", ended_at=now, observed_pnl_u=total,
                              net_option_pnl_u=total, open_liquidation_pnl_u=None)
                opening = None
                break
    if result["gaps"]:
        result["status"] = "incomplete"
        result["net_option_pnl_u"] = None
    if opening:
        result["watch_contracts"] = [{"symbol": symbol, "expiry_date": identity[0],
                                      "strike_u": identity[1]}]
        result["realized_closed_legs_pnl_u"] = total
    return result


def replay_and_log(path: Path, request: dict[str, Any], policy: dict[str, Any],
                   schedule: dict[str, Any]) -> dict[str, Any]:
    result = replay(path, request, policy, schedule)
    # An episode may grow a path, but must not move its entry or rewrite old facts.
    with store.connect(path, readonly=True) as db:
        rows = db.execute("SELECT payload_json FROM observations WHERE scope=? AND mode='shadow'",
                          (f"options:{result['symbol']}:replays",)).fetchall()
    for row in rows:
        old = json.loads(row["payload_json"])
        prior = old["results"]
        if (old["inputs"].get("format") == FORMAT
                and all(prior[k] == result[k] for k in
                        ("episode_id", "policy_hash", "assumptions_hash"))):
            old_request = old["inputs"]["request"]
            old_frames = old_request["observations"]
            if (old_request["contract"] != request["contract"]
                    or request["observations"][:len(old_frames)] != old_frames):
                raise ValueError("Episode revisions must extend the original path; use a new "
                                 "episode ID for corrected or alternative entry evidence")
    version = _engine_version()
    identity = {"request": request, "policy": policy, "schedule": schedule, "version": version}
    event = {
        "idempotency_key": "replay:" + store.digest(identity),
        "scope": f"options:{result['symbol']}:replays",
        "mode": "shadow", "kind": "calculation", "quality": "synthetic",
        "observed_at": request["as_of"], "source": "observed_checkpoint_replay",
        "code_version": version, "inputs": {"format": FORMAT, **identity}, "results": result,
        "evidence_ids": result["evidence_ids"], "policy_hash": result["policy_hash"],
    }
    if _time(request["as_of"]) < max(_time(store.get_observation(path, i)["observed_at"])
                                        for i in result["evidence_ids"]):
        raise ValueError("Replay as_of precedes source evidence")
    receipt = store.save_observation(path, event)
    if store.get_observation(path, receipt["record_id"])["payload"]["results"] != result:
        raise ValueError("Replay readback mismatch")
    return {**result, "record_id": receipt["record_id"], "write_status": receipt["status"],
            "readback_verified": True}
