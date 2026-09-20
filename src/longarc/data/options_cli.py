"""Explicit local commands for browser captures, sparse history and cost scenarios."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from longarc.analytics.cost_journal import estimate_and_log
from longarc.analytics.decisions import decide_and_log
from longarc.analytics.executions import performance, record_execution
from longarc.analytics.history import build_report, render_markdown
from longarc.data.option_capture import ingest_capture
from longarc.storage import store


def _json(path: str) -> Any:
    return json.loads(Path(path).read_text())


def run(args: argparse.Namespace) -> int:
    try:
        path = Path(args.db)
        if args.options_command == "decide":
            result = decide_and_log(path, _json(args.file), _json(args.policy))
        elif args.options_command == "execution-add":
            result = record_execution(path, _json(args.file))
        elif args.options_command == "performance":
            result = performance(path, args.account, args.mode)
        elif args.options_command == "ingest":
            result = ingest_capture(path, _json(args.file), mode=args.mode,
                                    closed_session=args.closed_session)
        elif args.options_command == "costs":
            result = estimate_and_log(path, _json(args.file), _json(args.fees))
        else:
            report = build_report(
                path, "QQQ", mode=args.mode, start=args.start, end=args.end,
                source=args.source, limit=args.limit,
                expected_dates=_json(args.expected_dates) if args.expected_dates else None,
            )
            for name, content in ((args.json_output, store.canonical(report)),
                                  (args.markdown_output, render_markdown(report))):
                if name:
                    destination = Path(name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(content + "\n")
            result = report if not (args.json_output or args.markdown_output) else {
                "series_count": len(report["series"]), "coverage": report["coverage"],
                "truncated": report["truncated"], "warnings": report["warnings"],
                "json_output": args.json_output, "markdown_output": args.markdown_output,
            }
        print(store.canonical(result))
        return 1 if result.get("status") == "error" else 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(store.canonical({"status": "error", "error": str(exc)}))
        return 1


def add_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "options", help="Capture ingestion, sparse history, cost estimates")
    commands = parser.add_subparsers(dest="options_command", required=True)
    for name in ("ingest", "history", "costs", "decide", "execution-add", "performance"):
        command = commands.add_parser(name)
        command.add_argument("--db", required=True, help="Existing local observation database")
        if name in ("ingest", "costs", "decide", "execution-add"):
            command.add_argument("--file", required=True, help="Capture or cost-request JSON")
        if name in ("ingest", "history"):
            command.add_argument("--mode", choices=("observe", "shadow"), default="observe")
        if name == "decide":
            command.add_argument("--policy", required=True, help="Explicit local policy JSON")
        if name == "performance":
            command.add_argument("--account", required=True, help="Local account alias")
            command.add_argument("--mode", choices=("manual", "shadow"), default="manual")
        if name == "ingest":
            command.add_argument("--closed-session", help="Verified last closed market date")
        if name == "costs":
            command.add_argument("--fees", required=True, help="Dated fee schedule JSON")
        if name == "history":
            command.add_argument("--start", required=True, help="Inclusive New York date")
            command.add_argument("--end", required=True, help="Inclusive New York date")
            command.add_argument("--source", default="schwab_visible_browser")
            command.add_argument("--limit", type=int, default=10000)
            command.add_argument("--expected-dates", help="JSON array of expected sessions")
            command.add_argument("--json-output")
            command.add_argument("--markdown-output")
        command.set_defaults(handler=run)
