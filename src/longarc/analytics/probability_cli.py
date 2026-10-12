"""Offline probability estimates and explicit provider comparisons."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from longarc.analytics.probabilities import estimate_probabilities


def run(args: argparse.Namespace) -> int:
    try:
        result = estimate_probabilities(json.loads(Path(args.file).read_text()))
        content = json.dumps(result, allow_nan=False, indent=2) + "\n"
        if args.json_output:
            output = Path(args.json_output)
            output.parent.mkdir(parents=True, exist_ok=True)
            # New files only: protect inputs, credentials and prior comparisons.
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(content)
            print(json.dumps({"status": "ok", "json_output": str(output),
                              "model": result["model"]}))
        else:
            print(content, end="")
        return 0
    except (ValueError, TypeError, OSError, OverflowError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 1


def add_parser(subparsers: Any) -> None:
    command = subparsers.add_parser("probabilities", help="Offline CALL OTM/touch estimates")
    command.add_argument("--file", required=True,
                         help="Explicit model inputs and optional web values")
    command.add_argument("--json-output", help="New output file; never overwrites existing files")
    command.set_defaults(handler=run)
