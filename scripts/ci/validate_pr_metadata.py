#!/usr/bin/env python3
"""Validate pull request metadata from a GitHub event payload."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

PLACEHOLDER_STRINGS = {
    "Describe what changed and why it is needed.",
    "List exact verification steps and expected results.",
    "Fill in what changed and why.",
    "- [ ] Add exact verification steps and outcomes.",
    "_No description provided._",
    "_No test plan provided._",
}


def _extract_section(body: str, heading: str) -> str | None:
    escaped_heading = re.escape(heading)
    pattern = rf"(?ms)^## {escaped_heading}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, body)
    if not match:
        return None
    return match.group(1).strip()


def validate_pr_metadata(title: str, body: str) -> list[str]:
    errors: list[str] = []

    if not title.strip():
        errors.append("PR title must not be empty.")

    description = _extract_section(body, "Description")
    if description is None:
        errors.append("Missing required section: ## Description")
    elif description in PLACEHOLDER_STRINGS:
        errors.append("Section ## Description must be replaced with real content.")
    elif not description.strip():
        errors.append("Section ## Description must not be empty.")

    test_plan = _extract_section(body, "Test Plan")
    if test_plan is None:
        errors.append("Missing required section: ## Test Plan")
    elif test_plan in PLACEHOLDER_STRINGS:
        errors.append("Section ## Test Plan must be replaced with real content.")
    elif not test_plan.strip():
        errors.append("Section ## Test Plan must not be empty.")

    return errors


def _load_event(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("GitHub event payload must be a JSON object.")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PR metadata from event JSON.")
    parser.add_argument(
        "--event-path",
        default="",
        help="Path to GitHub event payload JSON (defaults to $GITHUB_EVENT_PATH).",
    )
    args = parser.parse_args()

    event_path = args.event_path or os.environ["GITHUB_EVENT_PATH"]
    event = _load_event(Path(event_path))
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        print("No pull_request payload found; skipping PR metadata validation.")
        return 0

    title = str(pr.get("title", ""))
    body = str(pr.get("body", "") or "")
    errors = validate_pr_metadata(title, body)
    if errors:
        print("PR metadata validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PR metadata validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
