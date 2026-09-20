"""Structured local tools for Codex and human operators."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from longarc import __version__
from longarc.storage import store


def run(args: argparse.Namespace) -> int:
    envelope: dict[str, Any] = {
        "status": "ok",
        "as_of": store.utc_now(),
        "source": "local_sqlite",
        "quality": "operational",
        "warnings": [],
        "evidence_ids": [],
        "policy_hash": None,
        "input_hash": None,
        "code_version": __version__,
    }
    try:
        path = Path(args.db)
        action = args.db_command
        result: Any
        if action == "init":
            result = store.initialize(path)
        elif action == "health":
            result = store.health(path)
        elif action == "save":
            payload = json.loads(Path(args.file).read_text())
            if not isinstance(payload, dict):
                raise ValueError("Observation file must contain a JSON object")
            result = store.save_observation(path, payload)
            envelope.update(
                {
                    k: payload.get(k)
                    for k in ("source", "quality", "evidence_ids", "policy_hash", "code_version")
                }
            )
            envelope["input_hash"] = result["input_hash"]
            envelope["as_of"] = store.get_observation(path, result["record_id"])["observed_at"]
        elif action == "get":
            result = store.get_observation(path, args.id)
            envelope.update(
                {
                    k: result["payload"].get(k)
                    for k in ("source", "quality", "evidence_ids", "policy_hash", "code_version")
                }
            )
            envelope["input_hash"] = result["input_hash"]
            envelope["as_of"] = result["observed_at"]
        elif action == "list":
            result = store.list_observations(path, args.scope, args.limit)
        else:  # backup and restore share the safe, new-destination-only copy operation.
            result = store.copy_database(path, Path(args.to))
        envelope["result"] = result
        if envelope["policy_hash"] is None:
            envelope["warnings"].append("No policy attached; this is not a trading approval")
        print(store.canonical(envelope))
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        envelope.update(status="error", error=str(exc), result=None)
        print(store.canonical(envelope))
        return 1


def add_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("db", help="Local observation journal and recovery tools")
    commands = parser.add_subparsers(dest="db_command", required=True)
    for command in ("init", "health", "save", "get", "list", "backup", "restore"):
        child = commands.add_parser(command)
        child.add_argument(
            "--db", required=True, help="Explicit database path (source for restore)"
        )
        child.set_defaults(handler=run)
        if command == "save":
            child.add_argument("--file", required=True, help="Validated observation JSON file")
        elif command == "get":
            child.add_argument("--id", required=True)
        elif command == "list":
            child.add_argument("--scope", required=True)
            child.add_argument("--limit", default=20, type=int)
        elif command in ("backup", "restore"):
            child.add_argument("--to", required=True, help="New destination; never overwrite")
