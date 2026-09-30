"""Comparable saved call-IV context and explicit sensitivities, not expected returns."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from longarc.analytics.history import _number, _time
from longarc.core.symbols import canonical_symbol
from longarc.storage import store

NY = ZoneInfo("America/New_York")


def _positive(value: Any, name: str, *, zero: bool = False) -> float:
    number = _number(value)
    if number is None or (number < 0 if zero else number <= 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")
    return number


def context_metrics(
    iv: float | None, history: list[float], *, min_samples: int = 30,
    forecast_volatility: float | None = None, reference_iv: float | None = None,
    vega_per_vol_point: float | None = None, contracts: int = 1, multiplier: int = 100,
) -> dict[str, Any]:
    """IV inputs are fractions; Vega is dollars/share per ONE percentage point.

    History excludes the reference day. Rank extends that historical range with
    current IV; percentile counts strictly lower historical values (ties excluded).
    A forecast is an explicit same-horizon annualized assumption, not a fitted model.
    """
    if type(min_samples) is not int or min_samples < 2:
        raise ValueError("min_samples must be an integer >= 2")
    for name, quantity in (("contracts", contracts), ("multiplier", multiplier)):
        if type(quantity) is not int or quantity <= 0:
            raise ValueError(f"{name} must be a positive integer")
    for sample in history:
        _positive(sample, "historical IV")
    for name, value in (("IV", iv), ("forecast_volatility", forecast_volatility),
                        ("reference_iv", reference_iv)):
        if value is not None:
            _positive(value, name)
    if vega_per_vol_point is not None:
        _positive(vega_per_vol_point, "vega_per_vol_point", zero=True)
    n = len(history)
    enough = n >= min_samples
    result: dict[str, Any] = {
        "historical_samples": n, "minimum_samples": min_samples,
        "sample_requirement_met": enough,
        "historical_min_iv": min(history) if history else None,
        "historical_median_iv": median(history) if history else None,
        "historical_max_iv": max(history) if history else None,
        "iv_percentile_pct": None, "iv_rank_pct": None,
        "forecast_variance_spread_proxy": None, "iv_only_sensitivity_scenario": None,
    }
    if iv is None:
        return result
    if enough:
        result["iv_percentile_pct"] = 100 * sum(v < iv for v in history) / n
        low, high = min(iv, min(history)), max(iv, max(history))
        result["iv_rank_pct"] = 100 * (iv - low) / (high - low) if high > low else None
    if forecast_volatility is not None:
        result["forecast_variance_spread_proxy"] = {
            "forecast_volatility_fraction": forecast_volatility,
            "volatility_spread_percentage_points": 100 * (iv - forecast_volatility),
            "annualized_variance_spread": iv * iv - forecast_volatility * forecast_volatility,
            "basis": "Caller-supplied same-horizon annualized forecast assumption; "
                     "single-call proxy, not model-free VRP or dollar profit.",
        }
    target = reference_iv if reference_iv is not None else median(history) if enough else None
    if target is not None and vega_per_vol_point is not None:
        per_share = vega_per_vol_point * 100 * (iv - target)
        result["iv_only_sensitivity_scenario"] = {
            "reference_iv_fraction": target,
            "reference_basis": "explicit assumption" if reference_iv is not None
            else "prior daily sample median (not a forecast)",
            "vega_usd_per_share_per_percentage_point": vega_per_vol_point,
            "iv_drop_percentage_points": 100 * (iv - target),
            "contracts_assumed": contracts, "multiplier_assumed": multiplier,
            "short_call_gross_iv_only_pnl_usd": round(per_share * contracts * multiplier, 6),
            "basis": "Local linear sensitivity, constant spot/time/rates/dividends; "
                     "Vega is an explicit assumption. Excludes other price effects and costs.",
        }
    return result


def _blockers(point: dict[str, Any], max_age: int) -> list[str]:
    snap = point["snapshot"]
    reasons = []
    if snap.get("iv_unit") != "fraction":
        reasons.append("unverified_iv_unit")
    if _number(snap.get("iv")) is None or snap["iv"] <= 0:
        reasons.append("missing_or_invalid_iv")
    bid, ask = _number(snap.get("bid_u")), _number(snap.get("ask_u"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        reasons.append("missing_or_invalid_bid_ask")
    for field in ("quote_at", "greeks_at"):
        if snap.get(field) is None:
            reasons.append("missing_" + field)
            continue
        try:
            age = (_time(point["captured_at"]) - _time(snap[field])).total_seconds()
            if age < 0 or age > max_age:
                reasons.append("future_or_stale_" + field)
        except ValueError:
            reasons.append("invalid_" + field)
    return reasons


def iv_history(
    path: Path, *, symbol: str, source: str, as_of: str, target_dte: int = 28,
    target_delta: float = .075, dte_tolerance: int = 7, delta_tolerance: float = .025,
    lookback_samples: int = 252, min_samples: int = 30, max_age_seconds: int = 300,
    expected_dates: list[str] | None = None, limit: int = 10000,
    forecast_volatility: float | None = None, reference_iv: float | None = None,
    vega_per_vol_point: float | None = None, contracts: int = 1, multiplier: int = 100,
) -> dict[str, Any]:
    """One latest target-bearing batch per NY date, selected before inspecting IV.

    This is a changing-contract, bounded nearest-match proxy, not an interpolated
    constant-maturity surface. No guessing units, filling time gaps, or trading.
    """
    canonical_symbol(symbol)
    cutoff = _time(as_of)
    if not isinstance(source, str) or not source:
        raise ValueError("source must be explicit and nonempty")
    for name, value, lower, upper in (
        ("target_dte", target_dte, 1, 3660), ("dte_tolerance", dte_tolerance, 0, 3660),
        ("lookback_samples", lookback_samples, 2, 10000), ("min_samples", min_samples, 2, 10000),
        ("max_age_seconds", max_age_seconds, 0, 86400), ("limit", limit, 1, 100000),
    ):
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{name} must be an integer between {lower} and {upper}")
    if min_samples > lookback_samples:
        raise ValueError("min_samples must be <= lookback_samples")
    target_delta = _positive(target_delta, "target_delta")
    delta_tolerance = _positive(delta_tolerance, "delta_tolerance", zero=True)
    if target_delta > 1 or delta_tolerance > 1:
        raise ValueError("delta target/tolerance must be <= 1")
    context_metrics(None, [], min_samples=min_samples, forecast_volatility=forecast_volatility,
                    reference_iv=reference_iv, vega_per_vol_point=vega_per_vol_point,
                    contracts=contracts, multiplier=multiplier)
    if expected_dates is not None and (not isinstance(expected_dates, list)
                                      or any(not isinstance(d, str) for d in expected_dates)):
        raise ValueError("expected_dates must be an array of ISO session dates")
    expected = ({date.fromisoformat(d) for d in expected_dates}
                if expected_dates is not None else None)
    with store.connect(path, readonly=True) as db:
        db.execute("BEGIN")
        store.check_schema(db)
        rows = db.execute(
            "SELECT o.* FROM observations o WHERE o.scope=? AND o.source=? AND o.mode='observe' "
            "AND o.quality!='synthetic' "
            "AND julianday(o.observed_at)<=julianday(?) "
            "AND julianday(o.recorded_at)<=julianday(?) "
            "AND NOT EXISTS (SELECT 1 FROM observations n WHERE n.supersedes_id=o.record_id "
            "AND julianday(n.observed_at)<=julianday(?) "
            "AND julianday(n.recorded_at)<=julianday(?)) ORDER BY o.observed_at LIMIT ?",
            ("options:" + symbol, source, as_of, as_of, as_of, as_of, limit + 1),
        ).fetchall()
    if len(rows) > limit:
        raise ValueError("IV history exceeds observation limit; increase --limit")
    units: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    daily: dict[str, dict[str, Any]] = {}
    quote_rows = batches = 0
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("inputs", {}).get("format") != "option-chain-v1":
            continue
        batches += 1
        quotes = payload.get("results", {}).get("quotes", [])
        if not isinstance(quotes, list):
            exclusions["malformed_batch"] += 1
            continue
        candidates = []
        for q in quotes:
            quote_rows += 1
            try:
                if not isinstance(q, dict):
                    raise ValueError("malformed_quote")
                c, s = q["contract"], q["snapshot"]
                if not isinstance(c, dict) or not isinstance(s, dict):
                    raise ValueError("malformed_quote")
                units[str(s.get("iv_unit") or "missing")] += 1
                if c["symbol"] != symbol or c["option_type"] != "CALL":
                    raise ValueError("symbol_or_contract_mismatch")
                captured = _time(s["captured_at"])
                day = captured.astimezone(NY).date()
                if captured > cutoff or captured > _time(row["observed_at"]):
                    raise ValueError("capture_after_cutoff_or_observation")
                if payload["inputs"].get("closed_session") or day.weekday() >= 5:
                    raise ValueError("closed_session")
                if expected is not None and day not in expected:
                    raise ValueError("outside_explicit_sessions")
                delta = _number(s.get("delta"))
                dte = (date.fromisoformat(c["expiry_date"]) - day).days
                if (delta is None or not 0 <= delta <= 1 or dte <= 0
                        or abs(dte - target_dte) > dte_tolerance
                        or abs(delta - target_delta) > delta_tolerance + 1e-12):
                    raise ValueError("outside_target_or_missing_delta")
                if type(c.get("strike_u")) is not int or c["strike_u"] <= 0:
                    raise ValueError("invalid_strike")
                point = {"date": day.isoformat(), "record_id": row["record_id"],
                         "recorded_at": row["recorded_at"], "captured_at": s["captured_at"],
                         "contract": c, "snapshot": s, "dte": dte, "delta": delta,
                         "dte_deviation": dte - target_dte,
                         "delta_deviation": delta - target_delta}
                point["blockers"] = _blockers(point, max_age_seconds)
                exclusions.update(point["blockers"])
                candidates.append(point)
            except (KeyError, TypeError, ValueError) as exc:
                exclusions[str(exc)] += 1
        if not candidates:
            continue
        # Never choose by the richness, validity, or availability of the IV value.
        selected = min(candidates, key=lambda p: (
            abs(p["dte_deviation"]), abs(p["delta_deviation"]),
            p["contract"]["expiry_date"], p["contract"]["strike_u"]))
        prior = daily.get(selected["date"])
        if prior is None or (_time(selected["captured_at"]), selected["record_id"]) > (
                _time(prior["captured_at"]), prior["record_id"]):
            daily[selected["date"]] = selected
    points = [daily[d] for d in sorted(daily)]
    reference = points[-1] if points else None
    prior_points = points[:-1] if points else []
    if expected is not None and reference:
        window = sorted(d for d in expected if d.isoformat() < reference["date"])
        window = window[-lookback_samples:]
        prior_points = [p for p in prior_points if date.fromisoformat(p["date"]) in window]
        missing_dates: list[str] | None = [d.isoformat() for d in window
                                         if not any(p["date"] == d.isoformat()
                                                    and not p["blockers"] for p in prior_points)]
    else:
        prior_points = prior_points[-lookback_samples:]
        missing_dates = None
    historical = [p for p in prior_points if not p["blockers"]]
    current_iv = reference["snapshot"]["iv"] if reference and not reference["blockers"] else None
    metrics = context_metrics(current_iv, [p["snapshot"]["iv"] for p in historical],
                              min_samples=min_samples, forecast_volatility=forecast_volatility,
                              reference_iv=reference_iv, vega_per_vol_point=vega_per_vol_point,
                              contracts=contracts, multiplier=multiplier)
    return {
        "format": "iv-history-v1", "status": "ok" if current_iv is not None
        and metrics["sample_requirement_met"] else "insufficient_comparable_iv",
        "symbol": symbol, "source": source, "mode": "observe", "as_of": as_of,
        "method": {"version": "nearest-target-daily-v1", "target_calendar_dte": target_dte,
                   "target_call_delta": target_delta, "dte_tolerance": dte_tolerance,
                   "delta_tolerance": delta_tolerance, "lookback_observed_dates": lookback_samples,
                   "max_source_age_seconds_at_capture": max_age_seconds,
                   "selection": "Nearest DTE, then delta, in latest target-bearing batch per date; "
                   "choose before inspecting IV. Reference date excluded from history.",
                   "percentile_ties": "strictly lower", "rank_range_includes_reference": True},
        "coverage": {"canonical_batches": batches, "quote_rows": quote_rows,
                     "iv_units": dict(units), "row_exclusion_reasons": dict(exclusions),
                     "target_dates": len(points), "usable_prior_dates": len(historical),
                     "missing_or_unusable_expected_dates": missing_dates,
                     "calendar_verified": expected_dates is not None},
        "reference_point": reference,
        "daily_points": [*prior_points, *([reference] if reference else [])],
        "reference_age_seconds_at_as_of": (cutoff - _time(reference["captured_at"])).total_seconds()
        if reference else None,
        "distribution_point_ids": [p["record_id"] for p in historical], "metrics": metrics,
        "warnings": [
            "Saved quotes only; no live valuation, policy eligibility or trade approval.",
            "Bounded nearest-match changing-contract proxy; no maturity/delta interpolation.",
            "Any target-bearing batch can replace that day's sample, including a narrow held-call "
            "capture within tolerance. Consistent daily neighborhoods are needed "
            "for comparability.",
            "IV fractions assumed annualized consistently within this explicit source; "
            "provider model and price basis remain unverified. Raw provider_display is excluded.",
            "No historical fallback when the latest target-bearing sample is unusable.",
            "Missing source times are excluded; capture time cannot replace source time.",
            "One point per date is not proof of independent trials or complete EOD coverage.",
            "Without explicit sessions, holidays/missing sessions are unknown; 252 observed "
            "dates do not certify a one-year history. No forward fill.",
            "As-of uses observation and recorded times; reference day never enters its history.",
            "Forecast spread is model-dependent; IV contraction sensitivity is not total P&L "
            "or proof that volatility risk premium was earned.",
        ],
    }
