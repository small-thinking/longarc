"""Default-disabled E*TRADE CALL limit orders, separate from ordinary GET reads.

This is a transport adapter, not a production trading policy. A future live
caller must supply authorization, fresh account/coverage/cash/quote preflight
and durable attempt/reconciliation records. The CLI never enables this client.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from longarc.brokers.order_http import OrderResponse, OrderTransport, order_transport
from longarc.brokers.order_intents import normalize_call_intent
from longarc.etrade.auth import (
    INACTIVE_AFTER,
    AccessToken,
    AppConfig,
    _midnight_after,
    signed_request,
)
from longarc.etrade.client import ETradeError

_ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CLIENT_ID = re.compile(r"[A-Za-z0-9]{1,20}\Z")
_ID = re.compile(r"[1-9][0-9]{0,18}\Z")
_STATUSES = {"OPEN", "EXECUTED", "CANCELLED", "INDIVIDUAL_FILLS",
             "CANCEL_REQUESTED", "EXPIRED", "REJECTED"}
_TERMINAL = {"EXECUTED", "CANCELLED", "EXPIRED", "REJECTED"}
PREVIEW_LIFETIME = 180
MAX_ORDER_PAGES = 100


def _id(value: Any) -> str:
    if type(value) not in {str, int} or not _ID.fullmatch(str(value)):
        raise ValueError("A positive numeric E*TRADE order/preview ID is required")
    if int(value) >= 2**63:
        raise ValueError("E*TRADE order/preview ID exceeds int64")
    return str(value)


def _client_id(value: str) -> str:
    if not isinstance(value, str) or not _CLIENT_ID.fullmatch(value):
        raise ValueError("clientOrderId requires 1-20 alphanumeric characters")
    return value


def _rows(value: Any) -> list[dict[str, Any]]:
    """E*TRADE JSON examples use both singleton objects and arrays."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    raise ValueError("Malformed E*TRADE order collection")


def _number(value: Any) -> Decimal | None:
    if type(value) not in {str, int, float}:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def build_limit_call_order(intent: dict[str, Any], client_order_id: str) -> dict[str, Any]:
    """Same OSI intent as Schwab, mapped to E*TRADE's structured option product."""
    call = normalize_call_intent(intent)
    symbol = call["option_symbol"]
    return {
        "orderType": "OPTN", "clientOrderId": _client_id(client_order_id),
        "Order": [{
            "allOrNone": False, "priceType": "LIMIT", "limitPrice": call["price"],
            "orderTerm": "GOOD_FOR_DAY", "marketSession": "REGULAR",
            "Instrument": [{
                "Product": {
                    "symbol": symbol[:6].strip(), "securityType": "OPTN", "callPut": "CALL",
                    "expiryYear": 2000 + int(symbol[6:8]), "expiryMonth": int(symbol[8:10]),
                    "expiryDay": int(symbol[10:12]),
                    "strikePrice": format(Decimal(symbol[13:]) / 1000, ".3f"),
                },
                "orderAction": {"SELL_TO_OPEN": "SELL_OPEN", "BUY_TO_CLOSE": "BUY_CLOSE"}[
                    call["instruction"]],
                "quantityType": "QUANTITY", "quantity": call["quantity"],
            }],
        }],
    }


def _digest(spec: dict[str, Any]) -> str:
    encoded = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _product_matches(product: Any, desired: dict[str, Any]) -> bool:
    if not isinstance(product, dict):
        return False
    for key, value in desired.items():
        if key in {"expiryYear", "expiryMonth", "expiryDay", "strikePrice"}:
            if _number(product.get(key)) != Decimal(str(value)):
                return False
        elif product.get(key) != value:
            return False
    return True


def _matches_response(spec: dict[str, Any], response: dict[str, Any]) -> bool:
    """Compare only requested order terms; provider-added estimates remain evidence."""
    try:
        if response.get("orderType") != "OPTN":
            return False
        orders = _rows(response.get("Order"))
        if len(orders) != 1:
            return False
        expected, actual = spec["Order"][0], orders[0]
        if any(actual.get(k) != expected[k] for k in ("priceType", "orderTerm", "marketSession")):
            return False
        if _number(actual.get("limitPrice")) != Decimal(expected["limitPrice"]):
            return False
        if actual.get("allOrNone") is not False and actual.get("allOrNone") != "false":
            return False
        instruments = _rows(actual.get("Instrument"))
        if len(instruments) != 1:
            return False
        instrument = instruments[0]
        desired = expected["Instrument"][0]
        if instrument.get("orderAction") != desired["orderAction"]:
            return False
        if _number(instrument.get("quantity")) != Decimal(desired["quantity"]):
            return False
        return _product_matches(instrument.get("Product"), desired["Product"])
    except (ValueError, KeyError, TypeError):
        return False


