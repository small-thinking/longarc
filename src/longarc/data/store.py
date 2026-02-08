"""Parquet-backed storage for OHLCV bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

REQUIRED_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


@dataclass(frozen=True)
class WriteResult:
    input_rows: int
    total_rows: int


def _bar_file(base_path: Path, symbol: str, timeframe: str) -> Path:
    return base_path / symbol.upper() / timeframe / "bars.parquet"


def _to_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"Unsupported timestamp value type: {type(value)}")

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _to_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Field {field} must be numeric, got bool")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Field {field} must be numeric, got {value!r}") from exc


def _normalize_bar(record: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_COLUMNS if field not in record]
    if missing:
        raise ValueError(f"Bar is missing required fields: {missing}")

    return {
        "timestamp": _to_timestamp(record["timestamp"]),
        "open": _to_float(record["open"], "open"),
        "high": _to_float(record["high"], "high"),
        "low": _to_float(record["low"], "low"),
        "close": _to_float(record["close"], "close"),
        "volume": _to_float(record["volume"], "volume"),
    }


def _bars_to_table(bars: list[dict[str, Any]]) -> pa.Table:
    if not bars:
        schema = pa.schema(
            [
                ("timestamp", pa.timestamp("us", tz="UTC")),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("volume", pa.float64()),
            ]
        )
        return pa.Table.from_pydict({field: [] for field in REQUIRED_COLUMNS}, schema=schema)

    return pa.Table.from_pylist(bars)


def read_bars(base_path: str | Path, symbol: str, timeframe: str) -> list[dict[str, Any]]:
    path = _bar_file(Path(base_path), symbol, timeframe)
    if not path.exists():
        return []

    table = pq.read_table(path)
    columns = set(table.column_names)
    missing = [field for field in REQUIRED_COLUMNS if field not in columns]
    if missing:
        raise ValueError(f"Parquet is missing required fields: {missing}")

    rows = table.to_pylist()
    normalized = [_normalize_bar(row) for row in rows]
    normalized.sort(key=lambda row: row["timestamp"])
    return normalized


def write_bars(
    base_path: str | Path,
    symbol: str,
    timeframe: str,
    bars: Sequence[Mapping[str, Any]],
) -> WriteResult:
    base = Path(base_path)
    path = _bar_file(base, symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = read_bars(base, symbol, timeframe)
    incoming = [_normalize_bar(record) for record in bars]

    merged: dict[datetime, dict[str, Any]] = {}
    for row in existing:
        merged[row["timestamp"]] = row
    for row in incoming:
        merged[row["timestamp"]] = row

    ordered = [merged[ts] for ts in sorted(merged)]
    table = _bars_to_table(ordered)
    pq.write_table(table, path)
    return WriteResult(input_rows=len(incoming), total_rows=len(ordered))
