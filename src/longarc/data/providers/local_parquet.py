"""Local parquet provider with deterministic synthetic bar generation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from longarc.data.providers.base import DownloadResult
from longarc.data.store import WriteResult, write_bars

_TIMEFRAME_STEP: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}

def _parse_day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _step_for(timeframe: str) -> timedelta:
    try:
        return _TIMEFRAME_STEP[timeframe]
    except KeyError as exc:
        allowed = ", ".join(sorted(_TIMEFRAME_STEP))
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}. Expected one of: {allowed}"
        ) from exc


def generate_synthetic_bars(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
) -> list[dict[str, object]]:
    start_ts = _parse_day(start)
    end_ts = _parse_day(end)
    if end_ts < start_ts:
        raise ValueError("End date must be on or after start date")

    step = _step_for(timeframe)
    seed = sum(ord(char) for char in symbol.upper()) % 25
    bars: list[dict[str, object]] = []

    ts = start_ts
    idx = 0
    while ts <= end_ts:
        close = 100.0 + seed + idx * 0.5
        bars.append(
            {
                "timestamp": ts,
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.7,
                "close": close,
                "volume": 1_000 + idx * 10,
            }
        )
        ts += step
        idx += 1

    return bars


class LocalParquetProvider:
    def download_symbol(
        self,
        base_path: str | Path,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
    ) -> DownloadResult:
        bars = generate_synthetic_bars(symbol=symbol, timeframe=timeframe, start=start, end=end)
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


def download_symbol(
    base_path: str | Path,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
) -> DownloadResult:
    """Backward-compatible function wrapper."""
    provider = LocalParquetProvider()
    return provider.download_symbol(
        base_path=base_path,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
    )
