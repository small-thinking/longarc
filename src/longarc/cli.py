"""CLI entrypoint for LongArc."""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any, Callable, cast

from longarc.analytics.cli import add_parser as add_calc_parser
from longarc.core.logging import configure_logging
from longarc.data.providers.registry import get_provider
from longarc.data.store import read_bars
from longarc.storage.cli import add_parser

LOGGER = logging.getLogger(__name__)


def _data_download(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get("POLYGON_API_KEY")
    provider = get_provider(args.provider, api_key=api_key)
    for symbol in args.symbols:
        result = provider.download_symbol(
            base_path=args.data_path,
            symbol=symbol,
            timeframe=args.timeframe,
            start=args.start,
            end=args.end,
        )
        LOGGER.info(
            "Downloaded %s %s bars: input_rows=%s total_rows=%s",
            result.symbol,
            result.timeframe,
            result.input_rows,
            result.total_rows,
        )
    return 0


def _data_show_latest(args: argparse.Namespace) -> int:
    bars = read_bars(base_path=args.data_path, symbol=args.symbol, timeframe=args.timeframe)
    if not bars:
        LOGGER.info(
            "No bars found for symbol=%s timeframe=%s in %s",
            args.symbol.upper(),
            args.timeframe,
            args.data_path,
        )
        return 0

    latest = bars[-1]
    LOGGER.info(
        "Latest %s %s bar: timestamp=%s close=%.4f volume=%.2f",
        args.symbol.upper(),
        args.timeframe,
        latest["timestamp"].isoformat(),
        latest["close"],
        latest["volume"],
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="longarc")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data_parser = subparsers.add_parser("data", help="Data commands")
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)
    data_download = data_subparsers.add_parser("download", help="Download market data")
    data_download.add_argument("--symbols", nargs="+", required=True, help="Ticker symbols")
    data_download.add_argument(
        "--provider",
        required=True,
        choices=("local_parquet", "polygon"),
        help="Explicit source: local_parquet generates synthetic test bars; polygon fetches OHLCV",
    )
    data_download.add_argument("--timeframe", default="1d", help="Bar timeframe: 1m, 1h, 1d")
    data_download.add_argument(
        "--start", required=True, help="Inclusive start date, e.g. 2020-01-01"
    )
    data_download.add_argument("--end", required=True, help="Inclusive end date, e.g. 2024-01-01")
    data_download.add_argument(
        "--api-key",
        default="",
        help="Provider API key (or set POLYGON_API_KEY for polygon provider)",
    )
    data_download.add_argument("--data-path", default="./data", help="Base path for local data")
    data_download.set_defaults(handler=_data_download)

    data_latest = data_subparsers.add_parser("show-latest", help="Show latest market data")
    data_latest.add_argument("--symbol", required=True, help="Ticker symbol")
    data_latest.add_argument("--timeframe", default="1d", help="Bar timeframe: 1m, 1h, 1d")
    data_latest.add_argument("--data-path", default="./data", help="Base path for local data")
    data_latest.set_defaults(handler=_data_show_latest)

    add_parser(subparsers)
    add_calc_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    handler = cast(Callable[[argparse.Namespace], int], cast(Any, args).handler)
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
