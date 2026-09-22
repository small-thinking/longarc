"""Descriptive candidate comparisons from one saved chain batch, without ranking."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from longarc.analytics.costs import calculate_costs
from longarc.analytics.decisions import screen_entry
from longarc.analytics.metrics import NY, _integer, _number, _time, calculate
from longarc.core import symbols
from longarc.storage import store


def _half_premium_scenario(
    bid: int, fees: dict[str, Any], contracts: int, multiplier: int,
) -> dict[str, Any]:
    # The existing cost engine requires integer microdollar prices. Round upward
    # solely for its fee-waiver comparison (the threshold is also an integer),
    # then restore the exact half-microdollar when calculating scenario P&L.
    close = Decimal(bid) / 2
    result = calculate_costs(
        fees, contracts=contracts, multiplier=multiplier, opening_premium_u=bid,
        closing_ask_u=(bid + 1) // 2,
        opening_extra_fees_u=fees.get("opening_extra_fees_u"),
        closing_extra_fees_u=fees.get("closing_extra_fees_u"),
    )
    metrics = result["metrics"]
    adjustment = (Decimal((bid + 1) // 2) - close) * contracts * multiplier
    for key in ("gross_option_pnl_u", "net_option_pnl_u", "known_cost_net_option_pnl_u"):
        if metrics[key] is not None:
            metrics[key] = format(Decimal(metrics[key]) + adjustment, "f")
    metrics["closing_price_u"] = format(close, "f")
    return {**result, "price_basis": "hypothetical_half_opening_bid",
            "opening_price_u": str(bid), "contracts": contracts, "multiplier": multiplier,
            "gross_capture_fraction": "0.5", "future_execution_verified": False,
            "description": "Sell at observed bid; hypothetical buyback at exactly 50% of bid. "
                           "No forecast of whether or when this price occurs."}


def compare_candidates(
    path: Path, *, observation_id: str, as_of: str, policy: dict[str, Any],
    fees: dict[str, Any], contracts: int = 1, multiplier: int = 100,
) -> dict[str, Any]:
    """Compare one canonical option-chain-v1 observation using caller assumptions.

    Source ages and collection window are disclosed, never treated as proof of
    current executable quotes. A policy threshold pass is not an entry approval.
    as_of is the evaluation clock, not a historical database-knowledge cutoff.
    """
    now = _time(as_of)
    if not isinstance(policy, dict) or not isinstance(fees, dict):
        raise ValueError("policy and fees must be objects")
    if _integer(contracts, "contracts", 1) is None:
        raise ValueError("contracts is required")
    if _integer(multiplier, "multiplier", 1) is None:
        raise ValueError("multiplier is required")
    # Validate the fee schedule even if no usable bid survives the quality checks.
    _half_premium_scenario(0, fees, contracts, multiplier)
    payload = store.get_observation(path, observation_id)["payload"]
    with store.connect(path, readonly=True) as db:
        superseded = db.execute("SELECT 1 FROM observations WHERE supersedes_id=?",
                                (observation_id,)).fetchone()
        if superseded:
            raise ValueError("Observation was superseded; select its canonical replacement")
    inputs, batch = payload["inputs"], payload["results"]
    if inputs.get("format") != "option-chain-v1" or payload["kind"] != "observation":
        raise ValueError("A canonical option-chain-v1 observation is required")
    symbol = symbols.canonical_symbol(inputs.get("symbol", payload["scope"].removeprefix(
        "options:")))
    if payload["scope"] != f"options:{symbol}":
        raise ValueError("Observation scope does not match its symbol")
    symbols.require_policy_symbol(symbol, policy)
    observed = _time(payload["observed_at"])
    if observed > now:
        raise ValueError("Observation is later than as_of")
    rows = []
    captures = []
    identities = set()
    for quote in batch.get("quotes", []):
        contract, snapshot = quote["contract"], quote["snapshot"]
        if contract.get("symbol") != symbol or contract.get("option_type") != "CALL":
            raise ValueError("Every candidate must be a CALL for the observation symbol")
        identity = (contract.get("expiry_date"), contract.get("strike_u"),
                    contract.get("multiplier"), contract.get("option_symbol"))
        if identity in identities:
            raise ValueError("Duplicate candidate identity within observation")
        identities.add(identity)
        calc = calculate(contract, snapshot, as_of)
        metrics = calc["metrics"]
        warnings = list(dict.fromkeys([*quote.get("warnings", []), *calc["warnings"]]))
        captured = snapshot.get("captured_at")
        capture_consistent = None if captured is None else _time(captured) <= observed
        if captured is not None:
            captures.append(_time(captured))
        if capture_consistent is False:
            warnings.append("capture_timestamp_after_observation")
        # Source clocks beyond their capture are inconsistent even if as_of is later.
        inconsistent = {name for name in ("quote", "greeks", "underlying")
                        if captured is not None and snapshot.get(f"{name}_at") is not None
                        and _time(snapshot[f"{name}_at"]) > _time(captured)}
        warnings.extend(f"{name}_timestamp_after_capture" for name in sorted(inconsistent))
        quote_valid = (capture_consistent is True and metrics["midpoint_u"] is not None
                       and "quote" not in inconsistent)
        greeks_valid = (capture_consistent is True and metrics["observed_delta"] is not None
                        and "greeks" not in inconsistent)
        underlying_valid = (capture_consistent is True
                            and metrics["strike_distance_u"] is not None
                            and "underlying" not in inconsistent)
        facts = {**contract, **snapshot, "contracts": contracts, "multiplier": multiplier,
                 "bid_u": snapshot.get("bid_u") if quote_valid else None,
                 "ask_u": snapshot.get("ask_u") if quote_valid else None,
                 "delta": metrics["observed_delta"] if greeks_valid else None,
                 "open_interest": snapshot.get("open_interest")
                 if capture_consistent is True else None,
                 "underlying_price_u": snapshot.get("underlying_price_u")
                 if underlying_valid else None}
        checks = screen_entry(facts, policy["policy_parameters"], now.astimezone(NY).date())
        quote_checks = {key: checks[key] for key in (
            "entry_delta", "entry_dte", "otm", "liquidity_spread", "liquidity_oi")}
        timing = {"capture": {"source_at": captured,
                              "age_seconds": metrics["capture_age_seconds"],
                              "time_consistent": capture_consistent}}
        for name in ("quote", "greeks", "underlying"):
            age = metrics[f"{name}_age_seconds"]
            timing[name] = {"source_at": snapshot.get(f"{name}_at"), "age_seconds": age,
                            "time_consistent": None if age is None or capture_consistent is None
                            else capture_consistent and age >= 0 and name not in inconsistent,
                            "freshness_verified": None}
        probabilities = {}
        for name in ("probability_touch", "probability_otm"):
            value = _number(snapshot.get(name), name)
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
            probabilities[name] = None if value is None else format(value, "f")
        bid = snapshot.get("bid_u") if quote_valid else None
        matching_multiplier = contract.get("multiplier") in (None, multiplier)
        scenario = (_half_premium_scenario(bid, fees, contracts, multiplier)
                    if bid is not None and bid > 0 and matching_multiplier else None)
        if not matching_multiplier:
            warnings.append("scenario_multiplier_does_not_match_contract")
        rows.append({"contract": contract, "snapshot": snapshot, "metrics": metrics,
                     "provider_probabilities": probabilities, "timing": timing,
                     "quote_policy_checks": quote_checks,
                     "quote_policy_thresholds_pass": all(v is True for v in quote_checks.values()),
                     "entry_eligible": None,
                     "unknown_entry_checks": ["quote_freshness", "greeks_freshness",
                         "underlying_freshness", "standard_contract", "position_verified",
                         "coverage_verified", "orders_clear", "sizing_approved",
                         "dividend_window_clear", "reentry_cooldown_clear", "market_open"],
                     "half_premium_buyback_scenario": scenario, "warnings": warnings})
    start, end = (min(captures), max(captures)) if captures else (None, None)
    return {"report": "candidate-comparison-v1", "observation_id": observation_id,
            "as_of": now.isoformat(), "observed_at": payload["observed_at"],
            "symbol": symbol, "source": payload["source"],
            "mode": payload["mode"], "quality": payload["quality"],
            "policy_hash": store.digest(policy), "fee_schedule_as_of": fees["as_of"],
            "fee_schedule": fees,
            "scenario_assumptions": {"contracts": contracts, "multiplier": multiplier},
            "coverage": {"complete": batch.get("complete"), "status": batch.get("status"),
                         "closed_session": inputs.get("closed_session"),
                         "missing_expiries": batch.get("missing_expiries", []),
                         "missing_contracts": batch.get("missing_contracts", []),
                         "candidate_count": len(rows),
                         "capture_start": start.isoformat() if start else None,
                         "capture_end": end.isoformat() if end else None,
                         "capture_span_seconds": (end - start).total_seconds()
                         if start is not None and end is not None else None},
            "candidates": rows,
            "warnings": [*batch.get("warnings", []),
                "One saved collection window; candidates may not be simultaneous or current.",
                "Observed policy thresholds do not verify account, calendar, or entry eligibility.",
                "Provider probabilities are descriptive estimates, not realized frequencies.",
                "Hypothetical fee-aware scenarios are not expected income or annualized returns."]}
