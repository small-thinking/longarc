"""Shared exact monetary units, finite numbers, and provider-clock conversions."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def money_u(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value)) * 1_000_000
    except InvalidOperation:
        return None
    if amount.is_finite() and 0 <= amount < 2**63 and amount == amount.to_integral_value():
        return int(amount)
    return None


def count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if result.is_finite() and 0 <= result < 2**63 and result == result.to_integral_value():
        return int(result)
    return None


def number(
    value: Any, *, nonnegative: bool = False, lower: int | None = None, upper: int | None = None
) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if (
        not result.is_finite()
        or (nonnegative and result < 0)
        or (lower is not None and result < lower)
        or (upper is not None and result > upper)
    ):
        return None
    converted = float(result)
    return converted if math.isfinite(converted) else None


def epoch_time(value: Any, *, milliseconds: bool = False) -> str | None:
    result = count(value)
    if not result:
        return None
    try:
        seconds = result / 1000 if milliseconds else result
        return (
            datetime.fromtimestamp(seconds, UTC)
            .isoformat(timespec="milliseconds" if milliseconds else "seconds")
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        return None
