"""Named plans remain offline; explicit order observations use disabled GET clients."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from longarc.brokers import cli, order_cli
from longarc.brokers.profiles import BrokerProfile, save_profiles
from longarc.cli import main
from longarc.core.private_files import read_private_json, write_private_json
from longarc.etrade.auth import AccessToken, AppConfig

INTENT = {"option_symbol": "QQQ   261030C00795000", "instruction": "SELL_TO_OPEN",
          "quantity": 2, "price": "1.25"}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    profiles = {
        "primary": BrokerProfile("primary", "etrade", "primary", "production",
                                  "private/primary", "key-one", "200"),
        "secondary": BrokerProfile("secondary", "etrade", "secondary", "production",
                                    "private/secondary", "key-two", "300"),
        "unbound": BrokerProfile("unbound", "etrade", "secondary", "production",
                                  "private/unbound"),
    }
    save_profiles(Path("private/brokers.json"), profiles)
    write_private_json(Path("private/intent.json"), INTENT)
    return tmp_path


def forbid_session(profile: BrokerProfile) -> None:
    pytest.fail("Offline plan or invalid inputs must not load OAuth/session")


@pytest.mark.parametrize("operation", ["preview", "place", "change-preview", "change-place",
                                      "cancel"])
def test_all_write_operation_plans_offline_without_app_or_token(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    monkeypatch.setattr(cli, "_session", forbid_session)
    args = ["broker", "--profile", "primary", "order-plan", "--operation", operation,
            "--output", f"private/{operation}.json"]
    if operation in {"cancel", "change-preview", "change-place"}:
        args += ["--order-id", "42"]
    if operation != "cancel":
        args += ["--file", "private/intent.json", "--client-order-id", "unique01"]
    assert main(args) == 0
    plan = read_private_json(workspace / f"private/{operation}.json")
    assert plan["identity"]["account_key"] == "key-one"
    assert plan["network_request_made"] is False and plan["live_write_available"] is False
    assert plan["requires_fresh_preflight"] is True
    assert (workspace / f"private/{operation}.json").stat().st_mode & 0o077 == 0
    assert main(args) == 1  # Evidence cannot overwrite a prior plan.


def test_plan_digest_binds_login_account_and_limit_price(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_session", forbid_session)
    digests = []
    for profile in ("primary", "secondary"):
        assert main(["broker", "--profile", profile, "order-plan", "--file", "private/intent.json",
                     "--client-order-id", "unique01", "--output", f"private/{profile}.json"]) == 0
        plan = read_private_json(workspace / f"private/{profile}.json")
        digests.append(plan["request_digest"])
    assert digests[0] != digests[1]
    write_private_json(Path("private/intent.json"), {**INTENT, "price": "1.30"})
    assert main(["broker", "--profile", "primary", "order-plan", "--file", "private/intent.json",
                 "--client-order-id", "unique01", "--output", "private/changed.json"]) == 0
    assert read_private_json(Path("private/changed.json"))["request_digest"] != digests[0]


@pytest.mark.parametrize("profile,output,order_id", [
    ("unbound", "private/result.json", "42"), ("missing", "private/result.json", "42"),
    ("primary", "outside.json", "42"), ("primary", "private/token.json", "42"),
    ("primary", "private/result.json", "../42"),
])
def test_invalid_read_or_binding_stops_before_oauth(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, profile: str, output: str, order_id: str,
) -> None:
    monkeypatch.setattr(cli, "_session", forbid_session)
    assert main(["broker", "--profile", profile, "order-status", "--order-id", order_id,
                 "--output", output]) == 1
    assert not (workspace / output).exists()


def test_order_read_reuses_profile_session_saves_unknown_and_keeps_writes_disabled(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = []
    config = AppConfig("key", "secret", "production", "secondary")
    token = AccessToken("token", "secret", 10000, 100, "key", "production", "secondary")

    def session(profile: BrokerProfile) -> SimpleNamespace:
        selected.append(profile.name)
        return SimpleNamespace(config=config, renew=lambda: token)

    class Client:
        def __init__(self, cfg: AppConfig, credential: AccessToken, key: str, account_id: str,
                     *, enabled: bool = False) -> None:
            assert cfg is config and credential is token
            assert key == "key-two" and account_id == "300" and enabled is False

        def get_order(self, order_id: str) -> dict:
            assert order_id == "42"
            return {"outcome": "UNKNOWN", "reconciliation_required": True}

    monkeypatch.setattr(cli, "_session", session)
    monkeypatch.setattr(order_cli, "ETradeOrderClient", Client)
    assert main(["broker", "--profile", "secondary", "order-status", "--order-id", "42",
                 "--output", "private/status.json"]) == 1
    saved = json.loads((workspace / "private/status.json").read_text())
    assert saved["request"]["login_alias"] == "secondary" and selected == ["secondary"]
    assert saved["order_writes_enabled"] is False
    assert saved["data"]["outcome"] == "UNKNOWN"
