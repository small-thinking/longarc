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
