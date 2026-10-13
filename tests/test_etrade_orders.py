"""Synthetic order transports only; never contact a broker or use private keys."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any
from urllib.request import Request

import pytest

from longarc.brokers.order_http import OrderResponse
from longarc.etrade.auth import AccessToken, AppConfig, signed_request
from longarc.etrade.client import ETradeError
from longarc.etrade.orders import ETradeOrderClient, build_limit_call_order, summarize_order
from longarc.schwab.orders import build_limit_call_order as schwab_order

INTENT = {"option_symbol": "QQQ   261030C00795000", "instruction": "SELL_TO_OPEN",
          "quantity": 2, "price": "1.25"}
CONFIG = AppConfig("key", "secret", "sandbox", "primary")
TOKEN = AccessToken("token", "token-secret", 10000, 100, "key", "sandbox", "primary")


def response(body: Any, status: int = 200) -> OrderResponse:
    return OrderResponse(status, {}, json.dumps(body).encode())


def preview(spec: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {"PreviewOrderResponse": {**deepcopy(spec), "accountId": "200",
                                     "PreviewIds": [{"previewId": 17}], **changes}}


def placed(spec: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {"PlaceOrderResponse": {**deepcopy(spec), "accountId": "200",
                                   "OrderIds": [{"orderId": 42}], **changes}}


def order(status: str = "OPEN", filled: Any = 0, **changes: Any) -> dict[str, Any]:
    spec = build_limit_call_order(INTENT, "original")
    detail = {**spec["Order"][0], "status": status, "accountId": "200"}
    detail["Instrument"][0].update(filledQuantity=filled, orderedQuantity=2,
                                   averageExecutionPrice="1.20")
    return {"orderId": 42, "orderType": "OPTN", "OrderDetail": [detail], **changes}


class Wire:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> OrderResponse:
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, OSError):
            raise result
        return result


def client(wire: Wire, *, enabled: bool = True, **kwargs: Any) -> ETradeOrderClient:
    return ETradeOrderClient(CONFIG, TOKEN, "account", "200", enabled=enabled,
                             transport=wire, clock=lambda: 100, **kwargs)


def test_shared_intent_maps_limit_price_quantity_and_osi_without_drift() -> None:
    spec = build_limit_call_order(INTENT, "sample01")
    schwab = schwab_order(INTENT)
    assert spec["Order"][0]["limitPrice"] == schwab["price"] == "1.25"
    instrument = spec["Order"][0]["Instrument"][0]
    assert instrument["quantity"] == schwab["orderLegCollection"][0]["quantity"] == 2
    assert instrument["orderAction"] == "SELL_OPEN"
    assert instrument["Product"] == {
        "symbol": "QQQ", "securityType": "OPTN", "callPut": "CALL", "expiryYear": 2026,
        "expiryMonth": 10, "expiryDay": 30, "strikePrice": "795.000",
    }
    assert spec["Order"][0]["priceType"] == "LIMIT"
    assert spec["Order"][0]["orderTerm"] == "GOOD_FOR_DAY"
    assert build_limit_call_order({**INTENT, "instruction": "BUY_TO_CLOSE"}, "sample02")[
        "Order"][0]["Instrument"][0]["orderAction"] == "BUY_CLOSE"


@pytest.mark.parametrize("change", [
    {"instruction": "BUY_TO_OPEN"}, {"quantity": True}, {"quantity": 0}, {"quantity": 1.5},
    {"price": "0"}, {"price": "-1"}, {"price": 1.25}, {"price": "1.001"},
    {"price": "1.23", "tick": "0.05"}, {"option_symbol": "QQQ   261030P00795000"},
    {"option_symbol": "QQQ   260230C00795000"}, {"children": []},
])
def test_both_brokers_reject_broad_or_invalid_intents(change: dict) -> None:
    for builder in (lambda value: build_limit_call_order(value, "test"), schwab_order):
        with pytest.raises(ValueError):
            builder({**INTENT, **change})


@pytest.mark.parametrize("method,signature", [
    ("POST", "neHZFbSA0WRadd3Vmw7AD1tATE8%3D"),
    ("PUT", "%2B4kfeUTJqesxvyXmJejmJk3jGLE%3D"),
])
def test_oauth_signature_includes_method_and_excludes_json_body(
    method: str, signature: str,
) -> None:
    kwargs = dict(token="token", token_secret="token-secret", method=method,
                  clock=lambda: 100, nonce=lambda: "nonce")
    request = signed_request("https://apisb.etrade.com/v1/accounts/account/orders/preview",
                             CONFIG, body=b'{"test":"data"}', **kwargs)
    other = signed_request(request.full_url, CONFIG, body=b'{"different":"data"}', **kwargs)
    assert f'oauth_signature="{signature}"' in request.get_header("Authorization")
    assert request.get_header("Authorization") == other.get_header("Authorization")
    assert request.get_method() == method and request.data == b'{"test":"data"}'
    assert request.get_header("Content-type") == "application/json"


def test_default_disabled_write_gate_and_login_environment_pin_before_transport() -> None:
    wire = Wire()
    adapter = client(wire, enabled=False)
    for operation in (lambda: adapter.preview_order(INTENT, "id"),
                      lambda: adapter.place_order("17"), lambda: adapter.cancel_order("42")):
        with pytest.raises(PermissionError, match="disabled"):
            operation()
    for token in (replace(TOKEN, login_alias="other"), replace(TOKEN, environment="production"),
                  replace(TOKEN, consumer_key="other")):
        with pytest.raises(ETradeError, match="another"):
            ETradeOrderClient(CONFIG, token, "account", "200", transport=wire)
    assert not wire.requests


def test_preview_place_keeps_identical_terms_and_requires_same_client_receipt() -> None:
    spec = build_limit_call_order(INTENT, "unique01")
    wire = Wire(response(preview(spec)), response(placed(spec)))
    adapter = client(wire)
    result = adapter.preview_order(INTENT, "unique01")
    assert result["outcome"] == "PREVIEWED" and result["preview_id"] == "17"
    with pytest.raises(ValueError, match="unused preview"):
        client(Wire()).place_order("17")
    result["preview"]["Order"][0]["limitPrice"] = "0.01"  # Cannot mutate stored intent.
    executed = adapter.place_order("17")
    assert executed["outcome"] == "ACCEPTED" and executed["order_id"] == "42"
    assert executed["reconciliation_required"] is True
    assert [r.get_method() for r in wire.requests] == ["POST", "POST"]
    assert all(r.full_url.startswith("https://apisb.etrade.com/v1/accounts/account/orders/")
               for r in wire.requests)
    submitted = json.loads(wire.requests[1].data)["PlaceOrderRequest"]
    assert submitted.pop("PreviewIds") == [{"previewId": 17}]
    assert submitted == spec
    with pytest.raises(ValueError, match="unused preview"):
        adapter.place_order("17")
    with pytest.raises(ValueError, match="already attempted"):
        adapter.preview_order(INTENT, "unique01")
    assert len(wire.requests) == 2


def test_preview_ttl_consumed_before_timeout_and_no_automatic_retry() -> None:
    now = [100]
    spec = build_limit_call_order(INTENT, "expiry01")
    wire = Wire(response(preview(spec)), TimeoutError("synthetic"))
    adapter = ETradeOrderClient(CONFIG, TOKEN, "account", "200", enabled=True,
                                transport=wire, clock=lambda: now[0])
    adapter.preview_order(INTENT, "expiry01")
    now[0] = 280
    with pytest.raises(ValueError, match="expired"):
        adapter.place_order("17")
    now[0] = 279
    assert adapter.place_order("17")["outcome"] == "UNKNOWN"
    with pytest.raises(ValueError, match="unused preview"):
        adapter.place_order("17")
    assert len(wire.requests) == 2


@pytest.mark.parametrize("change", [
    {"accountId": "other-account"}, {"PreviewIds": []},
    {"PreviewIds": [{"previewId": 0}]}, {"orderType": "EQ"},
    {"Order": []},
])
def test_bad_preview_cannot_be_placed(change: dict) -> None:
    spec = build_limit_call_order(INTENT, "bad01")
    wire = Wire(response(preview(spec, **change)))
    adapter = client(wire)
    assert adapter.preview_order(INTENT, "bad01")["outcome"] == "UNKNOWN"
    with pytest.raises(ValueError):
        adapter.place_order("17")
    assert len(wire.requests) == 1


def test_preview_echo_price_mismatch_and_messages_are_not_usable_receipts() -> None:
    spec = build_limit_call_order(INTENT, "message01")
    altered = preview(spec)
    altered["PreviewOrderResponse"]["Order"][0]["limitPrice"] = "1.30"
    assert client(Wire(response(altered))).preview_order(INTENT, "message01")[
        "outcome"] == "UNKNOWN"
    wire = Wire(response(preview(spec, messageList={"Message": [{"type": "WARNING"}]})))
    adapter = client(wire)
    assert adapter.preview_order(INTENT, "message01")["outcome"] == "REVIEW_REQUIRED"
    with pytest.raises(ValueError):
        adapter.place_order("17")


@pytest.mark.parametrize("status", [302, 408, 429, 500, 503])
def test_uncertain_write_responses_are_not_retried(status: int) -> None:
    wire = Wire(response({}, status))
    result = client(wire).cancel_order("42")
    assert result["outcome"] == "UNKNOWN" and result["reconciliation_required"]
    assert len(wire.requests) == 1


def test_cancel_request_is_not_final_cancel_and_mismatch_is_unknown() -> None:
    wire = Wire(response({"CancelOrderResponse": {"accountId": "200", "orderId": 42}}))
    adapter = client(wire)
    result = adapter.cancel_order("42")
    assert result["outcome"] == "CANCELLATION_REQUESTED" and result["reconciliation_required"]
    assert wire.requests[0].get_method() == "PUT"
    assert json.loads(wire.requests[0].data) == {"CancelOrderRequest": {"orderId": 42}}
    with pytest.raises(ValueError, match="already attempted"):
        adapter.cancel_order("42")
    wire = Wire(response({"CancelOrderResponse": {"accountId": "wrong", "orderId": 42}}))
    assert client(wire).cancel_order("42")["outcome"] == "UNKNOWN"


def test_read_orders_disabled_writes_pagination_and_exact_status() -> None:
    first = order()
    second = order("CANCEL_REQUESTED", orderId=43)
    wire = Wire(response({"OrdersResponse": {"Order": [first], "marker": "page2"}}),
                response({"OrdersResponse": {"Order": second}}))
    result = client(wire, enabled=False).get_order("43")
    assert result["outcome"] == "OBSERVED" and not result["terminal"]
    assert result["reconciliation_required"] and result["statuses"] == ["CANCEL_REQUESTED"]
    assert all(r.get_method() == "GET" and r.data is None for r in wire.requests)
    assert "marker=page2" in wire.requests[1].full_url


@pytest.mark.parametrize("data", [
    {}, {"Order": [], "marker": "more"},
    {"Order": [], "next": "https://evil.example/orders?marker=more"},
    {"Order": [], "next": "https://apisb.etrade.com/v1/accounts/other/orders?marker=more"},
    {"Order": [order(OrderDetail=[{"accountId": "other", "status": "OPEN"}])]},
    {"Order": [order(), order()]},
])
def test_incomplete_or_foreign_order_response_is_unknown(data: dict) -> None:
    wire = Wire(response({"OrdersResponse": data}))
    result = client(wire).list_orders()
    assert result["outcome"] == "UNKNOWN" and not result["complete"]
    assert len(wire.requests) == 1


def test_missing_order_and_missing_fill_are_not_zero_or_cancelled() -> None:
    result = client(Wire(response({"OrdersResponse": {"Order": []}}))).get_order("42")
    assert result["outcome"] == "UNAVAILABLE" and result["reconciliation_required"]
    raw = order(filled=None)
    summary = summarize_order(raw, "200")
    assert summary["fills"][0]["filled_quantity"] is None
    assert not summary["terminal"]
    assert not summarize_order(order("NEW_PROVIDER_STATUS"), "200")["status_known"]


@pytest.mark.parametrize("filled,status", [(1, "OPEN"), (0, "CANCEL_REQUESTED"),
                                            (None, "OPEN"), (2, "EXECUTED")])
def test_uncertain_or_partial_change_stops_before_preview(filled: Any, status: str) -> None:
    wire = Wire(response({"OrdersResponse": {"Order": [order(status, filled)]}}))
    with pytest.raises(ValueError, match="open, unfilled"):
        client(wire).preview_order(INTENT, "change01", order_id="42")
    assert [r.get_method() for r in wire.requests] == ["GET"]


def test_change_preview_and_place_use_put_and_fill_race_stops_place() -> None:
    spec = build_limit_call_order({**INTENT, "price": "1.30"}, "change01")
    raw = order()
    page = {"OrdersResponse": {"Order": [raw]}}
    wire = Wire(response(page), response(preview(spec)), response(page), response(placed(spec)))
    adapter = client(wire)
    assert adapter.preview_order({**INTENT, "price": "1.30"}, "change01", order_id="42")[
        "outcome"] == "PREVIEWED"
    assert adapter.place_order("17")["outcome"] == "ACCEPTED"
    assert [r.get_method() for r in wire.requests] == ["GET", "PUT", "GET", "PUT"]
    assert wire.requests[1].full_url.endswith("/42/change/preview")
    assert wire.requests[3].full_url.endswith("/42/change/place")
    wire = Wire(response(page), response(preview(spec)),
                response({"OrdersResponse": {"Order": [order(filled=1)]}}))
    adapter = client(wire)
    adapter.preview_order({**INTENT, "price": "1.30"}, "change01", order_id="42")
    with pytest.raises(ValueError, match="changed after preview"):
        adapter.place_order("17")
    assert [r.get_method() for r in wire.requests] == ["GET", "PUT", "GET"]


def test_expired_token_and_production_host() -> None:
    wire = Wire()
    adapter = ETradeOrderClient(CONFIG, replace(TOKEN, expires_at=100), "account", "200",
                                transport=wire, clock=lambda: 100)
    with pytest.raises(ETradeError, match="expired"):
        adapter.list_orders()
    assert not wire.requests
    config = replace(CONFIG, environment="production")
    token = replace(TOKEN, environment="production")
    wire = Wire(response({"OrdersResponse": {"Order": []}}))
    ETradeOrderClient(config, token, "account", "200", transport=wire,
                      clock=lambda: 100).list_orders()
    assert wire.requests[0].full_url == "https://api.etrade.com/v1/accounts/account/orders?count=100"


def test_no_content_response_is_not_proof_of_no_open_orders() -> None:
    wire = Wire(OrderResponse(204, {}, b""))
    result = client(wire, enabled=False).list_orders()
    assert result["outcome"] == "UNKNOWN" and result["http_status"] == 204
    assert result["complete"] is False and result["reconciliation_required"] is True


def test_changed_contract_and_preview_expiry_during_status_read_stop_change_place() -> None:
    spec = build_limit_call_order(INTENT, "change01")
    raw = order(filled="0.0")
    page = {"OrdersResponse": {"Order": [raw]}}
    changed = deepcopy(raw)
    changed["OrderDetail"][0]["Instrument"][0]["Product"]["strikePrice"] = "800"
    wire = Wire(response(page), response(preview(spec)),
                response({"OrdersResponse": {"Order": [changed]}}))
    adapter = client(wire)
    assert adapter.preview_order(INTENT, "change01", order_id="42")["outcome"] == "PREVIEWED"
    with pytest.raises(ValueError, match="original CALL"):
        adapter.place_order("17")
    assert len(wire.requests) == 3

    now = [100]

    class SlowStatus(Wire):
        def __call__(self, request: Request, timeout: float) -> OrderResponse:
            result = super().__call__(request, timeout)
            if len(self.requests) == 3:
                now[0] = 280
            return result

    wire = SlowStatus(response(page), response(preview(spec)), response(page))
    adapter = ETradeOrderClient(CONFIG, TOKEN, "account", "200", enabled=True,
                                transport=wire, clock=lambda: now[0])
    adapter.preview_order(INTENT, "change01", order_id="42")
    with pytest.raises(ValueError, match="expired"):
        adapter.place_order("17")
    assert len(wire.requests) == 3


def test_placement_response_account_mismatch_and_midnight_hard_boundary() -> None:
    spec = build_limit_call_order(INTENT, "unique01")
    wire = Wire(response(preview(spec)), response(placed(spec, accountId="other")))
    adapter = client(wire)
    adapter.preview_order(INTENT, "unique01")
    assert adapter.place_order("17")["outcome"] == "UNKNOWN"
    with pytest.raises(ValueError):
        adapter.place_order("17")
    wire = Wire()
    # last_used_at=100 is before Eastern midnight at epoch 18000. A locally
    # extended expires_at cannot override the broker's hard midnight boundary.
    adapter = ETradeOrderClient(CONFIG, replace(TOKEN, expires_at=90000), "account", "200",
                                transport=wire, clock=lambda: 18000)
    with pytest.raises(ETradeError, match="expired"):
        adapter.list_orders()
    assert not wire.requests
