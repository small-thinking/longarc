"""CLI entrypoint for LongArc."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Callable, cast

from longarc.core.config import load_config
from longarc.core.logging import configure_logging
from longarc.data.providers.local_parquet import download_symbol
from longarc.data.store import read_bars

LOGGER = logging.getLogger(__name__)


def _data_download(args: argparse.Namespace) -> int:
    for symbol in args.symbols:
        result = download_symbol(
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


def _backtest(args: argparse.Namespace) -> int:
    load_config(Path(args.config))
    LOGGER.info("backtest not implemented yet")
    return 0


def _paper_sim_run(args: argparse.Namespace) -> int:
    load_config(Path(args.config))
    LOGGER.info("paper-sim run not implemented yet")
    return 0


def _paper_run(args: argparse.Namespace) -> int:
    load_config(Path(args.config))
    LOGGER.info("paper run not implemented yet")
    return 0


def _report(args: argparse.Namespace) -> int:
    LOGGER.info("report generation not implemented yet for run_id=%s", args.run_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="longarc")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data_parser = subparsers.add_parser("data", help="Data commands")
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)
    data_download = data_subparsers.add_parser("download", help="Download market data")
    data_download.add_argument("--symbols", nargs="+", required=True, help="Ticker symbols")
    data_download.add_argument("--timeframe", default="1d", help="Bar timeframe: 1m, 1h, 1d")
    data_download.add_argument(
        "--start", required=True, help="Inclusive start date, e.g. 2020-01-01"
    )
    data_download.add_argument("--end", required=True, help="Inclusive end date, e.g. 2024-01-01")
    data_download.add_argument("--data-path", default="./data", help="Base path for local data")
    data_download.set_defaults(handler=_data_download)

    data_latest = data_subparsers.add_parser("show-latest", help="Show latest market data")
    data_latest.add_argument("--symbol", required=True, help="Ticker symbol")
    data_latest.add_argument("--timeframe", default="1d", help="Bar timeframe: 1m, 1h, 1d")
    data_latest.add_argument("--data-path", default="./data", help="Base path for local data")
    data_latest.set_defaults(handler=_data_show_latest)

    backtest = subparsers.add_parser("backtest", help="Run backtest")
    backtest.add_argument(
        "--config",
        default="config/config.example.yaml",
        help="Path to config yaml",
    )
    backtest.set_defaults(handler=_backtest)

    paper_sim = subparsers.add_parser("paper-sim", help="Run local paper simulation")
    paper_sim_subparsers = paper_sim.add_subparsers(dest="paper_sim_command", required=True)
    paper_sim_run = paper_sim_subparsers.add_parser("run", help="Run paper simulation loop")
    paper_sim_run.add_argument(
        "--config",
        default="config/config.example.yaml",
        help="Path to config yaml",
    )
    paper_sim_run.set_defaults(handler=_paper_sim_run)

    paper = subparsers.add_parser("paper", help="Run paper trading")
    paper_subparsers = paper.add_subparsers(dest="paper_command", required=True)
    paper_run = paper_subparsers.add_parser("run", help="Run paper trading loop")
    paper_run.add_argument(
        "--config",
        default="config/config.example.yaml",
        help="Path to config yaml",
    )
    paper_run.set_defaults(handler=_paper_run)

    report = subparsers.add_parser("report", help="Generate report")
    report.add_argument("--run-id", required=True, help="Run identifier")
    report.set_defaults(handler=_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    handler = cast(Callable[[argparse.Namespace], int], cast(Any, args).handler)
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
