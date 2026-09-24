"""Deterministic advisory rules. Unknown facts are never a HOLD pass.

Freshness/position/dividend checks are caller attestations with evidence, not
inferred from a capture clock. No market predictions or broker actions.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from longarc.analytics.metrics import NY, _integer, _number, _time
from longarc.core import symbols
from longarc.storage import store


def _flag(facts: dict[str, Any], name: str) -> bool | None:
    value = facts.get(name)
    if value is not None and type(value) is not bool:
        raise ValueError(f"{name} must be boolean or null")
    return value


def _num(obj: dict[str, Any], name: str) -> Decimal | None:
    return _number(obj.get(name), name)


def _microseconds(duration: timedelta) -> int:
    return ((duration.days * 86_400 + duration.seconds) * 1_000_000
            + duration.microseconds)


def evaluate(request: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Policy uses policy_parameters and roll from the existing private contract.

    Request: as_of, facts, optional replacement; journal adds metadata separately.
    Monetary facts are integer microdollars. Explicit position_contracts=0 routes
    to entry screening; positive quantities route to existing-position rules.
    """
    now = _time(request["as_of"])
    p, roll = policy["policy_parameters"], policy["roll"]
    watch, defend = _num(p, "watch_delta"), _num(p, "defend_delta")
    profit = _num(p, "profit_capture_fraction")
    pace_min = _num(p, "profit_pace_min_capture_fraction")
    profit_operator = p.get("profit_capture_operator", ">=")
    if watch is None or defend is None or not 0 < watch < defend <= 1:
        raise ValueError("Require ordered watch/defend thresholds")
    if profit is None or not 0 < profit < 1:
        raise ValueError("Invalid profit threshold")
    if pace_min is not None and not 0 < pace_min < 1:
        raise ValueError("Invalid profit pace minimum")
    if profit_operator not in (">", ">="):
        raise ValueError("Invalid profit threshold operator")
    exit_days = _integer(p.get("latest_exit_dte"), "latest_exit_dte")
    if exit_days is None:
        raise ValueError("latest_exit_dte required")
    f = request["facts"]
    symbol = symbols.canonical_symbol(f.get("symbol"))
    if f.get("option_type") != "CALL":
        raise ValueError("Only CALL facts are supported")
    symbols.require_policy_symbol(symbol, policy)
    checks: dict[str, bool | None] = {}
    for key in ("position_verified", "coverage_verified", "orders_clear", "quote_usable",
                "greeks_usable", "underlying_usable", "dividend_window_clear", "market_open"):
        checks[key] = _flag(f, key)
    qty = _integer(f.get("position_contracts"), "position_contracts")
    reasons: list[str] = []
    metrics: dict[str, Any] = {}

    def output(action: str) -> dict[str, Any]:
        return {"symbol": symbol, "action": action, "reasons": reasons,
                "checks": checks, "metrics": metrics,
                "unknown_checks": [k for k, v in checks.items() if v is None],
                "execution": "human_only" if checks["market_open"] else "recheck_before_execution",
                "warnings": ["Advisory only; caller evidence and freshness are not independently "
                             "verified by this script", "No historical return forecast"]}

    if checks["position_verified"] is not True or qty is None:
        reasons.append("current_position_not_verified")
        return output("INSUFFICIENT_DATA")
    if qty == 0:
        entry = screen_entry(f, p, now.astimezone(NY).date())
        checks.update(entry)
        reasons.extend(k for k, v in entry.items() if v is not True)
        eligible = all(v is True for v in entry.values())
        return output("STO_CANDIDATE" if eligible else "NO_ENTRY")

    expiry = date.fromisoformat(f["expiry_date"]) if f.get("expiry_date") else None
    dte = (expiry - now.astimezone(NY).date()).days if expiry else None
    metrics["dte"] = dte
    delta = _num(f, "delta")
    if delta is not None and not 0 <= delta <= 1:
        raise ValueError("CALL delta must be 0..1")
    strike = _integer(f.get("strike_u"), "strike_u", 1)
    spot = _integer(f.get("underlying_price_u"), "underlying_price_u")
    if dte is not None and dte < 0:
        reasons.append("expired_contract_requires_settlement_reconciliation")
        return output("RECONCILE")
    checks["time_exit"] = dte <= exit_days if dte is not None else None
    checks["dividend_exit"] = (None if checks["dividend_window_clear"] is None
                               else not checks["dividend_window_clear"])
    checks["strike_exit"] = (spot >= strike if spot is not None and strike is not None
                             and checks["underlying_usable"] else None)
    checks["delta_exit"] = (delta >= defend if delta is not None
                            and checks["greeks_usable"] else None)
    for name in ("time_exit", "dividend_exit", "strike_exit", "delta_exit"):
        if checks[name] is True:
            reasons.append(name)
    if reasons:
        return output("BTC_RISK")

    ask = _integer(f.get("ask_u"), "ask_u")
    bid = _integer(f.get("bid_u"), "bid_u")
    opening = _integer(f.get("opening_premium_u"), "opening_premium_u", 1)
    multiplier = _integer(f.get("multiplier"), "multiplier", 1)
    opening_fees = _integer(f.get("allocated_opening_fees_u"), "allocated_opening_fees_u")
    closing_fees = _integer(f.get("estimated_closing_total_fees_u"), "closing_fees_u")
    valid_quote = (checks["quote_usable"] is True and ask is not None and bid is not None
                   and bid <= ask)
    checks["valid_quote"] = valid_quote
    capture = ((Decimal(opening) - ask) / opening
               if valid_quote and opening is not None and ask is not None else None)
    net = ((opening - ask) * qty * multiplier - opening_fees - closing_fees
           if capture is not None and opening is not None and ask is not None
           and multiplier is not None and opening_fees is not None
           and closing_fees is not None else None)
    metrics.update(gross_capture=None if capture is None else str(capture), net_close_pnl_u=net)
    if capture is None:
        threshold_reached = None
    elif profit_operator == ">":
        threshold_reached = capture > profit
    else:
        threshold_reached = capture >= profit
    if pace_min is not None:
        opened_at = _time(f["opening_executed_at"]) if f.get("opening_executed_at") else None
        # This is a comparison clock, not the broker's exercise deadline.
        expiry_close = (datetime.combine(expiry, time(16), NY) if expiry else None)
        elapsed_fraction = None
        if opened_at is not None and expiry_close is not None:
            if opened_at >= expiry_close or now < opened_at:
                raise ValueError("Opening time must precede observation and expiry close")
            elapsed_fraction = (Decimal(_microseconds(now - opened_at))
                                / Decimal(_microseconds(expiry_close - opened_at)))
        metrics["profit_pace_elapsed_fraction"] = (
            None if elapsed_fraction is None else str(elapsed_fraction))
        if capture is None:
            checks["profit_pace_reached"] = None
        elif capture <= pace_min:
            checks["profit_pace_reached"] = False
        else:
            checks["profit_pace_reached"] = (
                None if elapsed_fraction is None else capture > elapsed_fraction)
        if checks["profit_pace_reached"] is True:
            threshold_reached = True
        elif threshold_reached is False and checks["profit_pace_reached"] is None:
            threshold_reached = None
    if threshold_reached is None:
        checks["profit_exit"] = None
    elif threshold_reached is False:
        checks["profit_exit"] = False
    else:
        checks["profit_exit"] = net > 0 if net is not None else None
    if checks["profit_exit"] is True:
        reasons.append("profit_pace_and_positive_fee_adjusted_pnl"
                       if pace_min is not None and checks["profit_pace_reached"] is True
                       else "profit_threshold_and_positive_fee_adjusted_pnl")
        return output("BTC_PROFIT")
    required = ("coverage_verified", "orders_clear", "valid_quote", "greeks_usable",
                "underlying_usable", "dividend_window_clear")
    if (any(checks[k] is not True for k in required)
            or any(checks[k] is None for k in
                   ("time_exit", "strike_exit", "delta_exit", "profit_exit"))):
        reasons.append("critical_checks_missing_or_failed")
        return output("INSUFFICIENT_DATA")
    if delta is not None and delta >= watch:
        replacement = request.get("replacement")
        if replacement is not None:
            entry = screen_entry(replacement, p, now.astimezone(NY).date())
            if not replacement.get("expiry_date") or expiry is None:
                checks.update({"roll_" + k: v for k, v in entry.items()})
                reasons.append("replacement_expiry_unknown")
                return output("WATCH")
            new_expiry = date.fromisoformat(replacement["expiry_date"])
            new_strike = _integer(replacement.get("strike_u"), "replacement_strike", 1)
            count = _integer(f.get("episode_roll_count"), "episode_roll_count")
            new_bid = _integer(replacement.get("bid_u"), "replacement_bid_u")
            new_fees = _integer(replacement.get("opening_total_fees_u"), "new_fees_u")
            new_multiplier = _integer(replacement.get("multiplier"), "new_multiplier", 1)
            credit = ((new_bid - ask) * qty * multiplier - closing_fees - new_fees
                      if new_bid is not None and ask is not None and multiplier is not None
                      and closing_fees is not None and new_fees is not None else None)
            metrics["net_roll_cashflow_u"] = credit
            entry.update(
                higher_strike=new_strike > strike if new_strike and strike else None,
                later_expiry=new_expiry > expiry,
                extension_within_limit=(new_expiry - expiry).days
                <= roll["max_extra_calendar_days"],
                roll_count_within_limit=count < roll["max_rolls_per_episode"]
                if count is not None else None,
                nonnegative_net_credit=credit >= 0 if credit is not None else None,
                matched_roll_quantity=replacement.get("contracts") == qty,
                matched_multiplier=new_multiplier == multiplier and multiplier is not None,
                same_underlying=replacement.get("symbol") == symbol,
                call_only=replacement.get("option_type") == "CALL",
            )
            checks.update({"roll_" + k: v for k, v in entry.items()})
            if all(v is True for v in entry.values()):
                reasons.append("watch_range_and_replacement_screen_passed")
                return output("ROLL_CANDIDATE")
        reasons.append("watch_range_without_qualified_roll")
        return output("WATCH")
    reasons.append("all_required_checks_known_no_exit_or_watch_trigger")
    return output("HOLD")