def summarize_order(raw: dict[str, Any], account_id: str) -> dict[str, Any]:
    details = _rows(raw.get("OrderDetail"))
    if not details or any(row.get("accountId") != account_id for row in details):
        raise ValueError("E*TRADE order account identity is missing or mismatched")
    statuses = [row.get("status") for row in details]
    known = all(isinstance(status, str) and status in _STATUSES for status in statuses)
    fills: list[dict[str, Any]] = []
    for row in details:
        for instrument in _rows(row.get("Instrument")):
            # Missing quantities and execution prices stay unknown, never zero.
            ordered = _number(instrument.get("orderedQuantity", instrument.get("quantity")))
            filled = _number(instrument.get("filledQuantity"))
            fills.append({
                "ordered_quantity": str(ordered) if ordered is not None else None,
                "filled_quantity": str(filled) if filled is not None else None,
                "average_execution_price": instrument.get("averageExecutionPrice"),
                "order_action": instrument.get("orderAction"),
                "product": instrument.get("Product"),
            })
    return {"order_id": _id(raw.get("orderId")), "statuses": statuses,
            "status_known": known, "terminal": known and all(s in _TERMINAL for s in statuses),
            "fills": fills, "reconciliation_required": True}


class ETradeOrderClient:
    """Account-pinned order adapter; no retries, no production CLI write path."""

    def __init__(
        self, config: AppConfig, access_token: AccessToken, account_key: str, account_id: str,
        *, enabled: bool = False, transport: OrderTransport = order_transport,
        clock: Callable[[], float] = time.time, timeout: float = 20.0,
    ) -> None:
        if not access_token.belongs_to(config):
            raise ETradeError("Token belongs to another E*TRADE login/environment")
        if any(not isinstance(v, str) or not _ACCOUNT.fullmatch(v)
               for v in (account_key, account_id)):
            raise ValueError("Exact E*TRADE account key and ID are required")
        if type(enabled) is not bool or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid E*TRADE order client configuration")
        self._config, self._token = config, access_token
        self._key, self._account_id = account_key, account_id
        self._enabled, self._transport = enabled, transport
        self._clock, self._timeout = clock, timeout
        host = "apisb.etrade.com" if config.environment == "sandbox" else "api.etrade.com"
        self._url = f"https://{host}/v1/accounts/{account_key}/orders"
        self._previews: dict[str, dict[str, Any]] = {}
        self._attempts: set[str] = set()
        self._client_ids: set[str] = set()

    def _write_gate(self) -> None:
        if not self._enabled:
            raise PermissionError("E*TRADE order writes are disabled")

    def _request(
        self, method: str, suffix: str = "", body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._clock()
        if (not math.isfinite(now) or not math.isfinite(self._token.expires_at)
                or not math.isfinite(self._token.last_used_at)
                or now >= min(self._token.expires_at, _midnight_after(self._token.last_used_at))
                or now - self._token.last_used_at >= INACTIVE_AFTER
                or self._token.last_used_at <= 0 or self._token.last_used_at > now):
            raise ETradeError("E*TRADE token expired or inactive; renew/authorize before orders")
        request = signed_request(
            self._url + suffix, self._config, token=self._token.token,
            token_secret=self._token.secret, method=method,
            body=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
            clock=self._clock,
        )
        unknown: dict[str, Any] = {"outcome": "UNKNOWN", "reconciliation_required": True,
                                   "http_status": None, "order_id": None}
        try:
            response = self._transport(request, self._timeout)
        except OSError:
            return unknown
        if not isinstance(response, OrderResponse):
            return unknown
        unknown["http_status"] = response.status
        if response.status != 200:
            if 400 <= response.status < 500 and response.status not in {408, 429}:
                return {**unknown, "outcome": "REJECTED" if method != "GET" else "UNAVAILABLE"}
            return unknown
        try:
            data = json.loads(response.body)
        except (ValueError, UnicodeError):
            return unknown
        if not isinstance(data, dict) or not data or "Error" in data:
            return unknown
        return {**unknown, "outcome": "RESPONSE", "data": data}

    def list_orders(self) -> dict[str, Any]:
        """All pages in the broker's default query window, not a full history guarantee."""
        raw_orders: list[dict[str, Any]] = []
        seen_markers: set[str] = set()
        seen_ids: set[str] = set()
        suffix = "?count=100"
        for _ in range(MAX_ORDER_PAGES):
            result = self._request("GET", suffix)
            if result["outcome"] != "RESPONSE":
                return {**result, "complete": False}
            try:
                data = result["data"]["OrdersResponse"]
                if not isinstance(data, dict):
                    raise ValueError("Malformed orders response")
                if data.get("Messages") or data.get("messages"):
                    raise ValueError("Order query messages require review")
                rows = _rows(data["Order"])
                for row in rows:
                    summary = summarize_order(row, self._account_id)
                    if summary["order_id"] in seen_ids:
                        raise ValueError("Repeated order page")
                    seen_ids.add(summary["order_id"])
                raw_orders.extend(rows)
                next_url = data.get("next")
                marker = data.get("marker")
                if next_url:
                    parsed, pinned = urlsplit(next_url), urlsplit(self._url)
                    if (parsed.scheme, parsed.netloc, parsed.path) != (
                        pinned.scheme, pinned.netloc, pinned.path
                    ) or parsed.fragment or parsed.username or parsed.password:
                        raise ValueError("Unrecognized orders pagination URL")
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if set(query) - {"marker", "count"} or len(query.get("marker", [])) != 1:
                        raise ValueError("Unrecognized orders pagination query")
                    marker = query["marker"][0]
                if not marker:
                    if len(rows) >= 100:
                        raise ValueError("Order query may be truncated")
                    return {"outcome": "OBSERVED", "http_status": 200, "complete": True,
                            "query_scope": "broker_default_window", "raw_orders": raw_orders,
                            "orders": [summarize_order(row, self._account_id)
                                       for row in raw_orders]}
                if not isinstance(marker, str) or not marker or marker in seen_markers or not rows:
                    raise ValueError("Incomplete orders pagination")
                seen_markers.add(marker)
                suffix = "?" + urlencode({"count": 100, "marker": marker})
            except (ValueError, KeyError, TypeError):
                return {"outcome": "UNKNOWN", "http_status": 200, "complete": False,
                        "reconciliation_required": True}
        return {"outcome": "UNKNOWN", "complete": False, "reconciliation_required": True}

    def get_order(self, order_id: str) -> dict[str, Any]:
        current = _id(order_id)
        result = self.list_orders()
        if not result.get("complete"):
            return {**result, "order_id": current}
        matches = [row for row in result["raw_orders"] if _id(row["orderId"]) == current]
        if len(matches) != 1:
            return {"outcome": "UNAVAILABLE", "order_id": current,
                    "reconciliation_required": True, "reason": "not_found_in_query_window"}
        summary = summarize_order(matches[0], self._account_id)
        return {"outcome": "OBSERVED" if summary["status_known"] else "UNKNOWN",
                **summary, "raw_order": matches[0]}

    def _check_replaceable(self, current: str, spec: dict[str, Any], message: str) -> None:
        observed = self.get_order(current)
        if (observed["outcome"] != "OBSERVED" or observed["statuses"] != ["OPEN"]
                or len(observed["fills"]) != 1
                or _number(observed["fills"][0]["filled_quantity"]) != Decimal(0)):
            raise ValueError(message)
        instrument = observed["fills"][0]
        desired = spec["Order"][0]["Instrument"][0]
        if (not _product_matches(instrument.get("product"), desired["Product"])
                or instrument.get("order_action") != desired["orderAction"]):
            raise ValueError("Replacement must retain the original CALL and action")

    def preview_order(
        self, intent: dict[str, Any], client_order_id: str, *, order_id: str | None = None,
    ) -> dict[str, Any]:
        self._write_gate()
        spec = build_limit_call_order(intent, client_order_id)
        current = None if order_id is None else _id(order_id)
        if client_order_id in self._client_ids:
            raise ValueError("clientOrderId already attempted; reconcile before any new attempt")
        if current is not None:
            self._check_replaceable(
                current, spec, "Replacement requires an observed open, unfilled single-leg order")
        self._client_ids.add(client_order_id)
        started_at = self._clock()
        suffix = "/preview" if current is None else f"/{current}/change/preview"
        result = self._request("POST" if current is None else "PUT", suffix,
                               {"PreviewOrderRequest": spec})
        if result["outcome"] != "RESPONSE":
            return result
        try:
            preview = result["data"]["PreviewOrderResponse"]
            ids = _rows(preview.get("PreviewIds"))
            if (preview.get("accountId") != self._account_id or len(ids) != 1
                    or not _matches_response(spec, preview)):
                raise ValueError("Preview identity or terms mismatched")
            preview_id = _id(ids[0]["previewId"])
            # Any message requires a caller's review, so no executable receipt is issued.
            if preview.get("Messages") or preview.get("messageList") or any(
                row.get("Messages") or row.get("messages") for row in _rows(preview.get("Order"))
            ):
                return {"outcome": "REVIEW_REQUIRED", "preview": preview,
                        "reconciliation_required": True, "preview_id": None}
            if preview_id in self._previews:
                raise ValueError("Reused preview ID")
            self._previews[preview_id] = {"spec": deepcopy(spec), "started_at": started_at,
                                          "order_id": current, "digest": _digest(spec)}
        except (ValueError, KeyError, TypeError, AttributeError):
            return {"outcome": "UNKNOWN", "reconciliation_required": True, "preview_id": None}
        return {"outcome": "PREVIEWED", "preview_id": preview_id, "preview": preview,
                "order_id": current, "request_digest": _digest(spec),
                "reconciliation_required": False}

    def place_order(self, preview_id: str) -> dict[str, Any]:
        self._write_gate()
        preview_id = _id(preview_id)
        receipt = self._previews.get(preview_id)
        if receipt is None or preview_id in self._attempts:
            raise ValueError("An unused preview from this exact client/account is required")
        age = self._clock() - receipt["started_at"]
        if not 0 <= age < PREVIEW_LIFETIME:
            raise ValueError("E*TRADE preview has expired; obtain a new preview")
        current = receipt["order_id"]
        # A replacement can fill while being previewed; repeat the observation.
        if current is not None:
            self._check_replaceable(
                current, receipt["spec"],
                "Order changed after preview; reconcile before replacement")
        # Status pagination/transport can consume the remaining preview lifetime.
        if not 0 <= self._clock() - receipt["started_at"] < PREVIEW_LIFETIME:
            raise ValueError("E*TRADE preview has expired; obtain a new preview")
        self._attempts.add(preview_id)  # Consume before dispatch, including timeout/unknown.
        spec = deepcopy(receipt["spec"])
        spec["PreviewIds"] = [{"previewId": int(preview_id)}]
        suffix = "/place" if current is None else f"/{current}/change/place"
        result = self._request("POST" if current is None else "PUT", suffix,
                               {"PlaceOrderRequest": spec})
        if result["outcome"] != "RESPONSE":
            return result
        try:
            placed = result["data"]["PlaceOrderResponse"]
            if placed.get("accountId") != self._account_id or not _matches_response(spec, placed):
                raise ValueError("Placement response identity/terms mismatched")
            ids = _rows(placed.get("OrderIds"))
            if len(ids) != 1:
                raise ValueError("Ambiguous placed order ID")
            placed_id = _id(ids[0]["orderId"])
        except (ValueError, KeyError, TypeError, AttributeError):
            return {"outcome": "UNKNOWN", "reconciliation_required": True, "order_id": None}
        return {"outcome": "ACCEPTED", "order_id": placed_id,
                "reconciliation_required": True, "response": placed}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._write_gate()
        current = _id(order_id)
        attempt = "cancel:" + current
        if attempt in self._attempts:
            raise ValueError("Cancellation already attempted; reconcile before retry")
        self._attempts.add(attempt)
        result = self._request("PUT", "/cancel", {"CancelOrderRequest": {"orderId": int(current)}})
        if result["outcome"] != "RESPONSE":
            return result
        try:
            data = result["data"]["CancelOrderResponse"]
            if data.get("accountId") != self._account_id or _id(data.get("orderId")) != current:
                raise ValueError("Cancellation identity mismatch")
        except (ValueError, KeyError, TypeError, AttributeError):
            return {"outcome": "UNKNOWN", "order_id": current, "reconciliation_required": True}
        return {"outcome": "CANCELLATION_REQUESTED", "order_id": current,
                "reconciliation_required": True, "response": data}
