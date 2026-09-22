"""Read-only, as-of short-call lot reporting with separate provider valuations."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from longarc.analytics.costs import calculate_costs
from longarc.analytics.executions import FORMAT, _performance, _scope
from longarc.analytics.history import _number, _time
from longarc.core.symbols import canonical_symbol
from longarc.storage import store
from longarc.storage.holdings import integer, text, timestamp


def _read(path: Path, account: str, mode: str, cutoff: datetime,
          symbol: str | None, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    quote_mode = "observe" if mode == "manual" else "shadow"
    quote_scope = "scope LIKE 'options:%'" if symbol is None else "scope=?"
    params: list[Any] = [_scope(account), mode]
    if symbol is not None:
        params.append("options:" + symbol)
    params.extend([quote_mode, limit + 1])
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        rows = db.execute(
            "SELECT record_id, recorded_at, observed_at, supersedes_id, payload_json "
            "FROM observations WHERE (scope=? AND mode=?) OR (" + quote_scope +
            " AND mode=?) ORDER BY observed_at, recorded_at, record_id LIMIT ?", params,
        ).fetchall()
    if len(rows) > limit:
        raise ValueError("Selected observation row limit exceeded; increase limit")
    eligible = [r for r in rows if _time(r["recorded_at"]) <= cutoff
                and _time(r["observed_at"]) <= cutoff]
    superseded = {r["supersedes_id"] for r in eligible if r["supersedes_id"]}
    events, quotes = [], []
    for row in eligible:
        if row["record_id"] in superseded:
            continue
        p = json.loads(row["payload_json"])
        if p["inputs"].get("format") == FORMAT:
            event = p["inputs"]["execution"]
            if (_time(event["executed_at"]) <= cutoff
                    and (symbol is None or event["contract"]["symbol"] == symbol)):
                events.append(event)
        elif p["inputs"].get("format") == "option-chain-v1":
            batch_quotes = list(p["results"].get("quotes", []) or [])
            for missing in p["results"].get("missing_contracts", []) or []:
                if isinstance(missing, dict):
                    batch_quotes.append({
                        "contract": {"symbol": p["scope"].split(":", 1)[1],
                                     "option_type": "CALL", **missing},
                        "snapshot": {"captured_at": row["observed_at"]},
                        "warnings": ["contract_missing_from_requested_capture"],
                    })
            for q in batch_quotes:
                if symbol is not None and q["contract"].get("symbol") != symbol:
                    continue
                capture_valid = True
                observed = _time(row["observed_at"])
                try:
                    captured = _time(q["snapshot"]["captured_at"])
                    if captured > observed:
                        capture_valid = False
                        captured = observed
                        q = {**q, "warnings": [*q.get("warnings", []),
                                               "capture_postdates_observation"]}
                except (KeyError, ValueError, TypeError):
                    # Keep a known observation-time barrier instead of silently
                    # falling back to an older, favorable quote.
                    captured = _time(row["observed_at"])
                    capture_valid = False
                if captured <= cutoff:
                    quotes.append({**q, "source": p["source"],
                                   "record_id": row["record_id"],
                                   "recorded_at": row["recorded_at"],
                                   "captured_at": timestamp(captured.isoformat()),
                                   "capture_valid": capture_valid,
                                   "closed_session": p["inputs"].get("closed_session")})
    return events, quotes


def _point(q: dict[str, Any], contract: dict[str, Any], cutoff: datetime,
           max_age: int) -> dict[str, Any]:
    snap = q["snapshot"]
    captured = _time(q["captured_at"])
    warnings = list(q.get("warnings", []))
    age = (cutoff - captured).total_seconds()
    if not q["capture_valid"]:
        warnings.append("missing_or_invalid_capture_timestamp")
    freshness = {}
    for field in ("quote_at", "greeks_at", "underlying_at"):
        value = snap.get(field)
        if value is None:
            status = "unknown"
        else:
            try:
                stamp = _time(value)
                status = ("invalid_future" if stamp > captured else
                          "stale" if (cutoff - stamp).total_seconds() > max_age else "fresh")
            except (TypeError, ValueError):
                status = "invalid"
        freshness[field] = status
        if status != "fresh":
            warnings.append(f"{field}:{status}")
    quoted_multiplier = q["contract"].get("multiplier")
    contract_match = (None if quoted_multiplier is None else
                      quoted_multiplier == contract["multiplier"])
    if contract_match is None:
        warnings.append("unverified_quote_multiplier_conditional_same_deliverable")
    elif not contract_match:
        warnings.append("mismatched_multiplier")
    bid, ask = snap.get("bid_u"), snap.get("ask_u")
    valid = (type(bid) is int and type(ask) is int and 0 <= bid <= ask < 2**63
             and contract_match is not False and q["capture_valid"]
             and not freshness["quote_at"].startswith("invalid"))
    if not valid:
        warnings.append("invalid_or_missing_bid_ask_or_contract")
    delta, theta = _number(snap.get("delta")), _number(snap.get("theta"))
    if delta is not None and not 0 <= delta <= 1:
        delta = None
    if freshness["greeks_at"].startswith("invalid") or not q["capture_valid"]:
        delta = theta = None
    spot = _number(snap.get("underlying_price_u"), nonnegative=True)
    if freshness["underlying_at"].startswith("invalid") or not q["capture_valid"]:
        spot = None
    return {"record_id": q["record_id"],
            "captured_at": q["captured_at"] if q["capture_valid"] else None,
            "observation_order_at": q["captured_at"],
            "recorded_at": q["recorded_at"], "contract": q["contract"],
            "capture_age_seconds": age, "source_timestamps": {
                f: snap.get(f) for f in freshness}, "freshness": freshness,
            "valuation_freshness": ("invalid" if not valid else "stale" if age > max_age
                                    or freshness["quote_at"] == "stale" else
                                    "unknown" if freshness["quote_at"] == "unknown" else "fresh"),
            "bid_u": bid, "ask_u": ask, "price_valid": valid,
            "contract_match_verified": contract_match,
            "delta": delta, "theta": theta, "underlying_price_u": spot,
            "strike_distance_fraction": (contract["strike_u"] / spot - 1)
            if spot else None, "closed_session": q.get("closed_session"), "warnings": warnings}


def _scenario(latest: dict[str, Any], opening: dict[str, Any], quantity: int,
              opening_fees: int | None, fees: dict[str, Any]) -> dict[str, Any] | None:
    if not latest["price_valid"]:
        return None
    ask = latest["ask_u"]
    units = quantity * opening["contract"]["multiplier"]
    gross = (opening["price_u"] - ask) * units
    result: dict[str, Any] = {
        "basis": "ask", "status": "partial",
        "contract_match_verified": latest["contract_match_verified"],
        "multiplier_used": opening["contract"]["multiplier"],
        "assumptions": (["Quoted option has the same deliverable and multiplier as recorded lot"]
                        if latest["contract_match_verified"] is None else []),
        "valuation_freshness":
        latest["valuation_freshness"], "buyback_premium_u": str(ask * units),
        "gross_option_pnl_u": str(gross), "net_option_pnl_u": None,
        "known_cost_net_option_pnl_u": None,
        "estimated_closing_fees_u": None, "buyback_total_u": None,
        "fees_complete": False, "warnings": ["scenario_not_realized_profit"],
        "premium_capture_fraction": (opening["price_u"] - ask) / opening["price_u"]
        if opening["price_u"] else None,
    }
    if not fees:
        result["warnings"].append("unknown_closing_fee_schedule")
        return result
    costs = calculate_costs(
        fees, contracts=quantity, multiplier=opening["contract"]["multiplier"],
        opening_premium_u=opening["price_u"], closing_ask_u=ask,
        closing_bid_u=latest["bid_u"], opening_actual_fees_u=opening_fees,
        closing_extra_fees_u=fees.get("closing_extra_fees_u"),
    )
    metrics = costs["metrics"]
    result["warnings"] = costs["warnings"]
    closing_known = fees.get("closing_extra_fees_u") is not None
    if closing_known:
        result["estimated_closing_fees_u"] = metrics["closing_fees_u"]
        result["buyback_total_u"] = str(Decimal(ask * units) + Decimal(metrics["closing_fees_u"]))
    if opening_fees is None:
        result["warnings"].append("unknown_actual_opening_fees")
    else:
        result["fees_complete"] = costs["fees_complete"]
        result["net_option_pnl_u"] = metrics["net_option_pnl_u"]
        result["known_cost_net_option_pnl_u"] = metrics["known_cost_net_option_pnl_u"]
        result["status"] = costs["status"]
    return result


def position_report(path: Path, *, account: str, as_of: str, fees: dict[str, Any],
                    symbol: str | None = None, mode: str = "manual",
                    max_age_seconds: int = 300, limit: int = 10000) -> dict[str, Any]:
    """Value remaining execution lots with explicit as-of evidence and fee uncertainty.

    Manual lots join observe chains; shadow lots join shadow chains. Both record
    time and event/capture time must precede as_of. Providers remain separate.
    max_age_seconds labels evidence only; it is not a trading-policy threshold.
    """
    text(account, "account")
    cutoff = _time(as_of)
    integer(max_age_seconds, "max_age_seconds")
    integer(limit, "limit", 1)
    if limit > 100000:
        raise ValueError("limit must be <= 100000")
    if mode not in {"manual", "shadow"}:
        raise ValueError("mode must be manual or shadow")
    if symbol is not None:
        canonical_symbol(symbol)
    if not isinstance(fees, dict):
        raise ValueError("fees must be an object")
    if fees:
        # Validate supplied schedules even when there are no quotes or open lots.
        calculate_costs(fees, contracts=1, multiplier=100, opening_premium_u=0,
                        closing_ask_u=0, closing_extra_fees_u=fees.get("closing_extra_fees_u"))
        if date.fromisoformat(fees["as_of"]) > cutoff.date():
            raise ValueError("fee schedule postdates as_of")
    events, quotes = _read(path, account, mode, cutoff, symbol, limit)
    performance = _performance(events)
    openings = {e["external_execution_id"]: e for e in events if e["action"] == "STO"}
    lots = []
    for lot in performance["open_lots"]:
        opening = openings[lot["opening_execution_id"]]
        allocated = sum((Decimal(c["allocated_opening_fees_u"])
                         for c in performance["closed_events"]
                         if c["opening_execution_id"] == lot["opening_execution_id"]
                         and c["allocated_opening_fees_u"] is not None), Decimal(0))
        remaining_fees = (int(Decimal(opening["fees_u"]) - allocated)
                          if opening["fees_u"] is not None else None)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for q in quotes:
            if (all(q["contract"].get(k) == lot["contract"][k]
                    for k in ("symbol", "option_type", "expiry_date", "strike_u"))
                    and _time(q["captured_at"]) >= _time(opening["executed_at"])):
                grouped[q["source"]].append(q)
        series = []
        for source, candidates in sorted(grouped.items()):
            candidates.sort(key=lambda q: (q["captured_at"], q["recorded_at"], q["record_id"]))
            points = [_point(q, lot["contract"], cutoff, max_age_seconds) for q in candidates]
            latest = points[-1]
            simultaneous = [q for q in candidates
                            if q["captured_at"] == candidates[-1]["captured_at"]]
            option_symbols = {q["contract"].get("option_symbol") for q in candidates
                              if q["contract"].get("option_symbol")}
            if len(option_symbols) > 1:
                latest["price_valid"] = False
                latest["valuation_freshness"] = "invalid"
                latest["warnings"].append("ambiguous_option_symbols_for_execution_contract")
            if len({store.canonical({"contract": q["contract"], "snapshot": q["snapshot"]})
                    for q in simultaneous}) > 1:
                latest["price_valid"] = False
                latest["valuation_freshness"] = "invalid"
                latest["warnings"].append("ambiguous_simultaneous_contract_or_quote")
            series.append({"source": source, "latest": latest, "history": points,
                           "close_scenario": _scenario(latest, opening, lot["remaining_quantity"],
                                                       remaining_fees, fees)})
        warnings = []
        if not series:
            warnings.append("no_matching_quote_since_opening")
        if len(series) > 1:
            warnings.append("multiple_sources_valued_separately_no_combined_mark")
        lots.append({**lot, "opened_at": opening["executed_at"],
                     "opening_price_u": opening["price_u"],
                     "remaining_opening_fees_u": remaining_fees,
                     "quote_series": series, "warnings": warnings})
    return {"account": account, "mode": mode, "quote_mode":
            "observe" if mode == "manual" else "shadow", "symbol": symbol,
            "as_of": timestamp(as_of), "max_age_seconds": max_age_seconds, "limit": limit,
            "fee_schedule": fees, "open_lots": lots,
            "realized": {k: performance[k] for k in (
                "execution_count", "realized_gross_option_pnl_u", "realized_net_option_pnl_u",
                "fees_complete", "closed_events")},
            "limitations": [
                "As-of includes only evidence recorded and captured/executed by the cutoff",
                "Sparse saved observations only; no interpolation or broker reconciliation",
                "Ask close scenarios are historical estimates, not realized profit or firm fills",
                "Unknown quote multipliers produce conditional scenarios using the recorded lot; "
                "same deliverable is an assumption, not verified contract identity",
                "Missing source timestamps leave market freshness unknown",
                "Premium capture excludes costs and is not profit or a strategy return",
                "Known-cost subtotals exclude unknown fees; they are not exact net profit",
                "No stock P&L, taxes, portfolio valuation, or trading recommendation",
            ]}


def render_markdown(report: dict[str, Any]) -> str:
    def money(value: Any) -> str:
        if value is None or isinstance(value, bool):
            return "unknown"
        return f"${Decimal(str(value)) / 1000000:.4f}"

    lines = [f"# Position report — {report['as_of']}", "",
             f"Execution mode: {report['mode']}; quote mode: {report['quote_mode']}.",
             "Recorded all-time realized net option P&L subtotal through as-of: "
             f"{money(report['realized']['realized_net_option_pnl_u'])}. "
             "Complete account realized option P&L is unknown; "
             "an empty ledger does not establish zero income.",
             ""]
    for lot in report["open_lots"]:
        c = lot["contract"]
        lines.extend([f"## {c['symbol']} {c['expiry_date']} {money(c['strike_u'])} CALL", "",
                      f"Opened {lot['opened_at']}; remaining: {lot['remaining_quantity']}; "
                      f"opening premium: {money(lot['opening_price_u'])}; "
                      f"remaining opening fees: {money(lot['remaining_opening_fees_u'])}."])
        for series in lot["quote_series"]:
            p, scenario = series["latest"], series["close_scenario"]
            lines.extend(["", f"Source: {series['source']}; captured: {p['captured_at']}; "
                          f"freshness: {p['valuation_freshness']}; "
                          f"saved history points: {len(series['history'])}.",
                          f"Bid / ask: {money(p['bid_u'])} / {money(p['ask_u'])}; "
                          f"Delta: {p['delta']}; Theta: {p['theta']}; "
                          f"spot: {money(p['underlying_price_u'])}; "
                          f"strike distance fraction: {p['strike_distance_fraction']}."])
            if scenario:
                lines.append(f"Ask buyback premium: {money(scenario['buyback_premium_u'])}; "
                             f"gross close scenario: {money(scenario['gross_option_pnl_u'])}; "
                             f"net close scenario: {money(scenario['net_option_pnl_u'])}.")
                subtotal_label = ("known-cost subtotal; incomplete costs" if
                                  not scenario["fees_complete"] else "known-cost subtotal")
                lines.append(f"Close scenario {subtotal_label}: "
                             f"{money(scenario['known_cost_net_option_pnl_u'])}.")
                lines.append(f"Estimated closing fees: "
                             f"{money(scenario['estimated_closing_fees_u'])}; "
                             f"total buyback: {money(scenario['buyback_total_u'])}; "
                             f"premium capture fraction (before costs): "
                             f"{scenario['premium_capture_fraction']}.")
                lines.append(f"Contract match verified: {scenario['contract_match_verified']}; "
                             f"multiplier used: {scenario['multiplier_used']}.")
                lines.extend(f"- Assumption: {w}" for w in scenario["assumptions"])
                lines.extend(f"- {w}" for w in scenario["warnings"])
            lines.extend(f"- {w}" for w in p["warnings"])
        lines.extend(f"- {w}" for w in lot["warnings"])
        lines.append("")
    lines.extend(["", *[f"- {w}" for w in report["limitations"]]])
    return "\n".join(lines) + "\n"
