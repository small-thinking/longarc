"""Polygon data provider for OHLCV bar downloads."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from longarc.data.providers.base import DownloadResult
from longarc.data.store import WriteResult, write_bars

_TIMEFRAME_MAP: dict[str, tuple[int, str]] = {
    "1m": (1, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
}


def _timeframe_for(timeframe: str) -> tuple[int, str]:
    try:
        return _TIMEFRAME_MAP[timeframe]
    except KeyError as exc:
        allowed = ", ".join(sorted(_TIMEFRAME_MAP))
        raise ValueError(
            f"Unsupported timeframe {timeframe!r} for polygon provider. Expected one of: {allowed}"
        ) from exc


def _fetch_json(url: str) -> Mapping[str, Any]:
    request = Request(url, headers={"User-Agent": "longarc/0.1.0"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = response.read().decode("utf-8")

    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("Polygon response must be a JSON object.")
    return decoded


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Polygon field {field} must be numeric, got bool")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Polygon field {field} must be numeric, got {value!r}") from exc


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Polygon field {field} must be an integer, got bool")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Polygon field {field} must be an integer, got {value!r}") from exc


class PolygonProvider:
    """Download bars from Polygon aggs endpoint and persist to local parquet."""

    def __init__(
        self,
        api_key: str,
        fetch_json: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Polygon provider requires a non-empty API key.")
        self._api_key = api_key.strip()
        self._fetch_json = fetch_json or _fetch_json

    def _build_url(self, symbol: str, timeframe: str, start: str, end: str) -> str:
        multiplier, timespan = _timeframe_for(timeframe)
        query = urlencode(
            {
                "adjusted": "true",
                "sort": "asc",
                "limit": "50000",
                "apiKey": self._api_key,
            }
        )
        return (
            f"https://api.polygon.io/v2/aggs/ticker/{symbol.upper()}/range/"
            f"{multiplier}/{timespan}/{start}/{end}?{query}"
        )

    def _bars_from_payload(self, payload: Mapping[str, Any]) -> list[dict[str, object]]:
        status = str(payload.get("status", "")).upper()
        if status and status != "OK":
            error = payload.get("error") or payload.get("message") or "unknown_error"
            raise ValueError(f"Polygon request failed with status={status}: {error}")

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("Polygon response field 'results' must be a list.")

        bars: list[dict[str, object]] = []
        for row in raw_results:
            if not isinstance(row, Mapping):
                raise ValueError("Polygon bar row must be an object.")
            millis = _as_int(row.get("t"), "t")
            timestamp = datetime.fromtimestamp(millis / 1000, tz=UTC)
            bars.append(
                {
                    "timestamp": timestamp,
                    "open": _as_float(row.get("o"), "o"),
                    "high": _as_float(row.get("h"), "h"),
                    "low": _as_float(row.get("l"), "l"),
                    "close": _as_float(row.get("c"), "c"),
                    "volume": _as_float(row.get("v"), "v"),
                }
            )
        return bars

    def download_symbol(
        self,
        base_path: str | Path,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
    ) -> DownloadResult:
        url = self._build_url(symbol=symbol, timeframe=timeframe, start=start, end=end)
        payload = self._fetch_json(url)
        bars = self._bars_from_payload(payload)
        result: WriteResult = write_bars(
            base_path=base_path,
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
        )
        return DownloadResult(
            symbol=symbol.upper(),
            timeframe=timeframe,
            input_rows=result.input_rows,
            total_rows=result.total_rows,
        )
