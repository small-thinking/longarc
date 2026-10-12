"""Pinned, GET-only E*TRADE account and market data client."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Callable, Sequence, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

if TYPE_CHECKING:
    from longarc.etrade.auth import AccessToken, AppConfig

Transport = Callable[[Request, float], bytes]
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9.]{0,15}\Z")
_ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAX_PORTFOLIO_PAGES = 100


def _field(mapping: dict[str, object], lower: str, upper: str) -> object:
    return mapping[lower] if lower in mapping else mapping.get(upper)


class ETradeError(RuntimeError):
    """An E*TRADE error without tokens, account data, or response bodies."""


class ETradeNoContent(ETradeError):
    """The broker explicitly returned HTTP 204 for a resource with no data."""


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


def request_bytes(request: Request, timeout: float) -> bytes:
    """Issue one request and never follow a broker redirect."""
    if request.get_method() != "GET":
        raise ETradeError("Only GET requests are allowed by the E*TRADE adapter.")
    try:
        with build_opener(_NoRedirects()).open(request, timeout=timeout) as response:
            if response.status == 204:
                raise ETradeNoContent("E*TRADE returned HTTP 204 with no data.")
            return cast(bytes, response.read())
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ETradeError("E*TRADE authorization failed; renew or authorize again.") from None
        raise ETradeError(f"E*TRADE returned HTTP {exc.code}.") from None
    except OSError:
        raise ETradeError("E*TRADE request failed; check connectivity.") from None


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise ValueError("Invalid E*TRADE symbol.")
    return value.upper()


def _account(value: str) -> str:
    if not isinstance(value, str) or not _ACCOUNT.fullmatch(value):
        raise ValueError("Invalid E*TRADE account key.")
    return value


class ETradeReadOnlyClient:
    def __init__(
        self,
        config: AppConfig,
        access_token: AccessToken,
        *,
        transport: Transport = request_bytes,
        timeout: float = 20.0,
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        if not access_token.belongs_to(config):
            raise ETradeError("Token belongs to another E*TRADE login; authorize again.")
        if (
            not math.isfinite(access_token.expires_at)
            or not math.isfinite(access_token.last_used_at)
            or access_token.last_used_at <= 0
            or access_token.expires_at <= access_token.last_used_at
        ):
            raise ETradeError("Invalid E*TRADE access token timestamps.")
        if timeout <= 0:
            raise ValueError("Timeout must be positive.")
        self.config = config
        self.token = access_token
        self.transport = transport
        self.timeout = timeout
        self.clock = clock
        self.nonce = nonce

    @property
    def source_label(self) -> str:
        return "etrade_sandbox_synthetic" if self.config.environment == "sandbox" else "etrade_api"

    def _get(self, path: str, query: dict[str, str] | None = None) -> object:
        from longarc.etrade.auth import INACTIVE_AFTER, _midnight_after, signed_request

        now = self.clock()
        if not math.isfinite(now) or now < self.token.last_used_at:
            raise ETradeError("Invalid E*TRADE access token timestamps.")
        if now >= min(self.token.expires_at, _midnight_after(self.token.last_used_at)):
            raise ETradeError("E*TRADE token expired at Eastern midnight; authorize again.")
        if now - self.token.last_used_at >= INACTIVE_AFTER:
            raise ETradeError("E*TRADE token is inactive; renew it before reading data.")
        host = (
            "https://apisb.etrade.com"
            if self.config.environment == "sandbox"
            else "https://api.etrade.com"
        )
        url = host + path + ("?" + urlencode(query) if query else "")
        kwargs: dict[str, object] = {}
        if self.nonce is not None:
            kwargs["nonce"] = self.nonce
        request = signed_request(
            url,
            self.config,
            token=self.token.token,
            token_secret=self.token.secret,
            clock=self.clock,
            **kwargs,  # type: ignore[arg-type]
        )
        try:
            payload = self.transport(request, self.timeout)
        except OSError:
            raise ETradeError("E*TRADE request failed; check connectivity.") from None
        try:
            result: object = json.loads(payload)
        except (ValueError, UnicodeError):
            raise ETradeError("E*TRADE returned invalid JSON.") from None
        self.token = replace(self.token, last_used_at=now)
        return result

    def accounts(self) -> object:
        return self._get("/v1/accounts/list")

    def balance(self, account_key: str) -> object:
        return self._get(
            f"/v1/accounts/{_account(account_key)}/balance",
            {"instType": "BROKERAGE", "realTimeNAV": "true"},
        )

    def portfolio(self, account_key: str) -> list[dict[str, object]]:
        """Return all raw pages; refuse to claim completeness on broken pagination."""
        path = f"/v1/accounts/{_account(account_key)}/portfolio"
        pages: list[dict[str, object]] = []
        seen: set[str] = set()
        account_id: str | None = None
        query: dict[str, str] = {"count": "50"}
        for _ in range(MAX_PORTFOLIO_PAGES):
            try:
                raw = self._get(path, query)
            except ETradeNoContent:
                if pages:
                    raise ETradeError(
                        "E*TRADE portfolio ended before pagination completed."
                    ) from None
                return []
            if not isinstance(raw, dict):
                raise ETradeError("Invalid E*TRADE portfolio page.")
            body = raw.get("PortfolioResponse")
            if not isinstance(body, dict):
                raise ETradeError("Invalid E*TRADE portfolio response.")
            accounts = _field(body, "accountPortfolio", "AccountPortfolio")
            if (
                not isinstance(accounts, list)
                or len(accounts) != 1
                or not isinstance(accounts[0], dict)
            ):
                raise ETradeError("Ambiguous E*TRADE portfolio pagination.")
            page = accounts[0]
            returned_id = _field(page, "accountId", "AccountId")
            if not isinstance(returned_id, str) or not returned_id:
                raise ETradeError("E*TRADE portfolio omitted account identity.")
            if account_id is None:
                account_id = returned_id
            elif returned_id != account_id:
                raise ETradeError("E*TRADE portfolio changed account identity between pages.")
            positions = _field(page, "position", "Position")
            if positions is not None and not isinstance(positions, list):
                raise ETradeError("Invalid E*TRADE portfolio positions.")
            pages.append(raw)
            marker = page.get("marker", body.get("marker"))
            next_page = page.get("nextPageNo")
            total = page.get("totalNoOfPages", page.get("totalPages"))
            current = int(query.get("pageNumber", "1"))
            if total is not None and (
                isinstance(total, bool) or not str(total).isdigit() or int(str(total)) < current
            ):
                raise ETradeError("Invalid E*TRADE total page count.")
            if marker not in (None, ""):
                if (
                    not isinstance(marker, str)
                    or len(marker) > 2048
                    or any(ord(character) < 32 for character in marker)
                ):
                    raise ETradeError("Invalid E*TRADE pagination marker.")
                continuation = "marker:" + marker
                query = {"count": "50", "marker": marker}
            elif next_page not in (None, ""):
                if (
                    isinstance(next_page, bool)
                    or not str(next_page).isdigit()
                    or int(str(next_page)) <= current
                    or (total is not None and int(str(next_page)) > int(str(total)))
                ):
                    raise ETradeError("Invalid E*TRADE next page number.")
                continuation = "page:" + str(next_page)
                query = {"count": "50", "pageNumber": str(next_page)}
            elif total is not None:
                if current >= int(str(total)):
                    return pages
                continuation = "page:" + str(current + 1)
                query = {"count": "50", "pageNumber": str(current + 1)}
            else:
                if marker == "" or next_page == "":
                    return pages
                raise ETradeError("E*TRADE portfolio omitted pagination metadata.")
            if continuation in seen:
                raise ETradeError("E*TRADE portfolio pagination repeated a page.")
            seen.add(continuation)
        raise ETradeError("E*TRADE portfolio exceeded the page limit.")

    def quotes(self, symbols: Sequence[str]) -> object:
        if isinstance(symbols, str) or not symbols or len(symbols) > 25:
            raise ValueError("Provide 1 to 25 E*TRADE symbols.")
        names = [_symbol(symbol) for symbol in symbols]
        return self._get("/v1/market/quote/" + ",".join(names))

    def expiry_dates(self, symbol: str) -> object:
        return self._get(
            "/v1/market/optionexpiredate", {"symbol": _symbol(symbol), "expiryType": "ALL"}
        )

    def option_chains(self, symbol: str, expiry: str) -> object:
        try:
            day = date.fromisoformat(expiry)
        except (TypeError, ValueError):
            raise ValueError("Expiry must be a valid YYYY-MM-DD date.") from None
        if day.isoformat() != expiry:
            raise ValueError("Expiry must be a valid YYYY-MM-DD date.")
        return self._get(
            "/v1/market/optionchains",
            {
                "symbol": _symbol(symbol),
                "expiryYear": str(day.year),
                "expiryMonth": str(day.month),
                "expiryDay": str(day.day),
                "chainType": "CALLPUT",
                "includeWeekly": "true",
                "priceType": "ALL",
            },
        )
