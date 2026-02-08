from __future__ import annotations

from datetime import UTC, datetime

import pytest

from longarc.data.store import read_bars, write_bars


def _bar(ts: str, close: float) -> dict[str, object]:
    timestamp = datetime.fromisoformat(ts).replace(tzinfo=UTC)
    return {
        "timestamp": timestamp,
        "open": close - 0.2,
        "high": close + 0.5,
        "low": close - 0.7,
        "close": close,
        "volume": 1000.0,
    }


def test_write_bars_sorts_and_dedups_by_timestamp(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bars = [
        _bar("2024-01-02T00:00:00", 101.0),
        _bar("2024-01-01T00:00:00", 100.0),
        _bar("2024-01-02T00:00:00", 102.5),
    ]

    result = write_bars(base_path=tmp_path, symbol="AAPL", timeframe="1d", bars=bars)
    stored = read_bars(base_path=tmp_path, symbol="AAPL", timeframe="1d")

    assert result.input_rows == 3
    assert result.total_rows == 2
    assert [row["timestamp"].isoformat() for row in stored] == [
        "2024-01-01T00:00:00+00:00",
        "2024-01-02T00:00:00+00:00",
    ]
    assert stored[-1]["close"] == 102.5


def test_write_bars_is_idempotent_for_same_input(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bars = [_bar("2024-01-01T00:00:00", 100.0), _bar("2024-01-02T00:00:00", 101.0)]

    first = write_bars(base_path=tmp_path, symbol="AAPL", timeframe="1d", bars=bars)
    second = write_bars(base_path=tmp_path, symbol="AAPL", timeframe="1d", bars=bars)
    stored = read_bars(base_path=tmp_path, symbol="AAPL", timeframe="1d")

    assert first.total_rows == 2
    assert second.total_rows == 2
    assert len(stored) == 2


def test_write_bars_rejects_missing_required_fields(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="missing required fields"):
        write_bars(
            base_path=tmp_path,
            symbol="AAPL",
            timeframe="1d",
            bars=[
                {
                    "timestamp": datetime(2024, 1, 1, tzinfo=UTC),
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                }
            ],
        )