def screen_entry(f: dict[str, Any], p: dict[str, Any], today: date) -> dict[str, bool | None]:
    """Screen one explicitly sized candidate, without ranking or maximum sizing."""
    delta = _num(f, "delta")
    bid = _integer(f.get("bid_u"), "bid_u")
    ask = _integer(f.get("ask_u"), "ask_u")
    strike = _integer(f.get("strike_u"), "strike_u", 1)
    spot = _integer(f.get("underlying_price_u"), "underlying_price_u")
    oi = _integer(f.get("open_interest"), "open_interest")
    contracts = _integer(f.get("contracts"), "contracts", 1)
    multiplier = _integer(f.get("multiplier"), "multiplier", 1)
    expiry = f.get("expiry_date")
    dte = None if expiry is None else (date.fromisoformat(expiry) - today).days
    spread = (Decimal(2) * (ask - bid) / (ask + bid)
              if ask is not None and bid is not None and ask >= bid > 0 else None)
    dl, dh = map(Decimal, map(str, p["entry_delta_range"]))
    tl, th = p["entry_dte_range"]
    return {
        **{k: _flag(f, k) for k in (
            "quote_usable", "greeks_usable", "underlying_usable", "dividend_window_clear",
            "standard_contract", "coverage_verified", "orders_clear", "sizing_approved",
            "reentry_cooldown_clear")},
        "explicit_size": contracts is not None and multiplier is not None,
        "entry_delta": dl <= delta <= dh if delta is not None else None,
        "entry_dte": tl <= dte <= th if dte is not None else None,
        "otm": spot < strike if spot is not None and strike is not None else None,
        "liquidity_spread": spread <= Decimal(str(p["entry_max_spread_fraction_of_midpoint"]))
        if spread is not None else None,
        "liquidity_oi": oi >= p["entry_min_open_interest"] if oi is not None else None,
    }


