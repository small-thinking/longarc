from __future__ import annotations

import pytest

from longarc import __version__, package_name
from longarc.cli import build_parser, main


def test_package_name() -> None:
    assert package_name() == "longarc"


def test_package_version_is_set() -> None:
    assert __version__ == "0.1.0"


def test_cli_has_expected_top_level_commands() -> None:
    parser = build_parser()
    subparsers_action = next(
        action for action in parser._actions if action.dest == "command"  # noqa: SLF001
    )
    command_names = set(subparsers_action.choices.keys())
    assert command_names == {"data", "db", "calc", "options"}


@pytest.mark.parametrize("command", ["backtest", "paper-sim", "paper", "report"])
def test_retired_commands_fail_instead_of_reporting_success(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_download_requires_explicit_data_source() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["data", "download", "--symbols", "QQQ", "--start", "2024-01-01",
              "--end", "2024-01-02"])
    assert exc.value.code == 2
