from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from longarc.schwab.client import SchwabError, SchwabReadOnlyClient, _NoRedirects, request_bytes


def test_read_only_endpoints_and_queries() -> None:
    calls: list[tuple[str, str, dict[str, list[str]], str, float]] = []

    def transport(request: Request, timeout: float) -> bytes:
        url = urlsplit(request.full_url)
        calls.append(
            (
                request.get_method(),
                url.path,
                parse_qs(url.query),
                request.get_header("Authorization") or "",
                timeout,
            )
        )
        assert url.scheme == "https"
        assert url.netloc == "api.schwabapi.com"
        return b'{"ok": true}'

    client = SchwabReadOnlyClient("secret-token", transport=transport, timeout=7)
    assert client.accounts() == {"ok": True}
    assert client.account_numbers() == {"ok": True}
    assert client.transactions("abc_DEF-12", "2026-10-01T00:00:00Z", "2026-10-11T00:00:00Z") == {
        "ok": True
    }
    assert client.orders("abc_DEF-12", "2026-10-01T00:00:00Z", "2026-10-11T00:00:00Z") == {
        "ok": True
    }
    assert client.quotes(["qqq", "IAU"]) == {"ok": True}
    assert client.option_chain("qqq") == {"ok": True}
    assert calls == [
        ("GET", "/trader/v1/accounts", {"fields": ["positions"]}, "Bearer secret-token", 7),
        ("GET", "/trader/v1/accounts/accountNumbers", {}, "Bearer secret-token", 7),
        (
            "GET",
            "/trader/v1/accounts/abc_DEF-12/transactions",
            {
                "startDate": ["2026-10-01T00:00:00Z"],
                "endDate": ["2026-10-11T00:00:00Z"],
                "types": ["TRADE"],
            },
            "Bearer secret-token",
            7,
        ),
        (
            "GET",
            "/trader/v1/accounts/abc_DEF-12/orders",
            {
                "fromEnteredTime": ["2026-10-01T00:00:00Z"],
                "toEnteredTime": ["2026-10-11T00:00:00Z"],
            },
            "Bearer secret-token",
            7,
        ),
        ("GET", "/marketdata/v1/quotes", {"symbols": ["QQQ,IAU"]}, "Bearer secret-token", 7),
        (
            "GET",
            "/marketdata/v1/chains",
            {"symbol": ["QQQ"], "contractType": ["CALL"]},
            "Bearer secret-token",
            7,
        ),
    ]


@pytest.mark.parametrize("symbol", ["QQQ/../orders", "QQQ?x=1", "QQQ IAU", ""])
def test_invalid_symbols_are_rejected_before_transport(symbol: str) -> None:
    def unexpected_transport(request: Request, timeout: float) -> bytes:
        pytest.fail("Invalid input reached transport")

    client = SchwabReadOnlyClient("secret-token", transport=unexpected_transport)
    with pytest.raises(ValueError, match="Invalid underlying symbol"):
        client.option_chain(symbol)


def test_invalid_account_hash_and_dates_are_rejected() -> None:
    def unexpected_transport(request: Request, timeout: float) -> bytes:
        pytest.fail("Invalid input reached transport")

    client = SchwabReadOnlyClient("secret-token", transport=unexpected_transport)
    with pytest.raises(ValueError, match="Invalid account hash"):
        client.transactions("abc/../../orders", "2026-10-01T00:00:00Z", "2026-10-11T00:00:00Z")
    with pytest.raises(ValueError, match="timezone"):
        client.transactions("abc", "2026-10-01", "2026-10-11T00:00:00Z")
    with pytest.raises(ValueError, match="start date"):
        client.transactions("abc", "2026-10-11T00:00:00Z", "2026-10-01T00:00:00Z")


def test_errors_do_not_expose_token_or_server_body() -> None:
    def unauthorized(request: Request, timeout: float) -> bytes:
        raise HTTPError(request.full_url, 401, "secret-token private-body", {}, None)

    client = SchwabReadOnlyClient("secret-token", transport=unauthorized)
    with pytest.raises(SchwabError, match="reauthorize") as caught:
        client.accounts()
    assert "secret-token" not in str(caught.value)
    assert "private-body" not in str(caught.value)
    assert "api.schwabapi.com" not in str(caught.value)

    def unavailable(request: Request, timeout: float) -> bytes:
        raise URLError("secret-token private-host")

    client = SchwabReadOnlyClient("secret-token", transport=unavailable)
    with pytest.raises(SchwabError, match="connectivity") as caught:
        client.accounts()
    assert "secret-token" not in str(caught.value)
    assert "private-host" not in str(caught.value)


def test_redirect_handler_refuses_redirect() -> None:
    request = Request("https://api.schwabapi.com/trader/v1/accounts")
    assert (
        _NoRedirects().redirect_request(
            request, None, 302, "Found", {}, "https://other.example/steal"
        )
        is None
    )


def test_request_bytes_sanitizes_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOpener:
        def open(self, request: Request, timeout: float) -> bytes:
            raise HTTPError(request.full_url, 302, "secret redirect", {}, None)

    def fake_build_opener(handler: object) -> FakeOpener:
        assert isinstance(handler, _NoRedirects)
        return FakeOpener()

    monkeypatch.setattr("longarc.schwab.client.build_opener", fake_build_opener)
    request = Request("https://api.schwabapi.com/v1/oauth/token")
    with pytest.raises(SchwabError, match="HTTP 302") as caught:
        request_bytes(request, 20)
    assert "secret redirect" not in str(caught.value)
