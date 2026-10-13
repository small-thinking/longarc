"""Broker-independent validation of the narrow covered-CALL order intent."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_OPTION = re.compile(r"([A-Z][A-Z0-9.]{0,5}) {0,5}(\d{2})(\d{2})(\d{2})C(\d{8})\Z")
_PRICE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?\Z")


def validate_call_symbol(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 21:
        raise ValueError("Standard CALL option symbol is required")
    match = _OPTION.fullmatch(value)
    if match is None or value[:6] != f"{match.group(1):<6}":
        raise ValueError("Standard CALL option symbol is required")
    _, year, month, day, strike = match.groups()
    try:
        date(2000 + int(year), int(month), int(day))
    except ValueError as error:
        raise ValueError("Invalid CALL expiry") from error
    if int(strike) <= 0:
        raise ValueError("Positive CALL strike is required")
    return value


def validate_quantity(value: Any) -> int:
    if type(value) is not int or not 1 <= value < 2**31:
        raise ValueError("Order quantity must be a positive integer")
    return value


def validate_limit_price(value: Any, tick: str = "0.01") -> str:
    if not isinstance(value, str) or len(value) > 15 or not _PRICE.fullmatch(value):
        raise ValueError("Limit price must be a positive decimal string")
    if tick not in {"0.01", "0.05"}:
        raise ValueError("Tick must be 0.01 or 0.05")
    try:
        price = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid limit price") from error
    if not price.is_finite() or price <= 0 or price % Decimal(tick):
        raise ValueError("Limit price must be positive and on the selected tick")
    return format(price.quantize(Decimal("0.01")), ".2f")


def normalize_call_intent(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict) or request.keys() - {
        "option_symbol", "instruction", "quantity", "price", "tick"
    }:
        raise ValueError("Unsupported order request fields")
    if request.get("instruction") not in {"SELL_TO_OPEN", "BUY_TO_CLOSE"}:
        raise ValueError("Only SELL_TO_OPEN or BUY_TO_CLOSE is supported")
    return {
        "option_symbol": validate_call_symbol(request.get("option_symbol")),
        "instruction": request["instruction"],
        "quantity": validate_quantity(request.get("quantity")),
        "price": validate_limit_price(request.get("price"), request.get("tick", "0.01")),
    }
