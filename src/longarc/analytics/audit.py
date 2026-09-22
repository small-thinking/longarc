"""Read-only inventory and quote-quality diagnostics, not strategy eligibility."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from longarc.analytics.history import _time
from longarc.analytics.metrics import calculate
from longarc.core.symbols import canonical_symbol
from longarc.storage import store

FIELDS = (
    "bid_u", "ask_u", "delta", "theta", "gamma", "iv", "underlying_price_u",
    "quote_at", "greeks_at", "underlying_at", "probability_otm", "probability_touch",
)
NY = ZoneInfo("America/New_York")


def data_audit(
    path: Path, *, symbol: str | None = None, start: str | None = None,
    end: str | None = None, expected_dates: list[str] | None = None,
    max_age_seconds: int = 300, limit: int = 10000,
) -> dict[str, Any]:
    """Count current revisions separately from physical records and repeated quotes.

    Date bounds use observation collection dates in New York, not inferred market
    sessions. Global inventory is deliberately labelled separately from the filtered
    quote/execution analysis. Fail on overflow rather than silently undercount.
    """
    if symbol is not None:
        canonical_symbol(symbol)
    first = date.fromisoformat(start) if start else None
    last = date.fromisoformat(end) if end else None
    if first and last and first > last:
        raise ValueError("start must be <= end")
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 100000:
        raise ValueError("limit must be between 1 and 100000")
    if expected_dates is not None and (not isinstance(expected_dates, list)
                                     or any(not isinstance(d, str) for d in expected_dates)):
        raise ValueError("expected_dates must be an array of ISO dates")
    expected = {date.fromisoformat(d) for d in expected_dates or []}
    expected = {d for d in expected if (not first or d >= first) and (not last or d <= last)}
    with store.connect(path, readonly=True) as db:
        db.execute("BEGIN")
        schema = store.check_schema(db)
        counts = {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                  for name in ("observations", "holding_lots", "holding_snapshots")}
        if counts["observations"] > limit:
            raise ValueError("Audit exceeds observation limit; increase --limit, never truncate")
        rows = [dict(r) for r in db.execute("SELECT * FROM observations ORDER BY observed_at")]
        integrity = [r[0] for r in db.execute("PRAGMA quick_check")]
        fk_errors = len(db.execute("PRAGMA foreign_key_check").fetchall())
    superseded = {r["supersedes_id"] for r in rows if r["supersedes_id"]}
    inventory = {
        "scope": "whole_database_unfiltered", "table_rows": counts,
        "superseded_observations": len(superseded),
        "current_observations": len(rows) - len(superseded),
        "by_kind": dict(Counter(r["kind"] for r in rows)),
        "by_mode": dict(Counter(r["mode"] for r in rows)),
        "by_quality": dict(Counter(r["quality"] for r in rows)),
        "first_observed_at": rows[0]["observed_at"] if rows else None,
        "last_observed_at": rows[-1]["observed_at"] if rows else None,
        "last_recorded_at": max((r["recorded_at"] for r in rows), default=None),
    }
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    executions: Counter[tuple[str, str, str]] = Counter()
    replay_status: Counter[tuple[str, str, str]] = Counter()
    malformed: list[dict[str, str]] = []
    malformed_events: list[dict[str, str]] = []
    selected_count = 0
    for row in rows:
        if row["record_id"] in superseded:
            continue
        day = _time(row["observed_at"]).astimezone(NY).date()
        if (first and day < first) or (last and day > last):
            continue
        selected_count += 1
        p = json.loads(row["payload_json"])
        inputs, result = p.get("inputs", {}), p.get("results", {})
        if inputs.get("format") == "option-chain-v1":
            ticker = row["scope"].removeprefix("options:")
            try:
                canonical_symbol(ticker)
                if inputs.get("symbol", ticker) != ticker:
                    raise ValueError("batch symbol conflicts with scope")
                if not isinstance(result.get("quotes", []), list):
                    raise ValueError("quotes must be an array")
            except ValueError as exc:
                malformed.append({"record_id": row["record_id"], "reason": str(exc)})
                continue
            if symbol is None or ticker == symbol:
                groups[ticker, row["mode"], row["source"]].append(
                    {"row": row, "payload": p, "date": day.isoformat()})
        elif inputs.get("format") == "short-call-execution-v1":
            event = inputs.get("execution")
            try:
                if not isinstance(event, dict) or not isinstance(event.get("contract"), dict):
                    raise ValueError("execution and contract must be objects")
                ticker = canonical_symbol(event["contract"].get("symbol"))
                if event.get("action") not in {"STO", "BTC", "EXPIRE", "ASSIGN"}:
                    raise ValueError("invalid execution action")
            except ValueError as exc:
                malformed_events.append({"record_id": row["record_id"], "reason": str(exc)})
                continue
            if symbol is None or ticker == symbol:
                executions[str(ticker), row["mode"], str(event.get("action"))] += 1
        elif row["scope"].endswith(":replays"):
            ticker = row["scope"].split(":")[1]
            if symbol is None or ticker == symbol:
                replay_status[ticker, row["mode"], str(result.get("status"))] += 1
    observed_symbols = {key[0] for key in groups}
    coverage_symbols = {symbol} if symbol is not None else observed_symbols
    observe_coverage = []
    for ticker in sorted(coverage_symbols):
        days = {b["date"] for (s, mode, _), bs in groups.items()
                if s == ticker and mode == "observe" for b in bs}
        observe_coverage.append({"symbol": ticker, "capture_dates": sorted(days),
                                 "missing_expected_dates": sorted(
                                     d.isoformat() for d in expected if d.isoformat() not in days)
                                 if expected_dates is not None else None})
    return {
        "format": "data-audit-v1", "status": "ok" if integrity == ["ok"]
        and not fk_errors else "error", "schema_version": schema,
        "integrity": integrity, "foreign_key_errors": fk_errors,
        "inventory": inventory,
        "selection": {"symbol": symbol, "start": start, "end": end,
                      "date_basis": "New York observation collection date",
                      "current_observations_in_date_range_all_symbols": selected_count,
                      "max_source_age_seconds_at_capture": max_age_seconds},
        "quote_groups": [_group(key, bs, expected if expected_dates is not None else None,
                                max_age_seconds) for key, bs in sorted(groups.items())],
        "observe_capture_coverage": observe_coverage,
        "execution_events": [{"symbol": s, "mode": m, "action": a, "count": n}
                             for (s, m, a), n in sorted(executions.items())],
        "replay_revision_counts": [{"symbol": s, "mode": m, "status": a, "count": n}
                                   for (s, m, a), n in sorted(replay_status.items())],
        "malformed_batches": malformed,
        "malformed_execution_records": malformed_events,
        "warnings": [
            "Global inventory includes calculations, imports and evidence; do not add quote rows.",
            "Quote groups isolate symbol, mode and source; counts are not independent trials.",
            "Identical values without valid source timestamps are only potential repeats.",
            "Replay quote prerequisites are necessary, not sufficient: calendar, contract, "
            "account and policy evidence are still required.",
            "Replay counts are stored revisions, not distinct or completed strategy trials.",
            "Missing expected dates are unknown unless an explicit session list is supplied.",
        ],
    }


def _group(
    key: tuple[str, str, str], batches: list[dict[str, Any]],
    expected: set[date] | None, max_age: int,
) -> dict[str, Any]:
    symbol, mode, source = key
    missing: Counter[str] = Counter()
    blockers: Counter[str] = Counter()
    contracts: set[str] = set()
    expiries: set[str] = set()
    market_dates: set[str] = set()
    known_fingerprints: set[str] = set()
    unstamped_values: set[str] = set()
    quote_count = descriptive = ready = duplicates = potential = 0
    malformed: list[dict[str, Any]] = []
    for batch in batches:
        p = batch["payload"]
        for index, q in enumerate(p["results"].get("quotes", [])):
            quote_count += 1
            try:
                contract, snap = q["contract"], q["snapshot"]
                if contract["symbol"] != symbol:
                    raise ValueError("contract symbol conflicts with batch")
                captured = _time(snap["captured_at"])
                if captured > _time(p["observed_at"]):
                    raise ValueError("capture is later than observation")
                metrics = calculate(contract, snap, as_of=snap["captured_at"])
                identity = store.canonical({k: contract.get(k) for k in (
                    "symbol", "option_type", "expiry_date", "strike_u", "multiplier",
                    "option_symbol")})
                reasons = []
                if metrics["metrics"]["midpoint_u"] is None:
                    reasons.append("invalid_or_missing_bid_ask")
                if snap.get("delta") is None:
                    reasons.append("missing_delta")
                if snap.get("underlying_price_u") in (None, 0):
                    reasons.append("missing_or_zero_underlying_price")
                if contract.get("multiplier") is None:
                    reasons.append("unknown_multiplier")
                if p["inputs"].get("closed_session"):
                    reasons.append("closed_session")
                if metrics["metrics"]["dte_calendar_days"] < 0:
                    reasons.append("expired_contract")
                source_times_valid = True
                for field in ("quote_at", "greeks_at", "underlying_at"):
                    stamp = snap.get(field)
                    if stamp is None:
                        reasons.append("missing_" + field)
                        if field != "underlying_at":
                            source_times_valid = False
                    else:
                        age = (captured - _time(stamp)).total_seconds()
                        if age < 0 or age > max_age:
                            reasons.append("future_or_stale_" + field)
                        if age < 0 and field != "underlying_at":
                            source_times_valid = False
                        if field == "quote_at" and age >= 0:
                            market_dates.add(_time(stamp).astimezone(NY).date().isoformat())
                for f in FIELDS:
                    missing[f] += snap.get(f) is None
                missing["multiplier"] += contract.get("multiplier") is None
                contracts.add(identity)
                expiries.add(contract["expiry_date"])
                descriptive += metrics["metrics"]["midpoint_u"] is not None
                blockers.update(reasons)
                ready += not reasons and mode == "observe"
                values = {k: v for k, v in snap.items() if k != "captured_at"}
                fingerprint = store.canonical([identity, values])
                if source_times_valid:
                    duplicates += fingerprint in known_fingerprints
                    known_fingerprints.add(fingerprint)
                else:
                    potential += fingerprint in unstamped_values
                    unstamped_values.add(fingerprint)
            except (KeyError, TypeError, ValueError) as exc:
                malformed.append({"record_id": batch["row"]["record_id"],
                                  "quote_index": index, "reason": str(exc)})
    dates = {batch["date"] for batch in batches}
    return {
        "symbol": symbol, "mode": mode, "source": source, "batch_count": len(batches),
        "quote_rows": quote_count, "contract_identities": len(contracts),
        "expiries": sorted(expiries), "capture_dates": sorted(dates),
        "source_quote_dates": sorted(market_dates),
        "partial_batches": sum(b["payload"]["results"].get("complete") is not True
                               for b in batches),
        "closed_session_batches": sum(bool(b["payload"]["inputs"].get("closed_session"))
                                      for b in batches),
        "missing_capture_dates": sorted(d.isoformat() for d in expected
                                        if d.isoformat() not in dates)
        if expected is not None else None,
        "field_missing": {f: {"count": missing[f], "denominator": quote_count - len(malformed),
                              "fraction": missing[f] / (quote_count - len(malformed))
                              if quote_count > len(malformed) else None}
                          for f in (*FIELDS, "multiplier")},
        "descriptive_price_rows": descriptive,
        "replay_quote_prerequisites_met_rows": ready,
        "replay_quote_blockers": dict(sorted(blockers.items())),
        "duplicate_source_snapshots": duplicates,
        "potential_repeats_with_unusable_source_times": potential,
        "malformed_quotes": malformed,
        "batch_evidence": [{"record_id": b["row"]["record_id"],
                            "observed_at": b["row"]["observed_at"],
                            "quote_count": len(b["payload"]["results"].get("quotes", []))}
                           for b in batches],
    }
