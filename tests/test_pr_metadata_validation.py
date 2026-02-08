from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path("scripts/ci/validate_pr_metadata.py")


def _run_with_event(tmp_path: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--event-path", str(event_path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_pr_metadata_validation_passes_for_filled_sections(tmp_path: Path) -> None:
    payload = {
        "pull_request": {
            "title": "docs: enforce PR metadata policy",
            "body": (
                "## Description\n"
                "Add CI enforcement for PR metadata sections.\n\n"
                "## Test Plan\n"
                "- Run pytest\n"
            ),
        }
    }
    result = _run_with_event(tmp_path, payload)
    assert result.returncode == 0
    assert "PR metadata validation passed." in result.stdout


def test_pr_metadata_validation_fails_for_placeholder_sections(tmp_path: Path) -> None:
    payload = {
        "pull_request": {
            "title": "docs: placeholder",
            "body": (
                "## Description\n"
                "Describe what changed and why it is needed.\n\n"
                "## Test Plan\n"
                "List exact verification steps and expected results.\n"
            ),
        }
    }
    result = _run_with_event(tmp_path, payload)
    assert result.returncode == 1
    assert "Section ## Description must be replaced with real content." in result.stdout
    assert "Section ## Test Plan must be replaced with real content." in result.stdout
