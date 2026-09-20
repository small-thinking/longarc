"""Covered-call close scenarios, with explicit fees and spread accounting.

Inputs ending in ``_u`` are nonnegative integer millionths of a dollar. Premiums,
quotes and slippage are per underlying unit; actual/extra fees are TOTAL per leg.
Actual fees are all-in and override estimates, including an explicit zero.
Output money is decimal strings in the same units, never binary floating point.
Ask-based scenarios already pay the spread: midpoint spread costs are diagnostic
only, never deducted again. Only supplied fills support realized-price results.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any


def _money(value: Any, name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError(f"{name} must be a nonnegative integer microdollar amount")
    return value


def calculate_costs(
    schedule: dict[str, Any], *, contracts: int, multiplier: int, opening_premium_u: int,
    closing_ask_u: int | None = None, closing_bid_u: int | None = None,
    closing_mid_u: int | None = None, closing_fill_u: int | None = None,
    opening_actual_fees_u: int | None = None, closing_actual_fees_u: int | None = None,
    opening_extra_fees_u: int | None = None, closing_extra_fees_u: int | None = None,
    closing_slippage_u: int = 0, opening_mid_u: int | None = None,
) -> dict[str, Any]:
    """Return ``metrics``, ``warnings``, ``status`` and schedule provenance.

    Schedule requires as_of (YYYY-MM-DD), base_fee_u (per leg),
    per_contract_fee_u (per leg), btc_waiver_threshold_u (per unit).
    Closing price preference: actual fill, ask, supplied/derived midpoint, bid.
    Missing extra fees make estimated net P&L incomplete (known-cost subtotal).
    Bid/midpoint-only scenarios are explicitly non-executable benchmarks.
    No quote-age, contract-identity or trading-policy validation is performed.
    """
    if not isinstance(schedule, dict):
        raise ValueError("schedule must be an object")
    stamp = schedule.get("as_of")
    if not isinstance(stamp, str):
        raise ValueError("schedule.as_of must be YYYY-MM-DD")
    date.fromisoformat(stamp)
    amounts = {}
    for key in ("base_fee_u", "per_contract_fee_u", "btc_waiver_threshold_u"):
        amounts[key] = _money(schedule.get(key), f"schedule.{key}")
    for name, count in (("contracts", contracts), ("multiplier", multiplier)):
        if type(count) is not int or not 1 <= count < 2**63:
            raise ValueError(f"{name} must be a positive integer")
    inputs = {
        "opening_premium_u": opening_premium_u, "closing_ask_u": closing_ask_u,
        "closing_bid_u": closing_bid_u, "closing_mid_u": closing_mid_u,
        "closing_fill_u": closing_fill_u, "opening_actual_fees_u": opening_actual_fees_u,
        "closing_actual_fees_u": closing_actual_fees_u,
        "opening_extra_fees_u": opening_extra_fees_u,
        "closing_extra_fees_u": closing_extra_fees_u,
        "closing_slippage_u": closing_slippage_u, "opening_mid_u": opening_mid_u,
    }
    for key, value in inputs.items():
        _money(value, key, optional=key not in {"opening_premium_u", "closing_slippage_u"})
    if closing_bid_u is not None and closing_ask_u is not None:
        if closing_bid_u > closing_ask_u:
            raise ValueError("crossed closing quote")
        if closing_mid_u is not None and not closing_bid_u <= closing_mid_u <= closing_ask_u:
            raise ValueError("closing midpoint must lie inside bid/ask")
    if closing_fill_u is not None and closing_slippage_u:
        raise ValueError("actual closing fill already includes slippage")

    warnings = []
    mid = None if closing_mid_u is None else Decimal(closing_mid_u)
    if mid is None and closing_bid_u is not None and closing_ask_u is not None:
        mid = (Decimal(closing_bid_u) + Decimal(closing_ask_u)) / 2
    close: Decimal
    if closing_fill_u is not None:
        close, basis = Decimal(closing_fill_u), "actual_fill"
    elif closing_ask_u is not None:
        close, basis = Decimal(closing_ask_u), "ask"
    elif mid is not None:
        close, basis = mid, "midpoint_benchmark"
    elif closing_bid_u is not None:
        close, basis = Decimal(closing_bid_u), "bid_benchmark"
    else:
        raise ValueError("a closing fill or quote is required")
    if basis.endswith("benchmark"):
        warnings.append("closing_price_is_not_executable_buy_quote")
    if basis != "actual_fill":
        warnings.append("scenario_not_realized_profit")
    close += closing_slippage_u
    units = Decimal(contracts) * multiplier
    base = Decimal(str(amounts["base_fee_u"]))
    per_contract = Decimal(str(amounts["per_contract_fee_u"])) * contracts
    waived = close <= Decimal(str(amounts["btc_waiver_threshold_u"]))

    def fees(leg: str, actual: int | None, extra: int | None, waive: bool) -> Decimal:
        if actual is not None:
            return Decimal(actual)
        if extra is None:
            warnings.append(f"unknown_{leg}_extra_fees")
        return base + (0 if waive else per_contract) + (extra or 0)

    opening_fees = fees("opening", opening_actual_fees_u, opening_extra_fees_u, False)
    closing_fees = fees("closing", closing_actual_fees_u, closing_extra_fees_u, waived)
    gross = (Decimal(opening_premium_u) - close) * units
    known_net = gross - opening_fees - closing_fees
    complete = not any(item.startswith("unknown_") for item in warnings)
    spread_cost = None if mid is None else (close - closing_slippage_u - mid) * units
    opening_spread = None if opening_mid_u is None else \
        (Decimal(opening_mid_u) - opening_premium_u) * units

    def text(value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")

    return {
        "schedule_as_of": stamp, "price_basis": basis, "fees_complete": complete,
        "status": "ok" if complete else "partial", "warnings": warnings,
        "metrics": {
            "closing_price_u": text(close), "opening_fees_u": text(opening_fees),
            "closing_fees_u": text(closing_fees), "gross_option_pnl_u": text(gross),
            "net_option_pnl_u": text(known_net) if complete else None,
            "known_cost_net_option_pnl_u": text(known_net),
            "closing_slippage_total_u": text(Decimal(closing_slippage_u) * units),
            "closing_spread_vs_midpoint_u": text(spread_cost),
            "opening_spread_vs_midpoint_u": text(opening_spread),
            "estimated_closing_contract_fee_waived": waived
            if closing_actual_fees_u is None else None,
        },
    }
