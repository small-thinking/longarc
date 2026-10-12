"""Small, read-only client for the Schwab account and market data APIs."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Callable, Sequence, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

_HOST = "https://api.schwabapi.com"
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9.]{0,15}\Z")
_ACCOUNT_HASH = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")

Transport = Callable[[Request, float], bytes]


class SchwabError(RuntimeError):
    """A sanitized Schwab transport or HTTP failure."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _safe_transport_error(error: OSError) -> SchwabError:
    if isinstance(error, HTTPError):
        if error.code == 401:
            return SchwabError("Schwab returned HTTP 401; reauthorize the account.")
        return SchwabError(f"Schwab returned HTTP {error.code}.")
    return SchwabError("Schwab request failed; check connectivity and try again.")


def request_bytes(request: Request, timeout: float) -> bytes:
    """Send one request without following redirects or exposing server error text."""
    opener = build_opener(_NoRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            return cast(bytes, response.read())
    except OSError as exc:
        raise _safe_transport_error(exc) from None


def _symbol(value: str) -> str:
    if not _SYMBOL.fullmatch(value):
        raise ValueError("Invalid underlying symbol.")
    return value.upper()


def _date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Transaction dates must be ISO-8601 timestamps.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Transaction dates must include a timezone.")
    return parsed


def _calendar_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Market-hours date must be YYYY-MM-DD.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("Market-hours date must be a valid YYYY-MM-DD date.") from exc


class SchwabReadOnlyClient:
    """Fetch only the Schwab resources needed for local covered-call analysis."""

    def __init__(
        self,
        access_token: str,
        *,
        transport: Transport | None = None,
        timeout: float = 20.0,
    ) -> None:
        if not access_token or access_token != access_token.strip():
            raise ValueError("A non-empty access token is required.")
        if timeout <= 0:
            raise ValueError("Timeout must be positive.")
        self._access_token = access_token
        self._transport = transport or request_bytes
        self._timeout = timeout

    def _get(self, path: str, query: dict[str, str] | None = None) -> object:
        url = f"{_HOST}{path}"
        if query:
            url += "?" + urlencode(query)
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            payload = self._transport(request, self._timeout)
        except OSError as exc:
            raise _safe_transport_error(exc) from None
        try:
            result: object = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            raise SchwabError("Schwab returned invalid JSON.") from None
        return result

    def accounts(self) -> object:
        return self._get("/trader/v1/accounts", {"fields": "positions"})

    def account_numbers(self) -> object:
        return self._get("/trader/v1/accounts/accountNumbers")

    def transactions(self, account_hash: str, start_date: str, end_date: str) -> object:
        if not _ACCOUNT_HASH.fullmatch(account_hash):
            raise ValueError("Invalid account hash.")
        if _date(start_date) > _date(end_date):
            raise ValueError("Transaction start date must be no later than end date.")
        return self._get(
            f"/trader/v1/accounts/{account_hash}/transactions",
            {"startDate": start_date, "endDate": end_date, "types": "TRADE"},
        )

    def orders(self, account_hash: str, start_date: str, end_date: str) -> object:
        if not _ACCOUNT_HASH.fullmatch(account_hash):
            raise ValueError("Invalid account hash.")
        if _date(start_date) > _date(end_date):
            raise ValueError("Order start date must be no later than end date.")
        return self._get(
            f"/trader/v1/accounts/{account_hash}/orders",
            {"fromEnteredTime": start_date, "toEnteredTime": end_date},
        )

    def quotes(self, symbols: Sequence[str]) -> object:
        if isinstance(symbols, str) or not symbols:
            raise ValueError("Provide one or more underlying symbols.")
        names = [_symbol(value) for value in symbols]
        return self._get("/marketdata/v1/quotes", {"symbols": ",".join(names)})

    def option_chain(self, symbol: str) -> object:
        return self._get(
            "/marketdata/v1/chains", {"symbol": _symbol(symbol), "contractType": "CALL"}
        )

    def market_hours(self, day: str) -> object:
        return self._get(
            "/marketdata/v1/markets", {"markets": "equity,option", "date": _calendar_date(day)}
        )
