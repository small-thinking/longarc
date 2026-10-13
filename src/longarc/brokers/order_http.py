"""Single-attempt broker order HTTP transport; never follows redirects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class OrderResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


OrderTransport = Callable[[Request, float], OrderResponse]


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, request: Request, fp: object, code: int, msg: str,
        headers: object, newurl: str,
    ) -> None:
        return None


def order_transport(request: Request, timeout: float) -> OrderResponse:
    """One HTTP attempt; redirects and error bodies are never followed or surfaced."""
    opener = build_opener(_NoRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            return OrderResponse(response.status, dict(response.headers), response.read())
    except HTTPError as error:
        return OrderResponse(error.code, dict(error.headers), b"")


