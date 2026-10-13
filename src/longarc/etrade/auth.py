"""E*TRADE OAuth 1.0a, scoped to one local login profile."""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import Request
from zoneinfo import ZoneInfo

from longarc.core.private_files import PrivateFileError, read_private_json, write_private_json
from longarc.etrade.client import ETradeError, Transport, request_bytes

AUTH_HOST = "https://api.etrade.com"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"
REQUEST_TOKEN_LIFETIME = 5 * 60
INACTIVE_AFTER = 2 * 60 * 60
EASTERN = ZoneInfo("America/New_York")


def _required(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ETradeError(f"Missing or invalid {key} in private E*TRADE file.")
    return value


def _private(path: Path) -> dict[str, object]:
    try:
        return read_private_json(path)
    except PrivateFileError:
        raise ETradeError(
            "Cannot read private E*TRADE file; configure or authorize first."
        ) from None


def _write(path: Path, payload: dict[str, object]) -> None:
    try:
        write_private_json(path, payload)
    except PrivateFileError:
        raise ETradeError("Cannot save private E*TRADE authorization file.") from None


@dataclass(frozen=True)
class AppConfig:
    consumer_key: str = field(repr=False)
    consumer_secret: str = field(repr=False)
    environment: str = "sandbox"
    login_alias: str = ""

    def __post_init__(self) -> None:
        if (
            not self.consumer_key
            or not self.consumer_secret
            or self.consumer_key != self.consumer_key.strip()
            or self.consumer_secret != self.consumer_secret.strip()
        ):
            raise ETradeError("E*TRADE consumer key and secret are required.")
        if self.environment not in ("sandbox", "production"):
            raise ETradeError("E*TRADE environment must be sandbox or production.")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.login_alias):
            raise ETradeError("E*TRADE login alias must be a short ASCII name.")

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        data = _private(path)
        return cls(
            _required(data, "consumer_key"),
            _required(data, "consumer_secret"),
            _required(data, "environment"),
            _required(data, "login_alias"),
        )

    def scope(self) -> dict[str, str]:
        return {
            "consumer_key": self.consumer_key,
            "environment": self.environment,
            "login_alias": self.login_alias,
        }


@dataclass(frozen=True)
class AccessToken:
    token: str = field(repr=False)
    secret: str = field(repr=False)
    expires_at: float
    last_used_at: float
    consumer_key: str = field(repr=False)
    environment: str = "sandbox"
    login_alias: str = ""

    def belongs_to(self, config: AppConfig) -> bool:
        return (
            self.consumer_key == config.consumer_key
            and self.environment == config.environment
            and self.login_alias == config.login_alias
        )


def _encode(value: str) -> str:
    return quote(value, safe="~-._")


