from __future__ import annotations

from pathlib import Path

from longarc import __version__, package_name
from longarc.cli import build_parser
from longarc.core.config import AppConfig, load_config


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
    assert {"data", "backtest", "paper-sim", "paper", "report"} <= command_names


def test_example_config_loads() -> None:
    config = load_config(Path("config/config.example.yaml"))
    assert isinstance(config, AppConfig)
    assert config.universe.symbols == ["AAPL"]
