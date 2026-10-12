"""Synthetic order transports; no live broker access or credentials."""

from __future__ import annotations

import copy
import json
from urllib.request import Request

import pytest

from longarc.schwab.orders import (
    OrderResponse,
    SchwabOrderClient,
    build_limit_call_order,
    order_digest,
    summarize_order,
    validate_order_spec,
)

SYMBOL = "QQQ   261112C00150000"
ACCOUNT = "test_account_hash"
LOCATION = f"https://api.schwabapi.com/trader/v1/accounts/{ACCOUNT}/orders/12345"


def intent() -> dict[str, object]:
    return {"option_symbol": SYMBOL, "instruction": "SELL_TO_OPEN", "quantity": 1,
            "price": "1.25", "tick": "0.05"}


def response(status: int, *, location: str | None = None, body: object = None) -> OrderResponse:
    headers = {"Location": location} if location else {}
    return OrderResponse(status, headers, b"" if body is None else json.dumps(body).encode())


def test_build_validate_digest_and_reject_broad_specs() -> None:
    spec = build_limit_call_order(intent())
    assert spec == {"session": "NORMAL", "duration": "DAY", "orderType": "LIMIT",
                    "price": "1.25", "orderStrategyType": "SINGLE",
                    "orderLegCollection": [{"instruction": "SELL_TO_OPEN", "quantity": 1,
                                            "instrument": {"assetType": "OPTION",
                                                           "symbol": SYMBOL}}]}
    assert validate_order_spec(spec) == spec
    assert order_digest(spec) == order_digest(copy.deepcopy(spec))
    assert len(order_digest(spec)) == 64
    for change in ({"orderType": "MARKET"}, {"duration": "GTC"},
                   {"childOrderStrategies": []}, {"orderStrategyType": "TRIGGER"}):
        invalid = {**spec, **change}
        with pytest.raises(ValueError):
            validate_order_spec(invalid)
    invalid = copy.deepcopy(spec)
    invalid["orderLegCollection"].append(copy.deepcopy(invalid["orderLegCollection"][0]))
    with pytest.raises(ValueError):
        validate_order_spec(invalid)


