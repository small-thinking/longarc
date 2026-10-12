"""E*TRADE GET-only routing and completeness tests with synthetic transport."""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from longarc.etrade.auth import AccessToken, AppConfig
from longarc.etrade.client import (
    ETradeError,
    ETradeNoContent,
    ETradeReadOnlyClient,
    _NoRedirects,
    request_bytes,
)


def _client(transport: object, *, environment: str = "sandbox") -> ETradeReadOnlyClient:
    config = AppConfig("key", "secret", environment, "primary")
    token = AccessToken("token", "token-secret", 2000, 1000, "key", environment, "primary")
    return ETradeReadOnlyClient(
        config, token, transport=transport, clock=lambda: 1100, nonce=lambda: "nonce"
    )  # type: ignore[arg-type]


def test_get_routes_and_sandbox_label() -> None:
    seen: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        seen.append(request)
        return b"{}"

    client = _client(transport)
    client.accounts()
    client.balance("AbC123")
    client.quotes(["qqq", "iau"])
    client.expiry_dates("qqq")
    client.option_chains("qqq", "2026-10-16")
    assert client.source_label == "etrade_sandbox_synthetic"
    assert all(request.get_method() == "GET" for request in seen)
    assert all(urlsplit(request.full_url).hostname == "apisb.etrade.com" for request in seen)
    assert [urlsplit(request.full_url).path for request in seen] == [
        "/v1/accounts/list",
        "/v1/accounts/AbC123/balance",
        "/v1/market/quote/QQQ,IAU",
        "/v1/market/optionexpiredate",
        "/v1/market/optionchains",
    ]
    assert parse_qs(urlsplit(seen[-1].full_url).query)["expiryDay"] == ["16"]
    assert all("oauth_signature=" in request.get_header("Authorization") for request in seen)


def test_portfolio_fetches_every_page_and_rejects_loop() -> None:
    seen: list[str] = []

    def transport(request: Request, timeout: float) -> bytes:
        seen.append(request.full_url)
        page = int(parse_qs(urlsplit(request.full_url).query).get("pageNumber", ["1"])[0])
        return json.dumps(
            {
                "PortfolioResponse": {
                    "accountPortfolio": [
                        {
                            "accountId": "numeric-account-1",
                            "position": [{"positionId": page}],
                            "totalNoOfPages": 2,
                            "nextPageNo": "2" if page == 1 else "",
                        }
                    ]
                }
            }
        ).encode()

    pages = _client(transport).portfolio("abc")
    assert len(pages) == 2
    assert len(seen) == 2

    def looping(request: Request, timeout: float) -> bytes:
        return json.dumps({"PortfolioResponse": {"accountPortfolio": [{
            "accountId": "123", "position": [], "marker": "same",
        }]}}).encode()

    with pytest.raises(ETradeError, match="repeated"):
        _client(looping).portfolio("abc")

    def missing(request: Request, timeout: float) -> bytes:
        return json.dumps({"PortfolioResponse": {"accountPortfolio": [{
            "accountId": "123",
            "position": [{} for _ in range(50)],
        }]}}).encode()

    with pytest.raises(ETradeError, match="omitted pagination"):
        _client(missing).portfolio("abc")

    def terminal(request: Request, timeout: float) -> bytes:
        return json.dumps({"PortfolioResponse": {"accountPortfolio": [{
            "accountId": "123",
            "position": [{} for _ in range(50)], "marker": "",
        }]}}).encode()

    assert len(_client(terminal).portfolio("abc")) == 1


def test_portfolio_rejects_account_switch_and_invalid_continuation() -> None:
    def switched(request: Request, timeout: float) -> bytes:
        page = parse_qs(urlsplit(request.full_url).query).get("pageNumber", ["1"])[0]
        return json.dumps({"PortfolioResponse": {"accountPortfolio": [{
            "accountId": "123" if page == "1" else "999", "position": [],
            "totalNoOfPages": 2, "nextPageNo": "2" if page == "1" else "",
        }]}}).encode()

    with pytest.raises(ETradeError, match="changed account identity"):
        _client(switched).portfolio("abc")

    def skipped(request: Request, timeout: float) -> bytes:
        return json.dumps({"PortfolioResponse": {"accountPortfolio": [{
            "accountId": "123", "position": [], "totalNoOfPages": 2, "nextPageNo": "3",
        }]}}).encode()

    with pytest.raises(ETradeError, match="next page"):
        _client(skipped).portfolio("abc")


