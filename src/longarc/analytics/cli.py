"""A single JSON request runs the read/calculate/record operation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from longarc.analytics.journal import calculate_and_log
from longarc.storage.store import canonical


def run(args: argparse.Namespace) -> int:
    try:
        result = calculate_and_log(Path(args.db), json.loads(Path(args.file).read_text()))
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        result = {"status": "error", "error": str(exc), "record_id": None}
    print(canonical(result))
    return 1 if result["status"] in ("error", "invalid") else 0


def add_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("calc", help="Calculate and log; no trading or policy approval")
    parser.add_argument("--db", required=True, help="Existing explicit database path")
    parser.add_argument("--file", required=True, help="Calculation request JSON")
    parser.set_defaults(handler=run)