def signed_request(
    url: str,
    config: AppConfig,
    *,
    token: str = "",
    token_secret: str = "",
    extras: dict[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    clock: Callable[[], float] = time.time,
    nonce: Callable[[], str] = lambda: secrets.token_hex(16),
) -> Request:
    """Sign a pinned-host OAuth request. JSON body is not a form signature parameter."""
    if method not in {"GET", "POST", "PUT"} or (method == "GET" and body is not None):
        raise ETradeError("Unsupported E*TRADE request method/body.")
    if body is not None and not isinstance(body, bytes):
        raise ETradeError("E*TRADE JSON request body must be bytes.")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"api.etrade.com", "apisb.etrade.com"}
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ETradeError("Unrecognized E*TRADE request URL.")
    oauth = {
        "oauth_consumer_key": config.consumer_key,
        "oauth_nonce": nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(clock())),
    }
    if token:
        oauth["oauth_token"] = token
    if extras:
        oauth.update(extras)
    parameters = parse_qsl(parsed.query, keep_blank_values=True) + list(oauth.items())
    normalized = "&".join(
        f"{_encode(key)}={_encode(value)}"
        for key, value in sorted(parameters, key=lambda item: (_encode(item[0]), _encode(item[1])))
    )
    base_url = f"{parsed.scheme}://{parsed.hostname}{parsed.path or '/'}"
    base = "&".join((method, _encode(base_url), _encode(normalized)))
    key = f"{_encode(config.consumer_secret)}&{_encode(token_secret)}".encode()
    signature = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = signature
    header = "OAuth " + ",".join(
        f'{_encode(name)}="{_encode(value)}"' for name, value in sorted(oauth.items())
    )
    headers = {"Authorization": header, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    return Request(url, data=body, headers=headers, method=method)


def _form_response(payload: bytes) -> dict[str, str]:
    try:
        pairs = parse_qsl(payload.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeError, ValueError):
        raise ETradeError("E*TRADE returned an invalid OAuth response.") from None
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise ETradeError("E*TRADE returned a duplicate OAuth field.")
        values[key] = value
    if not values.get("oauth_token") or not values.get("oauth_token_secret"):
        raise ETradeError("E*TRADE returned incomplete OAuth credentials.")
    return values


def _midnight_after(now: float) -> float:
    local = datetime.fromtimestamp(now, EASTERN)
    following = local.date() + timedelta(days=1)
    return datetime.combine(following, datetime.min.time(), EASTERN).timestamp()


class OAuthSession:
    def __init__(
        self,
        directory: Path,
        *,
        transport: Transport = request_bytes,
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self.directory = directory
        self.config = AppConfig.load(directory / "app.json")
        self.transport = transport
        self.clock = clock
        self.nonce = nonce

    def _request(
        self, path: str, *, token: str = "", secret: str = "", extras: dict[str, str] | None = None
    ) -> bytes:
        request = signed_request(
            AUTH_HOST + path,
            self.config,
            token=token,
            token_secret=secret,
            extras=extras,
            clock=self.clock,
            nonce=self.nonce,
        )
        try:
            return self.transport(request, 30)
        except OSError:
            raise ETradeError("E*TRADE authorization request failed.") from None

    def start(self) -> str:
        response = _form_response(
            self._request("/oauth/request_token", extras={"oauth_callback": "oob"})
        )
        _write(
            self.directory / "pending-auth.json",
            {
                **self.config.scope(),
                "oauth_token": response["oauth_token"],
                "oauth_token_secret": response["oauth_token_secret"],
                "created_at": self.clock(),
            },
        )
        return (
            AUTHORIZE_URL
            + "?"
            + urlencode({"key": self.config.consumer_key, "token": response["oauth_token"]})
        )

    def finish(self, verifier: str) -> AccessToken:
        if (
            not isinstance(verifier, str)
            or not verifier
            or verifier != verifier.strip()
            or len(verifier) > 256
        ):
            raise ETradeError("Enter the E*TRADE verification code.")
        pending = _private(self.directory / "pending-auth.json")
        if any(pending.get(k) != v for k, v in self.config.scope().items()):
            raise ETradeError("Authorization belongs to another E*TRADE login; start again.")
        created = pending.get("created_at")
        if (
            isinstance(created, bool)
            or not isinstance(created, (int, float))
            or not 0 <= self.clock() - created < REQUEST_TOKEN_LIFETIME
        ):
            raise ETradeError("E*TRADE request token expired; start authorization again.")
        response = _form_response(
            self._request(
                "/oauth/access_token",
                token=_required(pending, "oauth_token"),
                secret=_required(pending, "oauth_token_secret"),
                extras={"oauth_verifier": verifier},
            )
        )
        now = self.clock()
        saved = {
            **self.config.scope(),
            "oauth_token": response["oauth_token"],
            "oauth_token_secret": response["oauth_token_secret"],
            "expires_at": _midnight_after(now),
            "last_used_at": now,
        }
        _write(self.directory / "token.json", saved)
        (self.directory / "pending-auth.json").unlink(missing_ok=True)
        return self.credentials()

    def credentials(self) -> AccessToken:
        saved = _private(self.directory / "token.json")
        if any(saved.get(k) != v for k, v in self.config.scope().items()):
            raise ETradeError("Token belongs to another E*TRADE login; authorize again.")
        expiry, last_used = saved.get("expires_at"), saved.get("last_used_at")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in (expiry, last_used)):
            raise ETradeError("Invalid private E*TRADE token file.")
        assert isinstance(expiry, (int, float)) and isinstance(last_used, (int, float))
        now = self.clock()
        if (
            not math.isfinite(now)
            or now <= 0
            or not math.isfinite(expiry)
            or not math.isfinite(last_used)
            or expiry <= 0
            or last_used <= 0
            or last_used > now
            or expiry <= last_used
        ):
            raise ETradeError("Invalid private E*TRADE token file.")
        if now >= min(float(expiry), _midnight_after(float(last_used))):
            raise ETradeError("E*TRADE access token expired at Eastern midnight; authorize again.")
        return AccessToken(
            _required(saved, "oauth_token"),
            _required(saved, "oauth_token_secret"),
            float(expiry),
            float(last_used),
            **self.config.scope(),
        )

    def renew(self) -> AccessToken:
        token = self.credentials()
        self._request("/oauth/renew_access_token", token=token.token, secret=token.secret)
        saved = _private(self.directory / "token.json")
        if (
            any(saved.get(k) != v for k, v in self.config.scope().items())
            or saved.get("oauth_token") != token.token
            or saved.get("oauth_token_secret") != token.secret
        ):
            raise ETradeError("E*TRADE token changed during renewal; authorize again.")
        saved["last_used_at"] = self.clock()
        _write(self.directory / "token.json", saved)
        return self.credentials()
