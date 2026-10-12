"""Synthetic OAuth fixtures; these tests never contact E*TRADE."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request
from zoneinfo import ZoneInfo

import pytest

from longarc.etrade.auth import AppConfig, OAuthSession, _midnight_after, signed_request
from longarc.etrade.client import ETradeError


def _app(directory: Path, alias: str = "primary") -> None:
    directory.mkdir()
    (directory / "app.json").write_text(
        json.dumps(
            {
                "consumer_key": "key",
                "consumer_secret": "secret",
                "environment": "sandbox",
                "login_alias": alias,
            }
        )
    )
    (directory / "app.json").chmod(0o600)


def _replace(path: Path, **changes: object) -> None:
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data))


def test_login_alias_matches_named_profile_contract() -> None:
    assert (
        AppConfig("key", "secret", "production", "primary-login_2").login_alias == "primary-login_2"
    )
    for alias in ("", "123", "../other", "账户", "a" * 65):
        with pytest.raises(ETradeError, match="alias"):
            AppConfig("key", "secret", "production", alias)


def test_signature_matches_etrade_official_fixture() -> None:
    config = AppConfig(
        "c5bb4dcb7bd6826c7c4340df3f791188",
        "7d30246211192cda43ede3abd9b393b9",
        "production",
        "primary",
    )
    request = signed_request(
        "https://api.etrade.com/v1/accounts/list",
        config,
        token="VbiNYl63EejjlKdQM6FeENzcnrLACrZ2JYD6NQROfVI=",
        token_secret="XCF9RzyQr4UEPloA+WlC06BnTfYC1P0Fwr3GUw/B0Es=",
        clock=lambda: 1344885636,
        nonce=lambda: "0bba225a40d1bbac2430aa0c6163ce44",
    )
    assert 'oauth_signature="UOnPVdzExTAgHkcGWLLfeTaaMSM%3D"' in request.get_header("Authorization")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.etrade.com/v1/accounts/list",
        "https://api.etrade.com.evil.test/v1/accounts/list",
        "https://api.etrade.com:444/v1/accounts/list",
        "https://user@api.etrade.com/v1/accounts/list",
        "https://api.etrade.com/v1/accounts/list#fragment",
    ],
)
def test_signer_rejects_unpinned_destinations(url: str) -> None:
    with pytest.raises(ETradeError, match="Unrecognized"):
        signed_request(url, AppConfig("key", "secret", "sandbox", "primary"))


def test_oob_flow_scope_and_expiry(tmp_path: Path) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    seen: list[str] = []

    def transport(request: object, timeout: float) -> bytes:
        from urllib.request import Request

        assert isinstance(request, Request)
        assert request.get_method() == "GET"
        seen.append(request.full_url)
        if request.full_url.endswith("request_token"):
            assert 'oauth_callback="oob"' in request.get_header("Authorization")
            return b"oauth_token=temp&oauth_token_secret=tempsecret"
        if urlsplit(request.full_url).path == "/oauth/access_token":
            assert 'oauth_verifier="123456"' in request.get_header("Authorization")
            return b"oauth_token=access&oauth_token_secret=accesssecret"
        return b"Access Token has been renewed"

    session = OAuthSession(directory, transport=transport, clock=lambda: 1_700_000_000)
    url = session.start()
    assert urlsplit(url).hostname == "us.etrade.com"
    assert parse_qs(urlsplit(url).query) == {"key": ["key"], "token": ["temp"]}
    token = session.finish("123456")
    assert token.token == "access"
    assert token.belongs_to(session.config)
    assert (directory / "token.json").stat().st_mode & 0o077 == 0
    assert not (directory / "pending-auth.json").exists()
    assert session.renew().token == "access"
    assert len(seen) == 3
    other = AppConfig("key", "secret", "sandbox", "secondary")
    assert not token.belongs_to(other)


def test_request_token_and_profile_guards(tmp_path: Path) -> None:
    directory = tmp_path / "secondary"
    _app(directory, "secondary")
    calls = 0

    def transport(request: object, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        return b"oauth_token=temp&oauth_token_secret=tempsecret"

    session = OAuthSession(directory, transport=transport, clock=lambda: 1_700_000_000)
    session.start()
    pending = json.loads((directory / "pending-auth.json").read_text())
    pending["login_alias"] = "primary"
    (directory / "pending-auth.json").write_text(json.dumps(pending))
    with pytest.raises(ETradeError, match=r"another E\*TRADE login"):
        session.finish("123")
    assert calls == 1
    pending["login_alias"] = "secondary"
    pending["created_at"] -= 301
    (directory / "pending-auth.json").write_text(json.dumps(pending))
    with pytest.raises(ETradeError, match="expired"):
        session.finish("123")
    assert calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("consumer_key", "other-key"),
        ("environment", "production"),
        ("login_alias", "secondary"),
    ],
)
def test_pending_scope_mismatch_never_exchanges(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    calls: list[str] = []

    def transport(request: Request, timeout: float) -> bytes:
        calls.append(request.full_url)
        return b"oauth_token=temp&oauth_token_secret=tempsecret"

    session = OAuthSession(directory, transport=transport, clock=lambda: 1_700_000_000)
    session.start()
    _replace(directory / "pending-auth.json", **{field: value})
    with pytest.raises(ETradeError, match="another E\\*TRADE login"):
        session.finish("123")
    assert len(calls) == 1


@pytest.mark.parametrize("created_at", [0, 1_700_000_001, math.nan, math.inf, -math.inf])
def test_invalid_pending_time_never_exchanges(tmp_path: Path, created_at: float) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    calls = 0

    def transport(request: Request, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        return b"oauth_token=temp&oauth_token_secret=tempsecret"

    session = OAuthSession(directory, transport=transport, clock=lambda: 1_700_000_000)
    session.start()
    _replace(directory / "pending-auth.json", created_at=created_at)
    with pytest.raises(ETradeError, match="expired"):
        session.finish("123")
    assert calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("consumer_key", "other-key"),
        ("environment", "production"),
        ("login_alias", "secondary"),
        ("expires_at", math.nan),
        ("expires_at", math.inf),
        ("expires_at", -1),
        ("last_used_at", math.nan),
        ("last_used_at", math.inf),
        ("last_used_at", -1),
        ("last_used_at", 1_700_000_001),
    ],
)
def test_token_tampering_blocks_renew_without_network(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    calls = 0

    def transport(request: Request, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        return b"renewed"

    token_path = directory / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "consumer_key": "key",
                "environment": "sandbox",
                "login_alias": "primary",
                "oauth_token": "token",
                "oauth_token_secret": "secret",
                "expires_at": 1_700_010_000,
                "last_used_at": 1_700_000_000,
            }
        )
    )
    token_path.chmod(0o600)
    _replace(token_path, **{field: value})
    session = OAuthSession(directory, transport=transport, clock=lambda: 1_700_000_000)
    with pytest.raises(ETradeError):
        session.renew()
    assert calls == 0


@pytest.mark.parametrize("day", ["2026-03-08", "2026-11-01"])
def test_eastern_midnight_across_dst(tmp_path: Path, day: str) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    eastern = ZoneInfo("America/New_York")
    now = datetime.fromisoformat(day + "T12:00:00").replace(tzinfo=eastern).timestamp()
    midnight = _midnight_after(now)
    assert datetime.fromtimestamp(midnight, eastern).date().isoformat() != day
    token_path = directory / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "consumer_key": "key",
                "environment": "sandbox",
                "login_alias": "primary",
                "oauth_token": "token",
                "oauth_token_secret": "secret",
                "expires_at": midnight,
                "last_used_at": now,
            }
        )
    )
    token_path.chmod(0o600)
    assert OAuthSession(directory, clock=lambda: midnight - 1).credentials().token == "token"
    with pytest.raises(ETradeError, match="midnight"):
        OAuthSession(directory, clock=lambda: midnight).credentials()


def test_renew_uses_pinned_oauth_host_and_same_scope(tmp_path: Path) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    now = datetime(2026, 10, 12, 9, tzinfo=ZoneInfo("America/New_York")).timestamp()
    token_path = directory / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "consumer_key": "key",
                "environment": "sandbox",
                "login_alias": "primary",
                "oauth_token": "token",
                "oauth_token_secret": "secret",
                "expires_at": _midnight_after(now),
                "last_used_at": now - 7200,
            }
        )
    )
    token_path.chmod(0o600)
    seen: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        seen.append(request)
        return b"Access Token has been renewed"

    session = OAuthSession(directory, transport=transport, clock=lambda: now)
    renewed = session.renew()
    assert renewed.last_used_at == now
    assert renewed.token == "token"
    assert len(seen) == 1
    assert seen[0].full_url == "https://api.etrade.com/oauth/renew_access_token"
    assert seen[0].get_method() == "GET"
    assert 'oauth_token="token"' in seen[0].get_header("Authorization")


def test_renew_rejects_token_swapped_during_request(tmp_path: Path) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    now = datetime(2026, 10, 12, 9, tzinfo=ZoneInfo("America/New_York")).timestamp()
    token_path = directory / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "consumer_key": "key",
                "environment": "sandbox",
                "login_alias": "primary",
                "oauth_token": "original",
                "oauth_token_secret": "secret",
                "expires_at": _midnight_after(now),
                "last_used_at": now - 7200,
            }
        )
    )
    token_path.chmod(0o600)

    def transport(request: Request, timeout: float) -> bytes:
        _replace(token_path, oauth_token="replacement")
        return b"Access Token has been renewed"

    with pytest.raises(ETradeError, match="changed during renewal"):
        OAuthSession(directory, transport=transport, clock=lambda: now).renew()
    assert json.loads(token_path.read_text())["last_used_at"] == now - 7200


@pytest.mark.parametrize("clock_value", [math.nan, math.inf, -math.inf, -1])
def test_invalid_current_time_cannot_load_token(tmp_path: Path, clock_value: float) -> None:
    directory = tmp_path / "primary"
    _app(directory)
    token_path = directory / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "consumer_key": "key",
                "environment": "sandbox",
                "login_alias": "primary",
                "oauth_token": "token",
                "oauth_token_secret": "secret",
                "expires_at": 1_700_010_000,
                "last_used_at": 1_700_000_000,
            }
        )
    )
    token_path.chmod(0o600)
    with pytest.raises(ETradeError, match="Invalid private"):
        OAuthSession(directory, clock=lambda: clock_value).credentials()