@pytest.mark.parametrize("change", [
    {"option_symbol": "QQQ   261112P00150000"},
    {"option_symbol": "QQQ   261132C00150000"},
    {"option_symbol": "QQQ   261112C00000000"},
    {"instruction": "BUY_TO_OPEN"}, {"quantity": 0}, {"quantity": True},
    {"quantity": 1.5}, {"price": 1.25}, {"price": True},
    {"price": "NaN"}, {"price": "0.00"}, {"price": "1.251"},
    {"price": "1.23", "tick": "0.05"}, {"tick": "0.001"},
    {"price": "123456789012345.00"},
    {"orderStrategyType": "SINGLE"},
])
def test_invalid_intents_rejected_before_transport(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        build_limit_call_order({**intent(), **change})


def test_default_disabled_and_invalid_spec_do_not_reach_transport() -> None:
    calls: list[Request] = []

    def transport(request: Request, timeout: float) -> OrderResponse:
        calls.append(request)
        return response(201, location=LOCATION)

    spec = build_limit_call_order(intent())
    client = SchwabOrderClient("test-token", ACCOUNT, transport=transport)
    for operation in (lambda: client.preview_order(spec), lambda: client.submit_order(spec),
                      lambda: client.replace_order("12345", spec),
                      lambda: client.cancel_order("12345")):
        with pytest.raises(PermissionError, match="disabled"):
            operation()
    enabled = SchwabOrderClient("test-token", ACCOUNT, enabled=True, transport=transport)
    invalid = {**spec, "duration": "GTC"}
    with pytest.raises(ValueError):
        enabled.submit_order(invalid)
    with pytest.raises(ValueError):
        enabled.cancel_order("12345/../../orders")
    assert calls == []

    read_only = SchwabOrderClient(
        "test-token", ACCOUNT,
        transport=lambda request, timeout: response(
            200, body={"orderId": 12345, "status": "ACCEPTED", "quantity": 1}))
    observed = read_only.get_order("12345")
    assert observed["outcome"] == "OBSERVED"
    assert observed["reconciliation_required"] is True
    assert observed["order"]["filledQuantity"] is None


def test_exact_endpoints_methods_and_empty_201_location() -> None:
    calls: list[tuple[str, str, bytes | None]] = []
    results = [response(200, body={"previewOrderId": "preview-only"}),
               response(201, location=LOCATION), response(201, location=LOCATION),
               response(204), response(200, body={
                   "orderId": 12345, "status": "WORKING", "quantity": 1,
                   "filledQuantity": 0, "remainingQuantity": 1})]

    def transport(request: Request, timeout: float) -> OrderResponse:
        assert request.get_header("Authorization") == "Bearer test-token"
        assert timeout == 7
        calls.append((request.get_method(), request.full_url, request.data))
        return results.pop(0)

    spec = build_limit_call_order(intent())
    client = SchwabOrderClient("test-token", ACCOUNT, enabled=True, transport=transport,
                               timeout=7)
    assert client.preview_order(spec)["outcome"] == "PREVIEWED"
    submitted = client.submit_order(spec)
    assert submitted["outcome"] == "ACCEPTED" and submitted["order_id"] == "12345"
    assert submitted["reconciliation_required"] is True
    assert client.replace_order("12345", spec)["outcome"] == "ACCEPTED"
    assert client.cancel_order("12345")["outcome"] == "CANCELLATION_REQUESTED"
    assert client.get_order("12345")["order"]["status"] == "WORKING"
    base = f"https://api.schwabapi.com/trader/v1/accounts/{ACCOUNT}"
    assert [(method, url) for method, url, _ in calls] == [
        ("POST", f"{base}/previewOrder"), ("POST", f"{base}/orders"),
        ("PUT", f"{base}/orders/12345"), ("DELETE", f"{base}/orders/12345"),
        ("GET", f"{base}/orders/12345")]
    assert calls[1][2] is not None
    assert json.loads(calls[1][2]) == spec
    assert calls[3][2] is None and calls[4][2] is None


def test_submit_malformed_success_rejected_and_unknown_are_not_retried() -> None:
    spec = build_limit_call_order(intent())
    scenarios = [
        (response(201, location=LOCATION.replace("api.schwabapi.com", "evil.example")),
         "UNKNOWN"),
        (response(201, location=LOCATION.replace(ACCOUNT, "other_account")), "UNKNOWN"),
        (response(201, location=LOCATION + "?other=1"), "UNKNOWN"),
        (response(201), "UNKNOWN"), (response(200), "UNKNOWN"),
        (response(400, body={"private": "do not print"}), "REJECTED"),
        (response(503), "UNKNOWN"),
    ]
    for raw, outcome in scenarios:
        count = 0

        def transport(request: Request, timeout: float) -> OrderResponse:
            nonlocal count
            count += 1
            return raw

        client = SchwabOrderClient("test-token", ACCOUNT, enabled=True, transport=transport)
        result = client.submit_order(spec)
        assert result["outcome"] == outcome
        assert count == 1
        assert "private" not in str(result)

    def timeout(request: Request, seconds: float) -> OrderResponse:
        raise TimeoutError("secret endpoint detail")

    client = SchwabOrderClient("test-token", ACCOUNT, enabled=True, transport=timeout)
    assert client.submit_order(spec) == {"outcome": "UNKNOWN", "reconciliation_required": True,
                                         "http_status": None, "order_id": None}


def test_partial_fill_and_cancel_race_remain_observed_not_inferred() -> None:
    activities = [{"activityType": "EXECUTION", "executionLegs": [{"quantity": 1}]}]
    data = {"orderId": 12345, "status": "PENDING_CANCEL", "quantity": 3, "filledQuantity": 1,
            "remainingQuantity": 2, "orderActivityCollection": activities}
    assert summarize_order(data) == {"status": "PENDING_CANCEL", "quantity": 3,
                                     "filledQuantity": 1, "remainingQuantity": 2,
                                     "orderActivityCollection": activities,
                                     "status_known": True, "terminal": False}
    assert summarize_order({"status": "ACCEPTED"})["filledQuantity"] is None
    replies = [response(200), response(200, body=data)]
    client = SchwabOrderClient("test-token", ACCOUNT, enabled=True,
                               transport=lambda request, timeout: replies.pop(0))
    cancel = client.cancel_order("12345")
    observed = client.get_order("12345")
    assert cancel["outcome"] == "CANCELLATION_REQUESTED"
    assert observed["order"]["status"] == "PENDING_CANCEL"
    assert observed["order"]["filledQuantity"] == 1
    assert observed["order"]["remainingQuantity"] == 2
    assert observed["reconciliation_required"] is True
    assert observed["raw_order"] == data


def test_malformed_get_success_requires_reconciliation() -> None:
    client = SchwabOrderClient(
        "test-token", ACCOUNT,
        transport=lambda request, timeout: OrderResponse(200, {}, b"not json"))
    assert client.get_order("12345")["outcome"] == "UNKNOWN"
    empty = SchwabOrderClient(
        "test-token", ACCOUNT,
        transport=lambda request, timeout: response(200, body={}))
    assert empty.get_order("12345")["outcome"] == "UNKNOWN"


@pytest.mark.parametrize("broker_id", [None, 12346, "12346", True, 12345.0])
def test_get_order_requires_exact_broker_id(broker_id: object) -> None:
    body = {"status": "CANCELED", "filledQuantity": 0}
    if broker_id is not None:
        body["orderId"] = broker_id
    client = SchwabOrderClient(
        "test-token", ACCOUNT,
        transport=lambda request, timeout: response(200, body=body))
    result = client.get_order("12345")
    assert result["outcome"] == "UNKNOWN"
    assert result["reconciliation_required"] is True


@pytest.mark.parametrize("status,filled,terminal,clear", [
    ("WORKING", None, False, False),
    ("WORKING", 0, False, False),
    ("PARTIALLY_FILLED", 1, False, False),
    ("FILLED", 1, True, False),
    ("CANCELED", 1, True, False),
    ("CANCELED", 0, True, True),
    ("REJECTED", 0, True, True),
    ("EXPIRED", 0, True, True),
    ("REPLACED", 0, True, False),
])
def test_get_order_status_and_fill_reconciliation(
    status: str, filled: int | None, terminal: bool, clear: bool
) -> None:
    body: dict[str, object] = {"orderId": 12345, "status": status,
                               "orderLegCollection": [{"instruction": "SELL_TO_OPEN"}]}
    if filled is not None:
        body["filledQuantity"] = filled
    client = SchwabOrderClient(
        "test-token", ACCOUNT,
        transport=lambda request, timeout: response(200, body=body))
    result = client.get_order("12345")
    assert result["outcome"] == "OBSERVED"
    assert result["status_known"] is True and result["terminal"] is terminal
    assert result["reconciliation_required"] is (not clear)
    assert result["raw_order"] == body


def test_unknown_broker_status_requires_reconciliation_with_evidence() -> None:
    body = {"orderId": 12345, "status": "BROKER_NEW_STATUS", "filledQuantity": 0}
    client = SchwabOrderClient(
        "test-token", ACCOUNT,
        transport=lambda request, timeout: response(200, body=body))
    result = client.get_order("12345")
    assert result["outcome"] == "UNKNOWN"
    assert result["reconciliation_required"] is True
    assert result["status_known"] is False and result["terminal"] is False
    assert result["raw_order"] == body


def test_default_transport_refuses_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import HTTPError

    from longarc.schwab import orders

    def fake_opener(handler: object) -> object:
        assert isinstance(handler, orders._NoRedirects)

        class Opener:
            def open(self, request: Request, timeout: float) -> OrderResponse:
                raise HTTPError(request.full_url, 302, "redirect", {"Location": LOCATION}, None)

        return Opener()

    monkeypatch.setattr(orders, "build_opener", fake_opener)
    raw = orders.order_transport(Request("https://api.schwabapi.com/test"), 7)
    assert raw.status == 302
    assert raw.body == b""
