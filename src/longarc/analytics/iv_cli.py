"""Read-only IV history CLI with protected input paths and explicit scenario units."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from longarc.analytics.iv_history import iv_history
from longarc.storage import store


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    return "\n".join([
        f"# {report['symbol']} saved IV context", "", f"Status: {report['status']}",
        f"As of: {report['as_of']}; source: {report['source']}", "",
        "IV values are annualized-fraction assumptions; percentile/rank are 0–100 values.",
        "Method: " + json.dumps(report["method"], sort_keys=True), "",
        "Coverage: " + json.dumps(report["coverage"], sort_keys=True), "",
        f"Prior usable daily samples: {metrics['historical_samples']} "
        f"(minimum {metrics['minimum_samples']}).",
        f"IV percentile: {metrics['iv_percentile_pct']}; IV rank: {metrics['iv_rank_pct']}.",
        f"Prior median IV: {metrics['historical_median_iv']}.", "",
        "Forecast variance-spread proxy: "
        + json.dumps(metrics["forecast_variance_spread_proxy"]),
        "IV-only sensitivity scenario: " + json.dumps(metrics["iv_only_sensitivity_scenario"]),
        "", "## Selected daily target observations", "",
        "| NY date | Expiry | DTE | Delta | IV raw | IV unit | Blockers | Evidence |",
        "|---|---|---:|---:|---:|---|---|---|",
        *[f"| {p['date']} | {p['contract']['expiry_date']} | {p['dte']} | {p['delta']} | "
          f"{p['snapshot'].get('iv')} | {p['snapshot'].get('iv_unit')} | "
          f"{', '.join(p['blockers']) or 'none'} | {p['record_id']} |"
          for p in report["daily_points"]],
        "", "## Limits", "", *["- " + w for w in report["warnings"]], "",
    ])


def run(args: argparse.Namespace) -> int:
    try:
        path = Path(args.db)
        protected = {path.resolve(), Path(str(path) + "-wal").resolve(),
                     Path(str(path) + "-shm").resolve()}
        if args.expected_dates:
            protected.add(Path(args.expected_dates).resolve())
        destinations = [Path(v).resolve() for v in (args.json_output, args.markdown_output) if v]
        if any(p in protected for p in destinations) or len(set(destinations)) != len(destinations):
            raise ValueError("Report outputs must be distinct and cannot overwrite database/inputs")
        expected = (json.loads(Path(args.expected_dates).read_text())
                    if args.expected_dates else None)
        report = iv_history(
            path, symbol=args.symbol, source=args.source, as_of=args.as_of or store.utc_now(),
            target_dte=args.target_dte, target_delta=args.target_delta,
            dte_tolerance=args.dte_tolerance, delta_tolerance=args.delta_tolerance,
            lookback_samples=args.lookback_samples, min_samples=args.min_samples,
            max_age_seconds=args.max_age_seconds, limit=args.limit, expected_dates=expected,
            forecast_volatility=args.forecast_volatility, reference_iv=args.reference_iv,
            vega_per_vol_point=args.vega_per_vol_point,
            contracts=args.contracts, multiplier=args.multiplier,
        )
        for name, content in ((args.json_output, store.canonical(report) + "\n"),
                              (args.markdown_output, render_markdown(report))):
            if name:
                destination = Path(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content)
        print(store.canonical(report if not destinations else {
            "status": report["status"], "coverage": report["coverage"],
            "metrics": report["metrics"], "json_output": args.json_output,
            "markdown_output": args.markdown_output}))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(store.canonical({"status": "error", "error": str(exc)}))
        return 1


def add_parser(commands: Any) -> None:
    command = commands.add_parser("iv-history",
                                  help="Saved IV distribution and sensitivity scenarios")
    command.add_argument("--db", required=True)
    command.add_argument("--symbol", required=True, help="Explicit uppercase ticker")
    command.add_argument("--source", default="schwab_visible_browser")
    command.add_argument("--as-of", help="Timezone-qualified knowledge cutoff; default now")
    command.add_argument("--target-dte", type=int, default=28)
    command.add_argument("--target-delta", type=float, default=.075)
    command.add_argument("--dte-tolerance", type=int, default=7)
    command.add_argument("--delta-tolerance", type=float, default=.025)
    command.add_argument("--lookback-samples", type=int, default=252,
                         help="Prior observed target dates; not a guaranteed year")
    command.add_argument("--min-samples", type=int, default=30)
    command.add_argument("--max-age-seconds", type=int, default=300)
    command.add_argument("--limit", type=int, default=10000)
    command.add_argument("--expected-dates", help="Explicit exchange-session ISO date JSON array")
    command.add_argument("--forecast-volatility", type=float,
                         help="Explicit same-horizon annualized fraction assumption, e.g. 0.20")
    command.add_argument("--reference-iv", type=float,
                         help="IV-only scenario fraction; otherwise prior median if sufficient")
    command.add_argument("--vega-per-vol-point", type=float,
                         help="Explicit $/share per percentage point; "
                              "never infer provider Vega units")
    command.add_argument("--contracts", type=int, default=1,
                         help="Scenario assumption, not holdings")
    command.add_argument("--multiplier", type=int, default=100, help="Scenario assumption")
    command.add_argument("--json-output")
    command.add_argument("--markdown-output")
    command.set_defaults(handler=run)
