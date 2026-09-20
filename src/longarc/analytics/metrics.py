"""Short-call quote metrics. Money uses integer micro-units and decimal strings.

Fractions are unitless (0.10 = 10%); Greeks retain the provider's units.
No price is an executable fill, and scenario quantities are never inferred.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _integer(value: Any, name: str, minimum: int = 0) -> int | None:
    if value is not None and (type(value) is not int or not minimum <= value < 2**63):
        raise ValueError(f"{name} must be an integer >= {minimum}, or null")
    return value


def _number(value: Any, name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{name} must be finite numeric data")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be finite numeric data") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite numeric data")
    return result


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO string with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    return result.astimezone(UTC)


def _text(value: Decimal | int | None) -> str | None:
    return None if value is None else str(value) if isinstance(value, int) else format(value, "f")


def _fraction(numerator: Decimal | int, denominator: Decimal | int) -> str | None:
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 28
        return _text(Decimal(numerator) / Decimal(denominator))


def calculate(
    contract: dict[str, Any], snapshot: dict[str, Any], as_of: str,
    scenario: dict[str, Any] | None = None, previous: dict[str, Any] | None = None,
    replacement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return metrics, warnings and status; malformed input raises ValueError.

    Missing/future times warn, future data are excluded, crossed quotes invalidate
    quote calculations. Scenario premiums are per unit, fees are total micro-units.
    The caller must verify previous-snapshot identity and chronology.
    """
    if not isinstance(contract, dict) or not isinstance(snapshot, dict):
        raise ValueError("contract and snapshot must be objects")
    symbol = contract.get("symbol")
    if contract.get("option_type") != "CALL" or not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("An identified CALL contract is required")
    if not isinstance(contract.get("expiry_date"), str):
        raise ValueError("expiry_date must be YYYY-MM-DD")
    expiry = date.fromisoformat(contract["expiry_date"])
    strike = _integer(contract.get("strike_u"), "strike_u", 1)
    multiplier = _integer(contract.get("multiplier"), "multiplier", 1)
    if strike is None:
        raise ValueError("strike_u is required")
    now = _time(as_of)
    warnings: list[str] = []
    metrics: dict[str, Any] = {"dte_calendar_days": (expiry - now.astimezone(NY).date()).days}
    valid_time: dict[str, bool] = {}
    for name, field in (("quote", "quote_at"), ("greeks", "greeks_at"),
                        ("underlying", "underlying_at"), ("capture", "captured_at")):
        value = snapshot.get(field)
        age = None if value is None else (now - _time(value)).total_seconds()
        metrics[f"{name}_age_seconds"] = age
        valid_time[name] = age is None or age >= 0
        if age is None:
            warnings.append(f"missing_{field}")
        elif age < 0:
            warnings.append(f"future_{field}")
    if metrics["dte_calendar_days"] < 0:
        warnings.append("expired_contract")
    if multiplier is None:
        warnings.append("unknown_multiplier")

    prices = {key: _integer(snapshot.get(key), key) for key in
              ("bid_u", "ask_u", "last_u", "underlying_price_u")}
    bid, ask, underlying = prices["bid_u"], prices["ask_u"], prices["underlying_price_u"]
    crossed = bid is not None and ask is not None and bid > ask
    if crossed:
        warnings.append("crossed_quote")
    for key in ("bid_u", "ask_u", "underlying_price_u"):
        if prices[key] is None:
            warnings.append(f"missing_{key}")
    if underlying == 0:
        warnings.append("zero_underlying_price")
        underlying = None
    if not valid_time["capture"]:
        valid_time = dict.fromkeys(valid_time, False)
    if not valid_time["underlying"]:
        underlying = None
    if crossed or not valid_time["quote"]:
        bid = ask = None
    midpoint = None if bid is None or ask is None else Decimal(bid + ask) / 2
    spread = None if bid is None or ask is None else ask - bid
    intrinsic = None if underlying is None else max(underlying - strike, 0)
    extrinsic = None if midpoint is None or intrinsic is None else midpoint - intrinsic
    if extrinsic is not None and extrinsic < 0:
        warnings.append("negative_midpoint_extrinsic")
    if midpoint == 0:
        warnings.append("zero_midpoint")
    metrics.update({
        "midpoint_u": _text(midpoint), "spread_u": _text(spread),
        "spread_fraction_of_midpoint": None if midpoint is None or spread is None
        else _fraction(spread, midpoint),
        "intrinsic_u": _text(intrinsic), "midpoint_extrinsic_u": _text(extrinsic),
        "strike_distance_u": None if underlying is None else _text(strike - underlying),
        "strike_distance_fraction": None if underlying is None
        else _fraction(strike - underlying, underlying),
    })
    for key in ("delta", "theta"):
        value = _number(snapshot.get(key), key)
        if key == "delta" and value is not None and not 0 <= value <= 1:
            raise ValueError("CALL delta must be between 0 and 1")
        if value is None:
            warnings.append(f"missing_{key}")
        metrics[f"observed_{key}"] = _text(value) if valid_time["greeks"] else None

    if scenario is not None:
        if not isinstance(scenario, dict):
            raise ValueError("scenario must be an object")
        allowed = {"contracts", "opening_premium_u", "opening_fees_u",
                   "closing_fees_u", "covered_shares"}
        if scenario.keys() - allowed:
            raise ValueError("Unknown scenario fields")
        inputs = {key: _integer(scenario.get(key), key, 1 if key == "contracts" else 0)
                  for key in allowed}
        quantity = inputs["contracts"]
        units = None if quantity is None or multiplier is None else quantity * multiplier
        opening, opening_fees = inputs["opening_premium_u"], inputs["opening_fees_u"]
        closing_fees, shares = inputs["closing_fees_u"], inputs["covered_shares"]
        close_gross = None if units is None or ask is None else units * ask
        opening_gross = None if units is None or opening is None else units * opening
        close_cost = None if close_gross is None or closing_fees is None \
            else close_gross + closing_fees
        pnl = None if opening_gross is None or opening_fees is None or close_cost is None \
            else opening_gross - opening_fees - close_cost
        metrics.update({
            "scenario_close_gross_u": _text(close_gross),
            "scenario_close_cost_u": _text(close_cost),
            "scenario_net_option_pnl_u": _text(pnl),
            "scenario_gross_premium_captured_fraction": None if opening is None or ask is None
            else _fraction(opening - ask, opening),
            "scenario_coverage_fraction": None if shares is None or units is None
            else _fraction(shares, units),
            "scenario_uncovered_units": None if shares is None or units is None
            else max(units - shares, 0),
        })
        if pnl is None:
            warnings.append("incomplete_pnl_scenario")

    if previous is not None:
        prior = calculate(contract, previous, as_of)
        for key in ("midpoint_u", "observed_delta", "observed_theta"):
            current_value, old_value = metrics[key], prior["metrics"][key]
            metrics[f"change_{key}"] = None if current_value is None or old_value is None \
                else _text(Decimal(current_value) - Decimal(old_value))
        old_underlying = _integer(previous.get("underlying_price_u"), "underlying_price_u")
        usable = prior["metrics"]["strike_distance_u"] is not None
        metrics["change_underlying_price_u"] = None if underlying is None or not usable \
            or old_underlying is None else _text(underlying - old_underlying)
        warnings.extend(f"previous:{item}" for item in prior["warnings"])

    if replacement is not None:
        if not isinstance(replacement, dict):
            raise ValueError("replacement must be an object")
        if replacement.keys() - {"contract", "snapshot", "opening_fees_u"}:
            raise ValueError("Unknown replacement fields")
        new_contract, new_snapshot = replacement.get("contract"), replacement.get("snapshot")
        if not isinstance(new_contract, dict) or not isinstance(new_snapshot, dict):
            raise ValueError("Replacement requires contract and snapshot objects")
        if any(new_contract.get(key) != contract.get(key)
               for key in ("symbol", "option_type", "multiplier")):
            raise ValueError("Replacement must have the same symbol, option type and multiplier")
        new = calculate(new_contract, new_snapshot, as_of)
        new_bid = _integer(new_snapshot.get("bid_u"), "replacement.bid_u")
        usable = new["metrics"]["midpoint_u"] is not None
        metrics.update({
            "roll_quote_cashflow_per_unit_u": None if ask is None or new_bid is None or not usable
            else _text(new_bid - ask),
            "roll_added_calendar_days": new["metrics"]["dte_calendar_days"]
            - metrics["dte_calendar_days"],
            "roll_strike_change_u": _text(new_contract["strike_u"] - strike),
        })
        new_fees = _integer(replacement.get("opening_fees_u"), "replacement.opening_fees_u")
        quote_flow = metrics["roll_quote_cashflow_per_unit_u"]
        roll_net = None
        if scenario is not None and units is not None and closing_fees is not None \
                and new_fees is not None and quote_flow is not None:
            roll_net = int(quote_flow) * units - closing_fees - new_fees
        metrics["scenario_roll_net_cashflow_u"] = _text(roll_net)
        if roll_net is None:
            warnings.append("incomplete_roll_fee_scenario")
        warnings.extend(f"replacement:{item}" for item in new["warnings"])
        warnings.append("roll_quote_cashflow_excludes_fees")

    status = "invalid" if crossed else "partial" if warnings else "ok"
    return {"metrics": metrics, "warnings": list(dict.fromkeys(warnings)), "status": status}
