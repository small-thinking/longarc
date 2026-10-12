"""Named-account routing must fail closed before sending provider traffic."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from longarc.brokers import cli
from longarc.brokers.profiles import BrokerProfile, load_profiles, private_path, save_profiles
from longarc.brokers.snapshots import normalize_positions
from longarc.cli import main
from longarc.core.private_files import write_private_json


@pytest.fixture
def profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, BrokerProfile]:
    monkeypatch.chdir(tmp_path)
    result = {}
    for name, broker, login, environment, key, identifier in (
        ("primary", "etrade", "primary", "production", "primary-key", "200"),
        ("secondary", "etrade", "secondary", "production", "secondary-key", "300"),
        ("sandbox", "etrade", "primary", "sandbox", None, None),
        ("legacy", "schwab", "main", "production", "schwab-hash", "100"),
    ):
        directory = f"private/{broker}/{login}/{environment}"
        profile = BrokerProfile(name, broker, login, environment, directory, key, identifier)
        result[name] = profile
        app = (
            {
                "client_id": "hidden-id",
                "client_secret": "hidden-secret",
                "redirect_uri": "https://127.0.0.1:8182",
            }
            if broker == "schwab"
            else {
                "consumer_key": "hidden-key",
                "consumer_secret": "hidden-secret",
                "environment": environment,
                "login_alias": login,
            }
        )
        write_private_json(profile.directory / "app.json", app)
    save_profiles(Path("private/brokers.json"), result)
    return result


def portfolio(identifier: str, quantity: str = "246.245") -> list[dict]:
    return [
        {
            "PortfolioResponse": {
                "AccountPortfolio": [
                    {
                        "accountId": identifier,
                        "Position": [
                            {
                                "positionId": "position-a",
                                "positionType": "LONG",
                                "quantity": quantity,
                                "Product": {"symbol": "QQQ", "securityType": "EQ"},
                            }
                        ],
                        "totalNoOfPages": 1,
                        "nextPageNo": "",
                    }
                ]
            }
        }
    ]


def test_one_positions_command_routes_each_broker_and_preserves_aliases(
    profiles: dict[str, BrokerProfile],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = []

    class Client:
        def __init__(self, profile: BrokerProfile) -> None:
            self.profile = profile

        def portfolio(self, key: str) -> list[dict]:
            assert key == self.profile.account_key
            return portfolio(self.profile.account_id or "")

        def account(self, key: str) -> dict:
            assert key == self.profile.account_key
            return {
                "securitiesAccount": {
                    "accountNumber": "100",
                    "positions": [
                        {
                            "longQuantity": 641.358,
                            "shortQuantity": 0,
                            "instrument": {"symbol": "QQQ", "assetType": "EQUITY"},
                        }
                    ],
                }
            }

    def client(profile: BrokerProfile) -> Client:
        selected.append(profile.name)
        return Client(profile)

    monkeypatch.setattr(cli, "_client", client)
    for alias in ("legacy", "primary", "secondary"):
        file = Path(f"private/{alias}-positions.json")
        assert main(["broker", "--profile", alias, "positions", "--output", str(file)]) == 0
        saved = json.loads(file.read_text())
        assert saved["request"]["profile"] == saved["normalized"]["profile"] == alias
        assert saved["normalized"]["positions"][0]["quantity"] in {"246.245", "641.358"}
        assert saved["normalized"]["actual_fills_reconciled"] is False
        assert file.stat().st_mode & 0o777 == 0o600
    assert selected == ["legacy", "primary", "secondary"]
    output = capsys.readouterr().out
    assert "hidden-secret" not in output and "246.245" not in output


def test_missing_pin_cross_login_and_reserved_outputs_fail_before_network(
    profiles: dict[str, BrokerProfile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_client(profile: BrokerProfile) -> None:
        raise AssertionError("No provider/OAuth calls should happen")

    monkeypatch.setattr(cli, "_client", no_client)
    assert main(["broker", "--profile", "sandbox", "positions"]) == 1
    original = (profiles["primary"].directory / "app.json").read_bytes()
    assert (
        main(
            [
                "broker",
                "--profile",
                "primary",
                "quotes",
                "--symbols",
                "QQQ",
                "--output",
                "private/unconfigured/token.json",
            ]
        )
        == 1
    )
    assert main(["broker", "--profile", "primary", "chain", "--symbol", "QQQ"]) == 1
    assert (
        main(
            ["broker", "--profile", "legacy", "chain", "--symbol", "QQQ", "--expiry", "2026-11-06"]
        )
        == 1
    )
    assert (profiles["primary"].directory / "app.json").read_bytes() == original
    wrong = json.loads(original)
    wrong["login_alias"] = "secondary"
    write_private_json(profiles["primary"].directory / "app.json", wrong)
    assert main(["broker", "--profile", "primary", "quotes", "--symbols", "QQQ"]) == 1


def test_binding_uses_exact_inventory_and_never_first_account(
    profiles: dict[str, BrokerProfile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unbound = replace(profiles["primary"], account_key=None, account_id=None)
    profiles["primary"] = unbound
    save_profiles(Path("private/brokers.json"), profiles)
    envelope = {
        "provider": "etrade-api",
        "resource": "accounts",
        "request": unbound.identity(),
        "data": {
            "AccountListResponse": {
                "Accounts": {
                    "Account": [
                        {"accountIdKey": "other", "accountId": "999"},
                        {"accountIdKey": "chosen", "accountId": "200"},
                    ]
                }
            }
        },
    }
    file = Path("private/accounts.json")
    write_private_json(file, envelope)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "chosen")
    assert (
        main(["broker", "--profile", "primary", "bind-account", "--accounts-file", str(file)]) == 0
    )
    saved = load_profiles(Path("private/brokers.json"))
    assert saved["primary"].account_key == "chosen"
    assert saved["primary"].account_id == "200"
    assert saved["secondary"] == profiles["secondary"]


def test_positions_reject_other_accounts_and_preserve_unknown_direction(
    profiles: dict[str, BrokerProfile],
) -> None:
    with pytest.raises(ValueError, match="different account"):
        normalize_positions(profiles["primary"], portfolio("300"))
    raw = portfolio("200")
    position = raw[0]["PortfolioResponse"]["AccountPortfolio"][0]["Position"][0]
    position["positionType"] = "UNSPECIFIED"
    result = normalize_positions(profiles["primary"], raw)
    assert result["positions"][0]["quantity"] is None
    assert result["rejected"][0]["reason"] == "unknown_signed_quantity"
    position["positionType"] = "SHORT"
    assert normalize_positions(profiles["primary"], raw)["positions"][0]["quantity"] == "-246.245"
    with pytest.raises(ValueError, match="Repeated"):
        normalize_positions(profiles["primary"], raw + raw)


def test_expected_account_suffix_blocks_wrong_binding(
    profiles: dict[str, BrokerProfile],
) -> None:
    with pytest.raises(ValueError, match="expected account suffix"):
        replace(profiles["primary"], expected_account_last4="9820")
    profile = replace(
        profiles["primary"],
        account_key="intended",
        account_id="12349820",
        expected_account_last4="9820",
    )
    assert profile.identity()["expected_account_last4"] == "9820"


def test_schwab_invalid_quantity_stays_unknown(profiles: dict[str, BrokerProfile]) -> None:
    result = normalize_positions(
        profiles["legacy"],
        {
            "securitiesAccount": {
                "accountNumber": "100",
                "positions": [
                    {
                        "longQuantity": -100,
                        "shortQuantity": 0,
                        "instrument": {"symbol": "QQQ", "assetType": "EQUITY"},
                    }
                ],
            },
        },
    )
    assert result["positions"][0]["quantity"] is None
    assert result["rejected"][0]["reason"] == "unknown_signed_quantity"


def test_profile_paths_and_duplicate_names_are_rejected(
    profiles: dict[str, BrokerProfile],
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="private"):
        private_path(tmp_path / "outside/app.json")
    data = json.loads(Path("private/brokers.json").read_text())
    data["profiles"].append(data["profiles"][0])
    write_private_json(Path("private/brokers.json"), data)
    with pytest.raises(ValueError, match="unique"):
        load_profiles(Path("private/brokers.json"))


def test_status_and_profile_listing_do_not_make_auth_requests(
    profiles: dict[str, BrokerProfile],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_session", lambda _: pytest.fail("No OAuth for status"))
    assert main(["broker", "profiles"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 4
    assert main(["broker", "--profile", "primary", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["account_bound"] is True and status["token_present"] is False
    assert status["order_commands_available_here"] is False
