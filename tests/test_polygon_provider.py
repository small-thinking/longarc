from __future__ import annotations

from longarc.data.providers.polygon import PolygonProvider
from longarc.data.providers.registry import get_provider
from longarc.data.store import read_bars


def test_polygon_provider_downloads_and_persists(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def fake_fetch(_: str) -> dict[str, object]:
        return {
            "status": "OK",
            "results": [
                {
                    "t": 1704067200000,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.5,
                    "v": 12345,
                },
                {
                    "t": 1704153600000,
                    "o": 100.5,
                    "h": 102.0,
                    "l": 100.0,
                    "c": 101.8,
                    "v": 13000,
                },
            ],
        }

    provider = PolygonProvider(api_key="demo-key", fetch_json=fake_fetch)
    result = provider.download_symbol(
        base_path=tmp_path,
        symbol="AAPL",
        timeframe="1d",
        start="2024-01-01",
        end="2024-01-02",
    )

    stored = read_bars(base_path=tmp_path, symbol="AAPL", timeframe="1d")
    assert result.input_rows == 2
    assert result.total_rows == 2
    assert [row["timestamp"].isoformat() for row in stored] == [
        "2024-01-01T00:00:00+00:00",
        "2024-01-02T00:00:00+00:00",
    ]
    assert stored[-1]["close"] == 101.8


def test_registry_requires_api_key_for_polygon() -> None:
    try:
        get_provider("polygon")
        assert False, "Expected ValueError for missing api key"
    except ValueError as exc:
        assert "requires --api-key or POLYGON_API_KEY" in str(exc)
