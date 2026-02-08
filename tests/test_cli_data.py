from __future__ import annotations

from longarc.cli import main
from longarc.data.store import read_bars


def test_data_download_and_show_latest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    data_path = str(tmp_path / "data")

    assert (
        main(
            [
                "--log-level",
                "INFO",
                "data",
                "download",
                "--symbols",
                "AAPL",
                "MSFT",
                "--timeframe",
                "1d",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-03",
                "--data-path",
                data_path,
            ]
        )
        == 0
    )

    assert (
        main(
            [
                "--log-level",
                "INFO",
                "data",
                "show-latest",
                "--symbol",
                "AAPL",
                "--timeframe",
                "1d",
                "--data-path",
                data_path,
            ]
        )
        == 0
    )

    aapl_bars = read_bars(base_path=data_path, symbol="AAPL", timeframe="1d")
    msft_bars = read_bars(base_path=data_path, symbol="MSFT", timeframe="1d")
    assert len(aapl_bars) == 3
    assert len(msft_bars) == 3
    assert aapl_bars[-1]["timestamp"].isoformat() == "2024-01-03T00:00:00+00:00"


def test_data_download_polygon_provider_with_api_key(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    data_path = str(tmp_path / "data")

    def fake_fetch_json(_: str) -> dict[str, object]:
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
                }
            ],
        }

    monkeypatch.setattr("longarc.data.providers.polygon._fetch_json", fake_fetch_json)

    assert (
        main(
            [
                "--log-level",
                "INFO",
                "data",
                "download",
                "--provider",
                "polygon",
                "--api-key",
                "demo-key",
                "--symbols",
                "AAPL",
                "--timeframe",
                "1d",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-01",
                "--data-path",
                data_path,
            ]
        )
        == 0
    )

    aapl_bars = read_bars(base_path=data_path, symbol="AAPL", timeframe="1d")
    assert len(aapl_bars) == 1
    assert aapl_bars[-1]["close"] == 100.5