@pytest.mark.parametrize("status", [302, 401, 429])
def test_transport_sanitizes_http_and_never_retries(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    calls = 0

    class FakeOpener:
        def open(self, request: Request, timeout: float) -> bytes:
            nonlocal calls
            calls += 1
            raise HTTPError(
                request.full_url, status, "secret server message",
                {"Location": "https://untrusted.example/secret"}, None,
            )

    def fake_build_opener(handler: object) -> FakeOpener:
        assert isinstance(handler, _NoRedirects)
        return FakeOpener()

    monkeypatch.setattr("longarc.etrade.client.build_opener", fake_build_opener)
    with pytest.raises(ETradeError) as error:
        request_bytes(Request("https://api.etrade.com/v1/accounts/list", method="GET"), 20)
    assert calls == 1
    assert "secret" not in str(error.value)
    assert "untrusted" not in str(error.value)
    assert str(status) in str(error.value) or status == 401


def test_transport_rejects_non_get_without_opening(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("longarc.etrade.client.build_opener", lambda *args: pytest.fail("opened"))
    with pytest.raises(ETradeError, match="Only GET"):
        request_bytes(Request("https://api.etrade.com/v1/accounts/list", data=b"{}"), 20)


def test_http_204_is_distinct_from_malformed_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, status: int) -> None:
            self.status = status

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b""

    class FakeOpener:
        def __init__(self, status: int) -> None:
            self.status = status

        def open(self, request: Request, timeout: float) -> FakeResponse:
            return FakeResponse(self.status)

    monkeypatch.setattr("longarc.etrade.client.build_opener", lambda *args: FakeOpener(204))
    with pytest.raises(ETradeNoContent):
        request_bytes(Request("https://api.etrade.com/v1/accounts/list", method="GET"), 20)

    def no_content(request: Request, timeout: float) -> bytes:
        raise ETradeNoContent()

    assert _client(no_content).portfolio("abc") == []

    monkeypatch.setattr("longarc.etrade.client.build_opener", lambda *args: FakeOpener(200))
    with pytest.raises(ETradeError, match="invalid JSON"):
        _client(lambda request, timeout: request_bytes(request, timeout)).portfolio("abc")


def test_http_204_after_first_portfolio_page_is_incomplete() -> None:
    calls = 0

    def transport(request: Request, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps({"PortfolioResponse": {"accountPortfolio": [{
                "accountId": "123", "position": [{"positionId": 1}],
                "totalNoOfPages": 2, "nextPageNo": "2",
            }]}}).encode()
        raise ETradeNoContent()

    with pytest.raises(ETradeError, match="before pagination completed"):
        _client(transport).portfolio("abc")
    assert calls == 2


def test_client_updates_in_memory_activity_after_success() -> None:
    calls = 0
    now = 1000.0

    def clock() -> float:
        return now

    def transport(request: Request, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        return b"{}"

    config = AppConfig("key", "secret", "sandbox", "primary")
    token = AccessToken("token", "secret", 20_000, 900, "key", "sandbox", "primary")
    client = ETradeReadOnlyClient(config, token, transport=transport, clock=clock)
    client.accounts()
    now = 8100
    client.accounts()
    assert calls == 2
    assert client.token.last_used_at == 8100


@pytest.mark.parametrize("field,value", [
    ("consumer_key", "other"), ("environment", "production"),
    ("login_alias", "secondary"),
])
def test_client_rejects_token_scope(field: str, value: str) -> None:
    config = AppConfig("key", "secret", "sandbox", "primary")
    fields: dict[str, object] = {
        "token": "token", "secret": "token-secret", "expires_at": 2000,
        "last_used_at": 1000, "consumer_key": "key", "environment": "sandbox",
        "login_alias": "primary",
    }
    fields[field] = value
    token = AccessToken(**fields)  # type: ignore[arg-type]
    with pytest.raises(ETradeError, match="another E\\*TRADE login"):
        ETradeReadOnlyClient(config, token)


@pytest.mark.parametrize("expiry,last_used", [
    (math.nan, 1000), (math.inf, 1000), (-1, 1000),
    (2000, math.nan), (2000, math.inf), (2000, -1), (1000, 1000),
])
def test_client_rejects_bad_timestamps(expiry: float, last_used: float) -> None:
    config = AppConfig("key", "secret", "sandbox", "primary")
    token = AccessToken("token", "secret", expiry, last_used, "key", "sandbox", "primary")
    with pytest.raises(ETradeError, match="timestamps"):
        ETradeReadOnlyClient(config, token)


def test_rejects_invalid_inputs_and_inactive_tokens() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        raise AssertionError("No request should be sent")

    client = _client(transport)
    with pytest.raises(ValueError):
        client.balance("../../orders")
    with pytest.raises(ValueError):
        client.quotes(["QQQ/../../orders"])
    with pytest.raises(ValueError):
        client.option_chains("QQQ", "2026-02-30")
    stale = AccessToken("token", "secret", 10_000, 1000, "key", "sandbox", "primary")
    inactive = ETradeReadOnlyClient(client.config, stale, transport=transport, clock=lambda: 8200)
    with pytest.raises(ETradeError, match="inactive"):
        inactive.accounts()
