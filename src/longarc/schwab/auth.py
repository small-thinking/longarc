"""Manual browser OAuth, with private local credentials and expiring tokens."""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request

from longarc.schwab.client import SchwabError, request_bytes

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
DEFAULT_REDIRECT = "https://127.0.0.1:8182"
REFRESH_LIFETIME = 7 * 24 * 60 * 60
Transport = Callable[[Request, float], bytes]


def write_private_json(path: Path, payload: object, *, overwrite: bool = True) -> None:
    """Replace a private file atomically; never make its contents world-readable."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".schwab-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2, allow_nan=False)
            stream.write("\n")
        if overwrite:
            os.replace(temporary, path)
        else:
            # Linking fails atomically if another file appeared at the destination.
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_private_json(path: Path) -> dict[str, object]:
    try:
        if path.stat().st_mode & 0o077:
            raise SchwabError("Private Schwab file must have permissions 600.")
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise SchwabError(
            "Cannot read private Schwab file; configure or authorize first."
        ) from None
    if not isinstance(data, dict):
        raise SchwabError("Invalid private Schwab file.")
    return data


def _text(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SchwabError(f"Missing or invalid {name} in private Schwab file.")
    return value.strip()


def _number(data: dict[str, object], name: str) -> float:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchwabError(f"Missing or invalid {name} in private Schwab file.")
    if not (0 < value < float("inf")):
        raise SchwabError(f"Invalid {name} in private Schwab file.")
    return float(value)


@dataclass(frozen=True)
class AppConfig:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    redirect_uri: str = DEFAULT_REDIRECT

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.client_secret.strip():
            raise SchwabError("App ID and secret are required.")
        url = urlsplit(self.redirect_uri)
        if (
            url.scheme != "https"
            or url.hostname != "127.0.0.1"
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise SchwabError("Use a registered HTTPS callback on 127.0.0.1.")
        try:
            url.port
        except ValueError:
            raise SchwabError("Invalid callback port.") from None

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        data = read_private_json(path)
        return cls(
            _text(data, "client_id"), _text(data, "client_secret"), _text(data, "redirect_uri")
        )


class OAuthSession:
    def __init__(
        self,
        directory: Path,
        *,
        transport: Transport = request_bytes,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.config = AppConfig.load(directory / "app.json")
        self.transport = transport
        self.clock = clock

    def start(self) -> str:
        state = secrets.token_urlsafe(32)
        write_private_json(
            self.directory / "pending-auth.json",
            {
                "state": state,
                "created_at": self.clock(),
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
            },
        )
        return (
            AUTHORIZE_URL
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.config.client_id,
                    "redirect_uri": self.config.redirect_uri,
                    "state": state,
                }
            )
        )

    def finish(self, redirect_url: str) -> None:
        pending = read_private_json(self.directory / "pending-auth.json")
        age = self.clock() - _number(pending, "created_at")
        if not 0 <= age <= 15 * 60:
            raise SchwabError("Authorization attempt expired; run auth-start again.")
        if (
            pending.get("client_id") != self.config.client_id
            or pending.get("redirect_uri") != self.config.redirect_uri
        ):
            raise SchwabError("App configuration changed; run auth-start again.")
        returned, expected = urlsplit(redirect_url.strip()), urlsplit(self.config.redirect_uri)
        if (returned.scheme, returned.netloc, returned.path.rstrip("/")) != (
            expected.scheme,
            expected.netloc,
            expected.path.rstrip("/"),
        ) or returned.fragment:
            raise SchwabError("Redirect does not match the registered local callback.")
        query = parse_qs(returned.query, keep_blank_values=True)
        if "error" in query:
            raise SchwabError("Schwab authorization was not granted; run auth-start again.")
        if query.get("state") != [_text(pending, "state")]:
            raise SchwabError("OAuth state did not match; run auth-start again.")
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0]:
            raise SchwabError("Redirect must contain one authorization code.")
        # parse_qs decodes once; urlencode re-encodes for the token request.
        token = self._exchange(
            {
                "grant_type": "authorization_code",
                "code": codes[0],
                "redirect_uri": self.config.redirect_uri,
            }
        )
        self._save_token(token, self.clock())
        (self.directory / "pending-auth.json").unlink()

    def _exchange(self, fields: dict[str, str]) -> dict[str, object]:
        basic = base64.b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode()
        ).decode()
        request = Request(
            TOKEN_URL,
            data=urlencode(fields).encode(),
            method="POST",
            headers={
                "Authorization": "Basic " + basic,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            data = json.loads(self.transport(request, 30))
        except (ValueError, UnicodeError):
            raise SchwabError("Invalid OAuth token response.") from None
        if not isinstance(data, dict):
            raise SchwabError("Invalid OAuth token response.")
        return data

    def _save_token(self, token: dict[str, object], refresh_created_at: float) -> None:
        _text(token, "access_token")
        _text(token, "refresh_token")
        lifetime = _number(token, "expires_in")
        token_type = token.get("token_type", "Bearer")
        if not isinstance(token_type, str) or token_type.lower() != "bearer":
            raise SchwabError("Unexpected OAuth token type.")
        write_private_json(
            self.directory / "token.json",
            {
                "access_token": token["access_token"],
                "refresh_token": token["refresh_token"],
                "expires_at": self.clock() + lifetime,
                "refresh_created_at": refresh_created_at,
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
            },
        )

    def access_token(self) -> str:
        token = read_private_json(self.directory / "token.json")
        if (
            token.get("client_id") != self.config.client_id
            or token.get("redirect_uri") != self.config.redirect_uri
        ):
            raise SchwabError("Token belongs to another app configuration; authorize again.")
        refresh_created_at = _number(token, "refresh_created_at")
        if not 0 <= self.clock() - refresh_created_at < REFRESH_LIFETIME:
            raise SchwabError("Refresh token expired; run auth-start and authorize again.")
        if self.clock() >= _number(token, "expires_at") - 60:
            renewed = self._exchange(
                {"grant_type": "refresh_token", "refresh_token": _text(token, "refresh_token")}
            )
            # Refreshing access does not restart the original seven-day grant.
            renewed.setdefault("refresh_token", _text(token, "refresh_token"))
            self._save_token(renewed, refresh_created_at)
            return _text(renewed, "access_token")
        return _text(token, "access_token")
