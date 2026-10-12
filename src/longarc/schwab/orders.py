"""Default-disabled, single-leg CALL order capability. No automatic retries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = "https://api.schwabapi.com"
_ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ORDER_ID = re.compile(r"[0-9]{1,30}\Z")
_OPTION = re.compile(r"([A-Z][A-Z0-9.]{0,5}) {0,5}(\d{2})(\d{2})(\d{2})C(\d{8})\Z")
_PRICE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?\Z")
_KNOWN_STATUSES = {
    "AWAITING_PARENT_ORDER", "AWAITING_CONDITION", "AWAITING_STOP_CONDITION",
    "AWAITING_MANUAL_REVIEW", "ACCEPTED", "AWAITING_UR_OUT", "PENDING_ACTIVATION",
    "QUEUED", "WORKING", "PENDING_CANCEL", "PENDING_REPLACE", "NEW",
    "AWAITING_RELEASE_TIME", "PENDING_ACKNOWLEDGEMENT", "PENDING_RECALL",
    "PARTIALLY_FILLED", "REJECTED", "CANCELED", "REPLACED", "FILLED", "EXPIRED",
}
_TERMINAL_STATUSES = {"REJECTED", "CANCELED", "REPLACED", "FILLED", "EXPIRED"}
_CLEARABLE_ZERO_FILL_STATUSES = {"REJECTED", "CANCELED", "EXPIRED"}


@dataclass(frozen=True)
class OrderResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


OrderTransport = Callable[[Request, float], OrderResponse]


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, request: Request, fp: object, code: int, msg: str,
        headers: object, newurl: str,
    ) -> None:
        return None


def order_transport(request: Request, timeout: float) -> OrderResponse:
    """One HTTP attempt; redirects and error bodies are never followed or surfaced."""
    opener = build_opener(_NoRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            return OrderResponse(response.status, dict(response.headers), response.read())
    except HTTPError as error:
        return OrderResponse(error.code, dict(error.headers), b"")


def _option_symbol(value: Any) -> str:
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


def _quantity(value: Any) -> int:
    if type(value) is not int or not 1 <= value < 2**31:
        raise ValueError("Order quantity must be a positive integer")
    return value


def _price(value: Any, tick: str = "0.01") -> str:
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


def build_limit_call_order(request: dict[str, Any]) -> dict[str, Any]:
    """Build only a normal-session, day-duration, single CALL limit order."""
    if not isinstance(request, dict) or request.keys() - {
        "option_symbol", "instruction", "quantity", "price", "tick"
    }:
        raise ValueError("Unsupported order request fields")
    instruction = request.get("instruction")
    if instruction not in {"SELL_TO_OPEN", "BUY_TO_CLOSE"}:
        raise ValueError("Only SELL_TO_OPEN or BUY_TO_CLOSE is supported")
    symbol = _option_symbol(request.get("option_symbol"))
    quantity = _quantity(request.get("quantity"))
    price = _price(request.get("price"), request.get("tick", "0.01"))
    return {"session": "NORMAL", "duration": "DAY", "orderType": "LIMIT",
            "price": price, "orderStrategyType": "SINGLE",
            "orderLegCollection": [{"instruction": instruction, "quantity": quantity,
                                    "instrument": {"assetType": "OPTION", "symbol": symbol}}]}


def validate_order_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Reject extra broker fields, children, and any change to the narrow shape."""
    if not isinstance(spec, dict) or set(spec) != {
        "session", "duration", "orderType", "price", "orderStrategyType",
        "orderLegCollection",
    }:
        raise ValueError("Unsupported order specification shape")
    for name, expected in (("session", "NORMAL"), ("duration", "DAY"),
                           ("orderType", "LIMIT"), ("orderStrategyType", "SINGLE")):
        if spec[name] != expected:
            raise ValueError(f"Unsupported {name}")
    legs = spec["orderLegCollection"]
    if not isinstance(legs, list) or len(legs) != 1 or not isinstance(legs[0], dict):
        raise ValueError("Exactly one CALL leg is required")
    leg = legs[0]
    if set(leg) != {"instruction", "quantity", "instrument"}:
        raise ValueError("Unsupported order leg fields")
    instrument = leg["instrument"]
    if not isinstance(instrument, dict) or set(instrument) != {"assetType", "symbol"}:
        raise ValueError("Unsupported option instrument")
    if instrument["assetType"] != "OPTION":
        raise ValueError("Only OPTION instruments are supported")
    return build_limit_call_order({"option_symbol": instrument["symbol"],
                                   "instruction": leg["instruction"],
                                   "quantity": leg["quantity"], "price": spec["price"]})