def decide_and_log(path: Path, request: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    try:
        result = evaluate(request, policy)
    except (KeyError, TypeError, ValueError) as exc:
        result = {"action": "ERROR", "status": "error", "error": str(exc)}
    if not request.get("evidence_ids"):
        raise ValueError("Decision requires source evidence IDs")
    try:
        symbol = symbols.canonical_symbol(request.get("facts", {}).get("symbol"))
    except (ValueError, AttributeError):
        symbol = "invalid"  # Invalid identities must never be attributed to another asset.
    event = {
        "idempotency_key": request["idempotency_key"],
        "scope": f"options:{symbol}:decisions",
        "mode": request["mode"], "kind": "error" if result["action"] == "ERROR" else
        "no_action" if result["action"] in
        {"HOLD", "WATCH", "NO_ENTRY", "INSUFFICIENT_DATA", "RECONCILE"} else "calculation",
        "observed_at": request["as_of"], "source": request["source"],
        "quality": "synthetic" if request["mode"] == "shadow" else "unverified",
        "code_version": "decisions-v1:" + hashlib.sha256(
            Path(__file__).read_bytes() + Path(symbols.__file__).read_bytes()).hexdigest(),
        "inputs": {"request": request, "policy": policy}, "results": result,
        "policy_hash": store.digest(policy), "evidence_ids": request["evidence_ids"],
    }
    receipt = store.save_observation(path, event)
    saved = store.get_observation(path, receipt["record_id"])
    if saved["payload"]["results"] != result:
        raise ValueError("Decision readback mismatch")
    return {**result, "record_id": receipt["record_id"], "write_status": receipt["status"],
            "readback_verified": True}
