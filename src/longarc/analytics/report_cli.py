"""Repeatable read-only analysis commands and human-readable report exports."""

from __future__ import annotations

import argparse
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from longarc.analytics.audit import data_audit
from longarc.analytics.candidates import compare_candidates
from longarc.storage import store

COMMANDS = {"data-audit", "compare-candidates", "position-report"}


def _read(name: str) -> Any:
    return json.loads(Path(name).read_text())


def _money(value: Any) -> str:
    return "unknown" if value is None else f"${Decimal(str(value)) / 1000000:,.4f}"


def render_audit(report: dict[str, Any]) -> str:
    inv = report["inventory"]
    lines = ["# Data inventory and quality", "", f"Integrity: {report['status']}",
             "", "## Whole database (unfiltered)", "",
             f"Business records: {inv['table_rows']['observations']}; "
             f"current: {inv['current_observations']}; "
             f"superseded: {inv['superseded_observations']}.",
             f"Observed range: {inv['first_observed_at']} — {inv['last_observed_at']}.",
             "", "Record kinds: " + json.dumps(inv["by_kind"], sort_keys=True),
             "", "## Selected quote data", "",
             "Filters: " + json.dumps(report["selection"], sort_keys=True), "",
             "Observed capture coverage: " + json.dumps(report["observe_capture_coverage"]), "",
             "| Symbol / mode / source | Batches | Quote rows | Contract identities | "
             "Capture dates | Partial batches | Replay quote prerequisites met |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for g in report["quote_groups"]:
        lines.append(f"| {g['symbol']} / {g['mode']} / {g['source']} | {g['batch_count']} | "
                     f"{g['quote_rows']} | {g['contract_identities']} | "
                     f"{len(g['capture_dates'])} | {g['partial_batches']} | "
                     f"{g['replay_quote_prerequisites_met_rows']} |")
    if not report["quote_groups"]:
        lines.extend(["", "No matching canonical quote batches."])
    for g in report["quote_groups"]:
        lines.extend(["", f"### {g['symbol']} / {g['mode']} / {g['source']}", "",
                      "Capture dates: " + ", ".join(g["capture_dates"]),
                      "Source quote dates: " + (", ".join(g["source_quote_dates"]) or "unknown"),
                      "Missing expected dates: " + (json.dumps(g["missing_capture_dates"])
                          if g["missing_capture_dates"] is not None else "unknown"),
                      f"Confirmed duplicate snapshots: {g['duplicate_source_snapshots']}; "
                      "potential repeats with unusable source times: "
                      f"{g['potential_repeats_with_unusable_source_times']}.",
                      "", "Missing fields (count / valid parsed rows):"])
        lines.extend(f"- {field}: {v['count']} / {v['denominator']}"
                     for field, v in g["field_missing"].items() if v["count"])
        lines.extend(["", "Replay quote blockers: " + json.dumps(g["replay_quote_blockers"]),
                      f"Malformed quotes: {len(g['malformed_quotes'])}.",
                      "", "Batch evidence IDs:"])
        lines.extend(f"- {b['record_id']} — {b['observed_at']} ({b['quote_count']} quotes)"
                     for b in g["batch_evidence"])
    lines.extend(["", "## Execution events and replay revisions", "",
                  "Executions: " + json.dumps(report["execution_events"]),
                  "Replay revisions (not independent trials): "
                  + json.dumps(report["replay_revision_counts"]),
                  f"Malformed batches: {len(report['malformed_batches'])}.", "",
                  "Malformed execution records: "
                  + str(len(report["malformed_execution_records"])) + ".", "",
                  "## Interpretation", "", *["- " + w for w in report["warnings"]]])
    return "\n".join(lines) + "\n"


def render_candidates(report: dict[str, Any]) -> str:
    def percent(value: Any) -> str:
        return "unknown" if value is None else f"{Decimal(str(value)) * 100:.2f}%"

    lines = [f"# {report['symbol']} candidate comparison", "",
             f"Evidence: {report['observation_id']}", f"As of: {report['as_of']}",
             f"Source / mode: {report['source']} / {report['mode']}",
             f"Policy hash: {report['policy_hash']}",
             "Coverage: " + json.dumps(report["coverage"], sort_keys=True),
             "Scenario assumptions: " + json.dumps(report["scenario_assumptions"]),
             f"Fee schedule date: {report['fee_schedule_as_of']}", "",
             "Prices are per underlying share; scenario P&L is for the assumed quantity. "
             "Every entry eligibility remains unknown.", "",
             "| Expiry | Strike | Bid / ask | Delta | DTE | Strike distance | "
             "Spread / midpoint | Touch / OTM | Quote thresholds | 50% scenario net |",
             "|---|---:|---|---:|---:|---:|---:|---|---|---:|"]
    for row in report["candidates"]:
        c, s, m = row["contract"], row["snapshot"], row["metrics"]
        probs = row["provider_probabilities"]
        scenario = row["half_premium_buyback_scenario"]
        net = scenario["metrics"]["net_option_pnl_u"] if scenario else None
        thresholds = "pass" if row["quote_policy_thresholds_pass"] else "failed / unknown"
        lines.append(f"| {c['expiry_date']} | {_money(c['strike_u'])} | "
                     f"{_money(s.get('bid_u'))} / {_money(s.get('ask_u'))} | "
                     f"{m.get('observed_delta') or 'unknown'} | {m['dte_calendar_days']} | "
                     f"{percent(m['strike_distance_fraction'])} | "
                     f"{percent(m['spread_fraction_of_midpoint'])} | "
                     f"{percent(probs['probability_touch'])} / "
                     f"{percent(probs['probability_otm'])} | {thresholds} | {_money(net)} |")
    for row in report["candidates"]:
        c = row["contract"]
        lines.extend(["", f"## {c['expiry_date']} / {_money(c['strike_u'])}", "",
                      "Quote checks: " + json.dumps(row["quote_policy_checks"]),
                      "Source timing: " + json.dumps(row["timing"]),
                      "Unknown entry checks: " + ", ".join(row["unknown_entry_checks"]),
                      "Warnings: " + "; ".join(row["warnings"])])
        scenario = row["half_premium_buyback_scenario"]
        if scenario:
            metrics = scenario["metrics"]
            lines.append("50% premium scenario: gross "
                         + _money(metrics["gross_option_pnl_u"]) + "; known-cost subtotal "
                         + _money(metrics["known_cost_net_option_pnl_u"]) + "; complete net "
                         + _money(metrics["net_option_pnl_u"]) + ". "
                         "A subtotal with unknown charges is not complete net income.")
            lines.append("Scenario warnings: " + "; ".join(scenario["warnings"]))
    lines.extend(["", "## Limitations", "", *["- " + w for w in report["warnings"]]])
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    try:
        path = Path(args.db)
        protected = {path.resolve(), Path(str(path) + "-wal").resolve(),
                     Path(str(path) + "-shm").resolve()}
        protected.update(Path(value).resolve() for field in ("policy", "fees", "expected_dates")
                         if (value := getattr(args, field, None)))
        destinations = [Path(value).resolve() for value in
                        (args.json_output, args.markdown_output) if value]
        if any(p in protected for p in destinations) or len(set(destinations)) != len(destinations):
            raise ValueError("Report outputs must be distinct and cannot overwrite database/inputs")
        if args.options_command == "data-audit":
            report = data_audit(path, symbol=args.symbol, start=args.start, end=args.end,
                                expected_dates=_read(args.expected_dates)
                                if args.expected_dates else None,
                                max_age_seconds=args.max_age_seconds, limit=args.limit)
            markdown = render_audit(report)
        elif args.options_command == "compare-candidates":
            report = compare_candidates(path, observation_id=args.observation_id,
                                        as_of=args.as_of or store.utc_now(),
                                        policy=_read(args.policy),
                                        fees=_read(args.fees), contracts=args.contracts,
                                        multiplier=args.multiplier)
            markdown = render_candidates(report)
        else:
            from longarc.analytics.positions import position_report, render_markdown

            report = position_report(path, account=args.account, symbol=args.symbol,
                                     as_of=args.as_of or store.utc_now(), fees=_read(args.fees),
                                     mode=args.mode, max_age_seconds=args.max_age_seconds,
                                     limit=args.limit)
            markdown = render_markdown(report)
        encoded = store.canonical(report)
        for name, content in ((args.json_output, encoded + "\n"),
                              (args.markdown_output, markdown)):
            if name:
                destination = Path(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content)
        print(encoded if not destinations else store.canonical({
            "status": report.get("status", "ok"), "json_output": args.json_output,
            "markdown_output": args.markdown_output}))
        return 1 if report.get("status") == "error" else 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(store.canonical({"status": "error", "error": str(exc)}))
        return 1


def add_parsers(commands: Any) -> None:
    for name in sorted(COMMANDS):
        command = commands.add_parser(name, help="Read-only " + name.replace("-", " "))
        command.add_argument("--db", required=True)
        command.add_argument("--json-output")
        command.add_argument("--markdown-output")
        if name != "compare-candidates":
            command.add_argument("--symbol", help="Explicit uppercase symbol; omit for all")
            command.add_argument("--max-age-seconds", type=int, default=300,
                                 help="Freshness diagnostic limit (default: 300), not policy")
        if name == "data-audit":
            command.add_argument("--start", help="Inclusive New York collection date")
            command.add_argument("--end", help="Inclusive New York collection date")
            command.add_argument("--limit", type=int, default=10000)
            command.add_argument("--expected-dates", help="JSON array of expected session dates")
        else:
            command.add_argument("--as-of", help="ISO timestamp with timezone; default: now")
            command.add_argument("--fees", required=True, help="Dated fee assumptions JSON")
        if name == "compare-candidates":
            command.add_argument("--observation-id", required=True, help="One canonical chain ID")
            command.add_argument("--policy", required=True)
            command.add_argument("--contracts", type=int, default=1,
                                 help="Hypothetical quantity; not inferred coverage")
            command.add_argument("--multiplier", type=int, default=100,
                                 help="Explicit scenario assumption, not verified contract terms")
        if name == "position-report":
            command.add_argument("--account", required=True, help="Local execution ledger alias")
            command.add_argument("--mode", choices=("manual", "shadow"), default="manual")
            command.add_argument("--limit", type=int, default=10000,
                                 help="Maximum selected account/quote records; overflow fails")
        command.set_defaults(handler=run)