def order_digest(spec: dict[str, Any]) -> str:
    normalized = validate_order_spec(spec)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _known_quantity(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def summarize_order(data: object) -> dict[str, Any]:
    """Keep status and fill/activity evidence without inventing missing quantities."""
    if not isinstance(data, dict):
        raise ValueError("Order response must be an object")
    status = data.get("status") if isinstance(data.get("status"), str) else None
    quantity = _known_quantity(data.get("quantity"))
    filled = _known_quantity(data.get("filledQuantity"))
    remaining = _known_quantity(data.get("remainingQuantity"))
    activities = data.get("orderActivityCollection")
    if not isinstance(activities, list):
        activities = None
    return {"status": status, "quantity": quantity, "filledQuantity": filled,
            "remainingQuantity": remaining, "orderActivityCollection": activities,
            "status_known": status in _KNOWN_STATUSES,
            "terminal": status in _TERMINAL_STATUSES}


def _location_order_id(headers: Mapping[str, str], account_hash: str) -> str | None:
    location = next((value for key, value in headers.items() if key.lower() == "location"), None)
    if not isinstance(location, str):
        return None
    parsed = urlsplit(location)
    if (parsed.scheme != "https" or parsed.netloc != "api.schwabapi.com"
            or parsed.query or parsed.fragment):
        return None
    prefix = f"/trader/v1/accounts/{account_hash}/orders/"
    order_id = parsed.path.removeprefix(prefix)
    return order_id if parsed.path.startswith(prefix) and _ORDER_ID.fullmatch(order_id) else None


class SchwabOrderClient:
    """Separate order transport; write operations require an explicit enable gate."""

    def __init__(
        self, access_token: str, account_hash: str, *, enabled: bool = False,
        transport: OrderTransport | None = None, timeout: float = 20.0,
    ) -> None:
        if (not isinstance(access_token, str) or not access_token
                or access_token != access_token.strip()):
            raise ValueError("A non-empty access token is required")
        if not isinstance(account_hash, str) or not _ACCOUNT.fullmatch(account_hash):
            raise ValueError("Invalid account hash")
        if type(enabled) is not bool or timeout <= 0:
            raise ValueError("Invalid order client configuration")
        self._token = access_token
        self._account_hash = account_hash
        self._enabled = enabled
        self._transport = transport or order_transport
        self._timeout = timeout

    def _request(
        self, method: str, path: str, spec: dict[str, Any] | None = None,
    ) -> OrderResponse | None:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        body = None
        if spec is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(spec, separators=(",", ":")).encode()
        request = Request(HOST + path, data=body, headers=headers, method=method)
        try:
            response = self._transport(request, self._timeout)
        except OSError:
            return None
        if not isinstance(response, OrderResponse):
            return None
        return response

    def _enabled_write(self) -> None:
        if not self._enabled:
            raise PermissionError("Schwab order writes are disabled")

    def _orders_path(self) -> str:
        return f"/trader/v1/accounts/{self._account_hash}/orders"

    @staticmethod
    def _unknown(response: OrderResponse | None) -> dict[str, Any]:
        return {"outcome": "UNKNOWN", "reconciliation_required": True,
                "http_status": None if response is None else response.status,
                "order_id": None}

    def preview_order(self, spec: dict[str, Any]) -> dict[str, Any]:
        self._enabled_write()
        normalized = validate_order_spec(spec)
        response = self._request("POST", f"/trader/v1/accounts/{self._account_hash}/previewOrder",
                                 normalized)
        if response is None or response.status >= 500 or response.status in {408, 429}:
            return self._unknown(response)
        if response.status != 200 and 200 <= response.status < 400:
            return self._unknown(response)
        if response.status != 200:
            return {"outcome": "REJECTED", "reconciliation_required": False,
                    "http_status": response.status, "order_id": None}
        try:
            preview = json.loads(response.body)
        except (ValueError, UnicodeDecodeError):
            return self._unknown(response)
        if not isinstance(preview, dict) or not preview:
            return self._unknown(response)
        return {"outcome": "PREVIEWED", "reconciliation_required": False,
                "http_status": response.status, "order_id": None, "preview": preview}

    def submit_order(self, spec: dict[str, Any]) -> dict[str, Any]:
        self._enabled_write()
        normalized = validate_order_spec(spec)
        response = self._request("POST", self._orders_path(), normalized)
        return self._create_result(response)

    def replace_order(self, order_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        self._enabled_write()
        current = self._order_id(order_id)
        normalized = validate_order_spec(spec)
        response = self._request("PUT", f"{self._orders_path()}/{current}", normalized)
        return self._create_result(response)

    def _create_result(self, response: OrderResponse | None) -> dict[str, Any]:
        if response is None or response.status >= 500 or response.status in {408, 429}:
            return self._unknown(response)
        if response.status != 201 and 200 <= response.status < 400:
            return self._unknown(response)
        if response.status != 201:
            return {"outcome": "REJECTED", "reconciliation_required": False,
                    "http_status": response.status, "order_id": None}
        order_id = _location_order_id(response.headers, self._account_hash)
        if order_id is None:
            return self._unknown(response)
        return {"outcome": "ACCEPTED", "reconciliation_required": True,
                "http_status": response.status, "order_id": order_id}

    @staticmethod
    def _order_id(value: Any) -> str:
        if not isinstance(value, str) or not _ORDER_ID.fullmatch(value):
            raise ValueError("Invalid order ID")
        return value

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._enabled_write()
        current = self._order_id(order_id)
        response = self._request("DELETE", f"{self._orders_path()}/{current}")
        if response is None or response.status >= 500 or response.status in {408, 429}:
            return self._unknown(response)
        if response.status in {200, 204}:
            return {"outcome": "CANCELLATION_REQUESTED", "reconciliation_required": True,
                    "http_status": response.status, "order_id": current}
        if 200 <= response.status < 400:
            return self._unknown(response)
        return {"outcome": "REJECTED", "reconciliation_required": False,
                "http_status": response.status, "order_id": current}

    def get_order(self, order_id: str) -> dict[str, Any]:
        current = self._order_id(order_id)
        response = self._request("GET", f"{self._orders_path()}/{current}")
        if response is None or response.status >= 500 or response.status in {408, 429}:
            return self._unknown(response)
        if response.status != 200 and 200 <= response.status < 400:
            return self._unknown(response)
        if response.status != 200:
            return {"outcome": "UNAVAILABLE", "reconciliation_required": True,
                    "http_status": response.status, "order_id": current}
        try:
            data = json.loads(response.body)
            summary = summarize_order(data)
        except (ValueError, UnicodeDecodeError):
            return self._unknown(response)
        broker_id = data.get("orderId")
        if (type(broker_id) not in {str, int} or str(broker_id) != current):
            return {**self._unknown(response), "order_id": current}
        if not summary["status_known"]:
            return {**self._unknown(response), "order_id": current,
                    "status_known": False, "terminal": False,
                    "order": summary, "raw_order": data}
        terminal = summary["terminal"]
        clear = (summary["status"] in _CLEARABLE_ZERO_FILL_STATUSES
                 and summary["filledQuantity"] == 0)
        return {"outcome": "OBSERVED", "reconciliation_required": not clear,
                "http_status": response.status, "order_id": current,
                "status_known": True, "terminal": terminal,
                "order": summary, "raw_order": data}
