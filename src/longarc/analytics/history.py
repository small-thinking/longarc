"""Descriptive, sparse option histories using collection time as the analysis clock."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from longarc.storage import store


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result.astimezone(UTC)


def _number(value: Any, *, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or (nonnegative and value < 0):
        return None
    return float(value)


def _summary(values: list[float]) -> dict[str, Any]:
    return {"count": len(values), "median": median(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def _bucket(value: float | None, cuts: tuple[float, ...]) -> str:
    if value is None:
        return "missing"
    lower = "-inf"
    for upper in cuts:
        if value < upper:
            return f"[{lower},{upper})"
        lower = str(upper)
    return f"[{lower},inf)"


def _sessions(lo: date, hi: date, expected: set[date] | None) -> set[date]:
    if expected is not None:
        return {d for d in expected if lo <= d <= hi}
    return {lo + timedelta(days=i) for i in range((hi - lo).days + 1)
            if (lo + timedelta(days=i)).weekday() < 5}


def build_report(
    path: Path, symbol: str, *, mode: str = "observe", start: str | None = None,
    end: str | None = None, source: str | None = None, limit: int = 10000,
    expected_dates: list[str] | None = None,
) -> dict[str, Any]:
    """Read new chain batches only; date bounds are inclusive New York session dates.

    The row limit is explicit and overflow is reported. Different sources and modes
    never share a series or statistics. No interpolation or inferred intraday events.
    """
    if mode not in {"observe", "shadow", "manual"} or not 1 <= limit <= 100000:
        raise ValueError("Invalid mode or limit (1..100000)")
    first = date.fromisoformat(start) if start else None
    last = date.fromisoformat(end) if end else None
    if first and last and first > last:
        raise ValueError("start must be <= end")
    expected = ({date.fromisoformat(d) for d in expected_dates}
                if expected_dates is not None else None)
    conditions = ["o.scope=?", "o.mode=?", "o.kind IN ('observation','error')",
                  "NOT EXISTS (SELECT 1 FROM observations n WHERE n.supersedes_id=o.record_id)"]
    params: list[Any] = ["options:" + symbol.upper(), mode]
    if source is not None:
        conditions.append("o.source=?")
        params.append(source)
    # Broad UTC bounds retain every New York date; exact session filtering follows.
    if first:
        conditions.append("o.observed_at>=?")
        params.append(first.isoformat())
    if last:
        conditions.append("o.observed_at<?")
        params.append((last + timedelta(days=2)).isoformat())
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        rows = db.execute(
            "SELECT o.record_id,o.payload_json FROM observations o WHERE "
            + " AND ".join(conditions) + " ORDER BY o.observed_at,o.recorded_at LIMIT ?",
            [*params, limit + 1],
        ).fetchall()
    truncated = len(rows) > limit
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    batch_dates: dict[str, set[date]] = defaultdict(set)
    successful_dates: dict[str, set[date]] = defaultdict(set)
    warnings: list[str] = []
    batches: list[dict[str, Any]] = []
    errors = 0
    invalid = 0
    zone = ZoneInfo("America/New_York")
    for row in rows[:limit]:
        payload = json.loads(row["payload_json"])
        if payload["inputs"].get("format") != "option-chain-v1":
            continue
        try:
            batch_day = _time(payload["observed_at"]).astimezone(zone).date()
        except ValueError:
            invalid += 1
            continue
        if (first and batch_day < first) or (last and batch_day > last):
            continue
        src = payload["source"]
        batch_dates[src].add(batch_day)
        result = payload["results"]
        batches.append({"record_id": row["record_id"], "source": src,
                        "date": batch_day.isoformat(), "status": result.get("status"),
                        "complete": result.get("complete") is True,
                        "quote_count": len(result.get("quotes") or []),
                        "missing_expiries": result.get("missing_expiries", []),
                        "missing_contracts": result.get("missing_contracts", []),
                        "warnings": result.get("warnings", [])})
        if result.get("status") == "error":
            errors += 1
        for quote in result.get("quotes", []) or []:
            try:
                contract, snap = quote["contract"], quote["snapshot"]
                if contract["symbol"] != symbol.upper() or contract["option_type"] != "CALL":
                    continue
                expiry = date.fromisoformat(contract["expiry_date"])
                strike, multiplier = contract["strike_u"], contract.get("multiplier")
                if type(strike) is not int or strike <= 0:
                    raise ValueError("Invalid strike")
                if multiplier is not None and (type(multiplier) is not int or multiplier <= 0):
                    raise ValueError("Invalid multiplier")
                captured = _time(snap["captured_at"])
                day = captured.astimezone(zone).date()
                if (first and day < first) or (last and day > last):
                    continue
                identity = {k: contract.get(k) for k in (
                    "symbol", "option_type", "expiry_date", "strike_u", "multiplier",
                    "option_symbol")}
                key = store.canonical({"source": src, "contract": identity})
                bid = _number(snap.get("bid_u"), nonnegative=True)
                ask = _number(snap.get("ask_u"), nonnegative=True)
                source_time_valid = {}
                for field in ("quote_at", "greeks_at"):
                    try:
                        source_time_valid[field] = (snap.get(field) is None or
                                                    _time(snap[field]) <= captured)
                    except ValueError:
                        source_time_valid[field] = False
                valid_price = (bid is not None and ask is not None and bid <= ask
                               and source_time_valid["quote_at"])
                delta = _number(snap.get("delta"))
                if (not source_time_valid["greeks_at"] or
                        (delta is not None and not 0 <= delta <= 1)):
                    delta = None
                point = {"record_id": row["record_id"], "contract": identity, "source": src,
                         "captured_at": captured.isoformat(), "date": day.isoformat(),
                         "closed_session": payload["inputs"].get("closed_session"),
                         "dte": (expiry - day).days, "snapshot": snap,
                         "mid_u": (bid + ask) / 2 if bid is not None and ask is not None
                         and valid_price else None,
                         "delta": delta, "price_valid": valid_price,
                         "source_time_valid": source_time_valid,
                         "warnings": list(quote.get("warnings", []))}
                if not valid_price:
                    point["warnings"].append("Missing, invalid, or crossed bid/ask")
                for field, valid in source_time_valid.items():
                    if not valid:
                        point["warnings"].append(f"Invalid or future {field}; metric excluded")
                groups[key].append(point)
                if valid_price:
                    successful_dates[src].add(day)
            except (KeyError, TypeError, ValueError):
                invalid += 1
    series = []
    pooled: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    duplicates = 0
    for key, points in sorted(groups.items()):
        points.sort(key=lambda p: p["captured_at"])
        unique = []
        seen = set()
        previous_values = None
        for point in points:
            snap = point["snapshot"]
            values = {k: v for k, v in snap.items() if k != "captured_at"}
            fingerprint = store.canonical(values)
            # Only collapse provably repeated source-stamped values; retain unknowns.
            stamped = (snap.get("quote_at") is not None and snap.get("greeks_at") is not None
                       and all(point["source_time_valid"].values()))
            if stamped and fingerprint in seen:
                duplicates += 1
                continue
            if stamped:
                seen.add(fingerprint)
            point["repeated_values"] = fingerprint == previous_values
            point["source_time_missing"] = any(
                snap.get(field) is None for field in ("quote_at", "greeks_at"))
            previous_values = fingerprint
            unique.append(point)
        changes = []
        for previous, current in zip(unique, unique[1:]):
            # Collection time is not a market clock for a closed-session endpoint.
            # Keep it for audit, including against older untagged observations.
            if previous.get("closed_session") or current.get("closed_session"):
                continue
            elapsed = _time(current["captured_at"]) - _time(previous["captured_at"])
            hours = elapsed.total_seconds() / 3600
            if hours <= 0:
                continue
            a, b = previous["mid_u"], current["mid_u"]
            da, db_delta = previous["delta"], current["delta"]
            change = {"from": previous["captured_at"], "to": current["captured_at"],
                      "elapsed_hours": hours, "date": current["date"], "contract_key": key,
                      "mid_change_u": b - a if a is not None and b is not None else None,
                      "mid_change_fraction": b / a - 1 if a and b is not None else None,
                      "delta_change": db_delta - da if da is not None and db_delta is not None
                      else None, "repeated_values": current["repeated_values"]}
            changes.append(change)
            bucket = (current["source"], _bucket(previous["dte"], (7, 21, 35, 60)),
                      _bucket(da, (0.05, 0.1, 0.2, 0.3, 0.5)),
                      _bucket(hours, (12, 36, 84, 168)))
            pooled[bucket].append(change)
        observed_days = {date.fromisoformat(p["date"]) for p in points}
        lo, hi = min(observed_days), max(observed_days)
        sessions = _sessions(lo, hi, expected)
        series.append({"missing_dates": sorted(d.isoformat() for d in sessions - observed_days),
                       "contract": unique[0]["contract"], "source": unique[0]["source"],
                       "points": unique, "changes": changes})
    statistics = []
    for bucket, changes in sorted(pooled.items()):
        statistics.append({
            **dict(zip(("source", "initial_dte_bucket", "initial_delta_bucket",
                        "elapsed_hours_bucket"), bucket)),
            "interval_count": len(changes),
            "distinct_contracts": len({c["contract_key"] for c in changes}),
            "observation_dates": sorted({c["date"] for c in changes}),
            "distinct_observation_dates": len({c["date"] for c in changes}),
            "repeated_values_count": sum(c["repeated_values"] for c in changes),
            **{field: _summary([c[field] for c in changes if c[field] is not None])
               for field in ("mid_change_u", "mid_change_fraction", "delta_change")},
        })
    coverage = []
    if source is not None and source not in batch_dates and first and last:
        batch_dates[source] = set()
    for src, days in sorted(batch_dates.items()):
        lo = first or min(days)
        hi = last or max(days)
        calendar = _sessions(lo, hi, expected)
        coverage.append({"source": src, "batch_dates": sorted(d.isoformat() for d in days),
                         "missing_batch_dates": sorted(d.isoformat() for d in calendar - days),
                         "dates_without_complete_batches": sorted(
                             d.isoformat() for d in calendar - {
                                 date.fromisoformat(b["date"]) for b in batches
                                 if b["source"] == src and b["complete"]}),
                         "dates_without_valid_quotes": sorted(
                             d.isoformat() for d in calendar - successful_dates[src])})
    if truncated:
        warnings.append("Row limit reached: history and statistics are incomplete; narrow dates")
    if invalid:
        warnings.append(f"Skipped {invalid} malformed records or quotes")
    if not groups:
        warnings.append("No contract samples in the selected range; no trend can be inferred")
    warnings.extend([
        "Collection time is the analysis clock; missing source timestamps remain unknown",
        "Descriptive pooled intervals are correlated, not independent trials or expected returns",
        "No interpolation or inferred intraday threshold crossings; midpoint is not an execution",
        "Gap calendar: supplied sessions" if expected is not None else
        "Gap calendar: weekdays heuristic, not an exchange holiday calendar",
    ])
    return {"symbol": symbol.upper(), "mode": mode, "start": start, "end": end,
            "analysis_clock": "captured_at", "rows_read": min(len(rows), limit),
            "truncated": truncated, "duplicate_source_snapshots": duplicates,
            "error_batches": errors, "invalid_items": invalid, "series": series,
            "batches": batches, "incomplete_batches": sum(not b["complete"] for b in batches),
            "cross_contract_statistics": statistics, "coverage": coverage, "warnings": warnings}


def _display(value: Any, *, dollars: bool = False) -> str:
    if value is None:
        return "missing"
    return f"${value / 1_000_000:,.4f}" if dollars else f"{value:.4f}"


def render_markdown(report: dict[str, Any]) -> str:
    """Compact human report; full points remain available in JSON."""
    lines = [f"# {report['symbol']} option history ({report['mode']})", "",
             f"Series: {len(report['series'])}; rows: {report['rows_read']}; "
             f"error batches: {report['error_batches']}; truncated: {report['truncated']}; "
             f"incomplete batches: {report['incomplete_batches']}", "",
             "Prices and midpoint changes below are dollars per underlying share.", "",
             "| Source | Expiry | Strike | Samples | Latest midpoint | Latest delta |",
             "|---|---|---:|---:|---:|---:|"]
    for item in report["series"]:
        c, latest = item["contract"], item["points"][-1]
        lines.append(f"| {item['source']} | {c['expiry_date']} | "
                     f"{_display(c['strike_u'], dollars=True)} | {len(item['points'])} | "
                     f"{_display(latest['mid_u'], dollars=True)} | {_display(latest['delta'])} |")
    lines.extend(["", "Latest changes and contract gaps:"])
    for item in report["series"]:
        c = item["contract"]
        label = f"{item['source']} {c['expiry_date']} {_display(c['strike_u'], dollars=True)}"
        if item["changes"]:
            change = item["changes"][-1]
            lines.append(f"- {label}: {change['from']} → {change['to']} "
                         f"({change['elapsed_hours']:.2f} hours); midpoint change "
                         f"{_display(change['mid_change_u'], dollars=True)}; delta change "
                         f"{_display(change['delta_change'])}; missing session dates "
                         f"{', '.join(item['missing_dates']) or 'none'}")
        else:
            lines.append(f"- {label}: insufficient distinct capture times for a change")
    lines.extend(["", "Coverage:"])
    for coverage in report["coverage"]:
        lines.append(f"- {coverage['source']}: missing batches "
                     f"{', '.join(coverage['missing_batch_dates']) or 'none'}; "
                     f"dates without valid quotes "
                     f"{', '.join(coverage['dates_without_valid_quotes']) or 'none'}; "
                     f"dates without complete batches "
                     f"{', '.join(coverage['dates_without_complete_batches']) or 'none'}")
    for batch in report["batches"]:
        if not batch["complete"]:
            lines.append(f"- {batch['source']} {batch['date']}: incomplete "
                         f"({batch['status']}), {batch['quote_count']} quotes; "
                         f"missing expiries {batch['missing_expiries']}; "
                         f"missing contracts {batch['missing_contracts']}")
    lines.extend(["", "Cross-contract statistics (changes over observed intervals):"])
    for stats in report["cross_contract_statistics"]:
        lines.append(f"- {stats['source']}; DTE {stats['initial_dte_bucket']}; "
                     f"initial delta {stats['initial_delta_bucket']}; "
                     f"hours {stats['elapsed_hours_bucket']}: {stats['interval_count']} intervals, "
                     f"{stats['distinct_contracts']} contracts, "
                     f"{stats['distinct_observation_dates']} dates; median delta change "
                     f"{_display(stats['delta_change']['median'])}; median midpoint change "
                     f"{_display(stats['mid_change_u']['median'], dollars=True)}")
    lines.extend(["", *[f"- {w}" for w in report["warnings"]], ""])
    return "\n".join(lines)
