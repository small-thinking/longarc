"""Synthetic CLI and preflight checks; no OAuth or broker network calls."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from longarc.cli import build_parser
from longarc.schwab import order_cli
from longarc.schwab.auth import write_private_json
from longarc.schwab.client import SchwabError
from longarc.schwab.orders import build_limit_call_order

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
OPTION = "QQQ   261112C00150000"
ACCOUNT_HASH = "synthetic_hash"


def spec(instruction: str = "SELL_TO_OPEN", quantity: int = 1,
         price: str = "1.25") -> dict[str, Any]:
    return build_limit_call_order({"option_symbol": OPTION, "instruction": instruction,
                                   "quantity": quantity, "price": price})


def account(shares: float = 100, shorts: float = 0, cash: float = 1000) -> dict[str, Any]:
    positions: list[dict[str, Any]] = [
        {"instrument": {"assetType": "EQUITY", "symbol": "QQQ"},
         "longQuantity": shares, "shortQuantity": 0},
    ]
    if shorts:
        positions.append({"instrument": {"assetType": "OPTION", "symbol": OPTION,
                                         "underlyingSymbol": "QQQ", "putCall": "CALL"},
                          "longQuantity": 0, "shortQuantity": shorts})
    return {"accountNumber": "synthetic_number", "positions": positions,
            "currentBalances": {"cashAvailableForTrading": cash}}


def chain(*, quote_age: int = 10, adjusted: bool = False) -> dict[str, Any]:
    row = {"symbol": OPTION, "putCall": "CALL", "multiplier": 100,
           "nonStandard": adjusted, "mini": False,
           "expirationDate": "2026-11-12T21:00:00Z",
           "quoteTimeInLong": int((NOW - timedelta(seconds=quote_age)).timestamp() * 1000),
           "optionDeliverablesList": [{"symbol": "QQQ", "deliverableUnits": 100}]}
    return {"isDelayed": False, "isChainTruncated": False,
            "callExpDateMap": {"2026-11-12:10": {"150.0": [row]}}}


def calendar(open_: bool = True) -> dict[str, Any]:
    return {"equity": {"EQ": {"isOpen": open_, "sessionHours": {"regularMarket": [
        {"start": "2026-11-02T14:30:00+00:00", "end": "2026-11-02T21:00:00+00:00"}
    ]}}}}


def pending(instruction: str, *, symbol: str = OPTION, quantity: int = 1,
            status: str = "WORKING", remaining: int | None = None,
            asset_type: str = "OPTION") -> dict[str, Any]:
    return {"status": status, "quantity": quantity,
            "remainingQuantity": quantity if remaining is None else remaining,
            "orderLegCollection": [{"instruction": instruction, "quantity": quantity,
                                    "instrument": {"assetType": asset_type, "symbol": symbol}}]}


def invoke(action: str, *, directory: str = "private/schwab",
           options: list[str] | None = None) -> int:
    args = build_parser().parse_args(["schwab", "--directory", directory, action,
                                      *(options or [])])
    return args.handler(args)


def setup_private(tmp_path: Path, *, profile: dict[str, Any] | None = None) -> Path:
    directory = tmp_path / "private/schwab"
    directory.mkdir(parents=True)
    write_private_json(tmp_path / "private/intent.json", {
        "option_symbol": OPTION, "instruction": "SELL_TO_OPEN", "quantity": 1,
        "price": "1.25"}, overwrite=False)
    if profile is not None:
        write_private_json(directory / "trading.json", profile, overwrite=False)
    return directory


def enabled_profile() -> dict[str, Any]:
    return {"enabled": True, "account_hash": ACCOUNT_HASH,
            "max_contracts": 2, "max_buy_debit_u": 200_000_000,
            "open_orders_window_verified": True}


def options_for(action: str, *, output: str) -> list[str]:
    result = ["--output", output]
    if action in {"order-plan", "order-preview", "order-submit", "order-replace"}:
        result += ["--file", "private/intent.json"]
    if action in {"order-replace", "order-cancel", "order-status"}:
        result += ["--order-id", "12345"]
    return result


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline or rejected request reached OAuth/network")

    monkeypatch.setattr(order_cli, "OAuthSession", fail)
    monkeypatch.setattr(order_cli, "SchwabReadOnlyClient", fail)
    monkeypatch.setattr(order_cli, "SchwabOrderClient", fail)


@pytest.mark.parametrize("action", ["order-plan", "order-preview", "order-submit",
                                     "order-replace", "order-cancel", "order-status"])
def test_all_order_commands_default_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    setup_private(tmp_path)
    forbid_network(monkeypatch)
    output = f"private/schwab/{action}.json"
    assert invoke(action, options=options_for(action, output=output)) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads((tmp_path / output).read_text())
    assert printed["outcome"] == saved["outcome"] == "LOCAL_PLAN"
    assert printed["network_used"] is saved["network_used"] is False
    assert saved["broker_validated"] is False


@pytest.mark.parametrize("action", ["order-preview", "order-submit",
                                     "order-replace", "order-cancel"])
@pytest.mark.parametrize("profile", [None, {"enabled": False,
                                            "account_hash": ACCOUNT_HASH}])
def test_live_write_without_enabled_profile_stops_before_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str,
    profile: dict[str, Any] | None, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    setup_private(tmp_path, profile=profile)
    forbid_network(monkeypatch)
    opts = options_for(action, output=f"private/schwab/{action}.json")
    assert invoke(action, options=[*opts, "--live", "--attempt-id", "first",
                                   "--confirm-digest", "wrong"]) == 1
    assert "disabled" in capsys.readouterr().out
    assert not (tmp_path / "private/schwab/orders/attempts/first").exists()


def test_digest_binds_action_account_index_order_id_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = setup_private(tmp_path, profile=enabled_profile())
    forbid_network(monkeypatch)

    sequence = 0

    def plan(action: str, number: int, index: int = 0) -> str:
        nonlocal sequence
        sequence += 1
        opts = options_for(action, output=f"private/schwab/plan-{sequence}.json")
        if action in {"order-cancel", "order-replace", "order-status"}:
            pos = opts.index("--order-id") + 1
            opts[pos] = str(number)
        assert invoke(action, options=[*opts, "--account-index", str(index)]) == 0
        return json.loads(capsys.readouterr().out)["confirmation_digest"]

    base = plan("order-cancel", 12345)
    assert base != plan("order-replace", 12345)
    assert base != plan("order-cancel", 12346)
    assert base != plan("order-cancel", 12345, index=1)
    profile = enabled_profile()
    profile["account_hash"] = "other_synthetic_hash"
    write_private_json(directory / "trading.json", profile)
    assert base != plan("order-cancel", 12345)


def test_wrong_confirmation_digest_stops_before_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    setup_private(tmp_path, profile=enabled_profile())
    forbid_network(monkeypatch)
    opts = options_for("order-preview", output="private/schwab/preview-live.json")
    assert invoke("order-preview", options=[*opts, "--live", "--attempt-id", "first",
                                            "--confirm-digest", "0" * 64]) == 1
    assert "digest" in capsys.readouterr().out
    assert not (tmp_path / "private/schwab/orders/attempts/first").exists()


@pytest.mark.parametrize("action", ["order-submit", "order-replace"])
def test_live_submit_requires_explicit_order_window_attestation_before_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], action: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    profile = enabled_profile()
    profile.pop("open_orders_window_verified")
    setup_private(tmp_path, profile=profile)
    forbid_network(monkeypatch)
    plan_opts = options_for(action, output="private/schwab/window-plan.json")
    assert invoke(action, options=plan_opts) == 0
    digest = json.loads(capsys.readouterr().out)["confirmation_digest"]
    live_opts = options_for(action, output="private/schwab/window-live.json")
    assert invoke(action, options=[*live_opts, "--live", "--attempt-id", "window-one",
                                    "--confirm-digest", digest]) == 1
    assert "open-order query coverage" in capsys.readouterr().out
    assert not (tmp_path / "private/schwab/orders/attempts/window-one").exists()


def test_intent_precedes_oauth_and_attempt_id_cannot_resend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = setup_private(tmp_path, profile=enabled_profile())
    opts = options_for("order-preview", output="private/schwab/preview-plan.json")
    assert invoke("order-preview", options=opts) == 0
    digest = json.loads(capsys.readouterr().out)["confirmation_digest"]
    live_opts = options_for("order-preview", output="private/schwab/preview-live.json")
    calls = 0

    class FailingSession:
        def __init__(self, path: Path) -> None:
            nonlocal calls
            calls += 1
            assert path == directory
            intent_path = directory / "orders/attempts/attempt-one/intent.json"
            assert json.loads(intent_path.read_text())["outcome"] == "INTENT_RECORDED"
            raise SchwabError("Synthetic OAuth failure")

    monkeypatch.setattr(order_cli, "OAuthSession", FailingSession)
    live = [*live_opts, "--live", "--attempt-id", "attempt-one", "--confirm-digest", digest]
    assert invoke("order-preview", options=live) == 1
    assert "Synthetic OAuth failure" in capsys.readouterr().out
    assert calls == 1
    assert (directory / "orders/attempts/attempt-one/failure.json").exists()
    assert invoke("order-preview", options=live) == 1
    assert "Attempt already exists" in capsys.readouterr().out
    assert calls == 1


def test_live_preview_pins_account_and_never_submits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = setup_private(tmp_path, profile=enabled_profile())
    opts = options_for("order-preview", output="private/schwab/preview-plan.json")
    assert invoke("order-preview", options=opts) == 0
    digest = json.loads(capsys.readouterr().out)["confirmation_digest"]
    live_opts = options_for("order-preview", output="private/schwab/preview-live.json")
    events: list[str] = []

    class Session:
        def __init__(self, path: Path) -> None:
            assert (directory / "orders/attempts/preview-one/intent.json").exists()
            events.append("oauth")

        def access_token(self) -> str:
            return "synthetic-token"

    class Reads:
        def __init__(self, token: str) -> None:
            assert token == "synthetic-token"

        def account_numbers(self) -> list[dict[str, str]]:
            return [{"hashValue": ACCOUNT_HASH, "accountNumber": "synthetic_number"}]

    class Orders:
        def __init__(self, token: str, account_hash: str, *, enabled: bool) -> None:
            assert enabled and account_hash == ACCOUNT_HASH

        def preview_order(self, order: dict[str, Any]) -> dict[str, Any]:
            assert (directory / "orders/attempts/preview-one/intent.json").exists()
            assert order == spec()
            events.append("preview")
            return {"outcome": "PREVIEWED", "order_id": None,
                    "reconciliation_required": False, "preview": {"valid": True}}

        def submit_order(self, order: dict[str, Any]) -> None:
            pytest.fail("Preview called submit")

    monkeypatch.setattr(order_cli, "OAuthSession", Session)
    monkeypatch.setattr(order_cli, "SchwabReadOnlyClient", Reads)
    monkeypatch.setattr(order_cli, "SchwabOrderClient", Orders)
    live = [*live_opts, "--live", "--attempt-id", "preview-one", "--confirm-digest", digest]
    assert invoke("order-preview", options=live) == 0
    assert events == ["oauth", "preview"]
    assert json.loads((directory / "orders/attempts/preview-one/response.json").read_text())[
        "outcome"] == "PREVIEWED"


def test_live_pin_mismatch_stops_before_order_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    setup_private(tmp_path, profile=enabled_profile())
    opts = options_for("order-preview", output="private/schwab/pin-plan.json")
    assert invoke("order-preview", options=opts) == 0
    digest = json.loads(capsys.readouterr().out)["confirmation_digest"]
    live_opts = options_for("order-preview", output="private/schwab/pin-mismatch.json")

    class Session:
        def __init__(self, path: Path) -> None:
            pass

        def access_token(self) -> str:
            return "synthetic-token"

    class Reads:
        def __init__(self, token: str) -> None:
            pass

        def account_numbers(self) -> list[dict[str, str]]:
            return [{"hashValue": "other_hash"}]

    monkeypatch.setattr(order_cli, "OAuthSession", Session)
    monkeypatch.setattr(order_cli, "SchwabReadOnlyClient", Reads)
    monkeypatch.setattr(order_cli, "SchwabOrderClient",
                        lambda *args, **kwargs: pytest.fail("Pin mismatch reached order client"))
    assert invoke("order-preview", options=[*live_opts, "--live", "--attempt-id", "mismatch",
                                             "--confirm-digest", digest]) == 1
    assert "pinned trading profile" in capsys.readouterr().out


def test_live_unknown_submit_persists_response_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = setup_private(tmp_path, profile=enabled_profile())
    opts = options_for("order-submit", output="private/schwab/submit-plan.json")
    assert invoke("order-submit", options=opts) == 0
    digest = json.loads(capsys.readouterr().out)["confirmation_digest"]
    live_opts = options_for("order-submit", output="private/schwab/unknown-submit.json")

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return NOW

    class Session:
        def __init__(self, path: Path) -> None:
            assert (directory / "orders/attempts/unknown-one/intent.json").exists()

        def access_token(self) -> str:
            return "synthetic-token"

    events: list[str] = []

    class Reads:
        def __init__(self, token: str) -> None:
            pass

        def account_numbers(self) -> list[dict[str, str]]:
            return [{"hashValue": ACCOUNT_HASH, "accountNumber": "synthetic_number"}]

        def accounts(self) -> list[dict[str, Any]]:
            phase = "account_before" if "account_before" not in events else "account_after"
            events.append(phase)
            selected = account()
            if phase == "account_after":
                selected["positions"][0]["marketValue"] = 14_250
            return [{"securitiesAccount": selected}]

        def option_chain(self, symbol: str) -> dict[str, Any]:
            assert symbol == "QQQ"
            return chain()

        def orders(self, account_hash: str, start: str, end: str) -> list[dict[str, Any]]:
            assert account_hash == ACCOUNT_HASH
            events.append("orders")
            return []

        def market_hours(self, day: str) -> dict[str, Any]:
            events.append("calendar")
            return calendar()

    sends = 0

    class Orders:
        def __init__(self, token: str, account_hash: str, *, enabled: bool) -> None:
            assert enabled and account_hash == ACCOUNT_HASH

        def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
            nonlocal sends
            sends += 1
            assert (directory / "orders/attempts/unknown-one/intent.json").exists()
            return {"outcome": "UNKNOWN", "http_status": None,
                    "order_id": None, "reconciliation_required": True}

        def get_order(self, order_id: str) -> None:
            pytest.fail("Unknown submit should not read back an unverified order ID")

    monkeypatch.setattr(order_cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(order_cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(order_cli, "OAuthSession", Session)
    monkeypatch.setattr(order_cli, "SchwabReadOnlyClient", Reads)
    monkeypatch.setattr(order_cli, "SchwabOrderClient", Orders)
    live = [*live_opts, "--live", "--attempt-id", "unknown-one", "--confirm-digest", digest]
    assert invoke("order-submit", options=live) == 1
    assert sends == 1
    assert events == ["account_before", "orders", "calendar", "account_after"]
    saved = json.loads((directory / "orders/attempts/unknown-one/response.json").read_text())
    assert saved["outcome"] == "UNKNOWN" and saved["reconciliation_required"] is True
    result = json.loads((directory / "orders/attempts/unknown-one/result.json").read_text())
    assert result == saved
    assert json.loads((tmp_path / "private/schwab/unknown-submit.json").read_text())[
        "result"] == saved


@pytest.mark.parametrize("other_pending", [False, True])
@pytest.mark.parametrize("partial_stage", ["none", "prior", "list"])
def test_live_replace_excludes_only_verified_target_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], other_pending: bool, partial_stage: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = setup_private(tmp_path, profile=enabled_profile())
    plan_opts = options_for("order-replace", output="private/schwab/replace-plan.json")
    assert invoke("order-replace", options=plan_opts) == 0
    digest = json.loads(capsys.readouterr().out)["confirmation_digest"]

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return NOW

    class Session:
        def __init__(self, path: Path) -> None:
            assert (directory / "orders/attempts/replace-one/intent.json").exists()

        def access_token(self) -> str:
            return "synthetic-token"

    account_reads = 0

    class Reads:
        def __init__(self, token: str) -> None:
            pass

        def account_numbers(self) -> list[dict[str, str]]:
            return [{"hashValue": ACCOUNT_HASH, "accountNumber": "synthetic_number"}]

        def accounts(self) -> list[dict[str, Any]]:
            nonlocal account_reads
            account_reads += 1
            return [{"securitiesAccount": account(100)}]

        def option_chain(self, symbol: str) -> dict[str, Any]:
            return chain()

        def orders(self, account_hash: str, start: str, end: str) -> list[dict[str, Any]]:
            target = {**pending("SELL_TO_OPEN"), "orderId": 12345,
                      "filledQuantity": 1 if partial_stage == "list" else 0}
            return [target, *([{**pending("SELL_TO_OPEN"), "orderId": 12346}]
                             if other_pending else [])]

        def market_hours(self, day: str) -> dict[str, Any]:
            return calendar()

    replaced = 0
    prior = {"outcome": "OBSERVED", "order_id": "12345", "terminal": False,
             "order": {"status": "WORKING", "remainingQuantity": 1,
                       "filledQuantity": 1 if partial_stage == "prior" else 0},
             "raw_order": {"orderStrategyType": "SINGLE", "orderLegCollection": [
                 {"instruction": "SELL_TO_OPEN", "quantity": 1,
                  "instrument": {"assetType": "OPTION", "symbol": OPTION}}]}}

    class Orders:
        def __init__(self, token: str, account_hash: str, *, enabled: bool) -> None:
            assert enabled

        def get_order(self, order_id: str) -> dict[str, Any]:
            assert order_id == "12345"
            return prior

        def replace_order(self, order_id: str, order: dict[str, Any]) -> dict[str, Any]:
            nonlocal replaced
            replaced += 1
            assert order_id == "12345" and order == spec()
            return {"outcome": "UNKNOWN", "order_id": None,
                    "reconciliation_required": True}

    monkeypatch.setattr(order_cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(order_cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(order_cli, "OAuthSession", Session)
    monkeypatch.setattr(order_cli, "SchwabReadOnlyClient", Reads)
    monkeypatch.setattr(order_cli, "SchwabOrderClient", Orders)
    live_opts = options_for("order-replace", output="private/schwab/replace-live.json")
    assert invoke("order-replace", options=[*live_opts, "--live", "--attempt-id",
                                            "replace-one", "--confirm-digest", digest]) == 1
    assert replaced == (1 if partial_stage == "none" and not other_pending else 0)
    assert (directory / "orders/attempts/replace-one/target-order.json").exists()
    assert account_reads == (0 if partial_stage == "prior" else
                             1 if partial_stage == "list" else 2)
    if other_pending or partial_stage != "none":
        assert not (directory / "orders/attempts/replace-one/request-started.json").exists()
    else:
        assert (directory / "orders/attempts/replace-one/request-started.json").exists()


def test_live_cancel_readback_keeps_partial_fill_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = setup_private(tmp_path, profile=enabled_profile())
    plan_opts = options_for("order-cancel", output="private/schwab/cancel-plan.json")
    assert invoke("order-cancel", options=plan_opts) == 0
    digest = json.loads(capsys.readouterr().out)["confirmation_digest"]

    class Session:
        def __init__(self, path: Path) -> None:
            assert (directory / "orders/attempts/cancel-one/intent.json").exists()

        def access_token(self) -> str:
            return "synthetic-token"

    class Reads:
        def __init__(self, token: str) -> None:
            pass

        def account_numbers(self) -> list[dict[str, str]]:
            return [{"hashValue": ACCOUNT_HASH}]

    readbacks = 0

    class Orders:
        def __init__(self, token: str, account_hash: str, *, enabled: bool) -> None:
            assert enabled

        def get_order(self, order_id: str) -> dict[str, Any]:
            nonlocal readbacks
            readbacks += 1
            if readbacks == 1:
                return {"outcome": "OBSERVED", "terminal": False,
                        "order_id": order_id, "order": {"status": "WORKING"}}
            return {"outcome": "OBSERVED", "terminal": True, "order_id": order_id,
                    "reconciliation_required": True,
                    "order": {"status": "CANCELED", "filledQuantity": 1,
                              "remainingQuantity": 0}}

        def cancel_order(self, order_id: str) -> dict[str, Any]:
            assert (directory / "orders/attempts/cancel-one/request-started.json").exists()
            return {"outcome": "CANCELLATION_REQUESTED", "order_id": order_id,
                    "reconciliation_required": True}

    monkeypatch.setattr(order_cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(order_cli, "OAuthSession", Session)
    monkeypatch.setattr(order_cli, "SchwabReadOnlyClient", Reads)
    monkeypatch.setattr(order_cli, "SchwabOrderClient", Orders)
    live_opts = options_for("order-cancel", output="private/schwab/cancel-live.json")
    assert invoke("order-cancel", options=[*live_opts, "--live", "--attempt-id",
                                           "cancel-one", "--confirm-digest", digest]) == 0
    assert readbacks == 2
    result = json.loads((directory / "orders/attempts/cancel-one/result.json").read_text())
    assert result["mutation"]["outcome"] == "CANCELLATION_REQUESTED"
    assert result["status_readback"]["order"]["filledQuantity"] == 1
    assert result["status_readback"]["reconciliation_required"] is True
    assert result["no_execution_journal_write"] is True


def test_641_shares_six_shorts_blocks_one_new_sto() -> None:
    with pytest.raises(SchwabError, match="Insufficient unreserved shares"):
        order_cli.check_preflight(spec(), account(641.358, 6), [], chain(), calendar(), NOW)


def test_100_fresh_shares_allows_one_sto() -> None:
    result = order_cli.check_preflight(spec(), account(100), [], chain(), calendar(), NOW)
    assert result["unreserved_shares"] == "100"
    assert result["strategy_approval"] is False


@pytest.mark.parametrize("orders", [
    [pending("SELL_TO_OPEN")],
    [pending("SELL_TO_OPEN", status="PENDING_CANCEL")],
    [{**pending("SELL_TO_OPEN", quantity=2, status="WORKING", remaining=1),
      "filledQuantity": 1}],
    [{"status": "FILLED", "quantity": 1, "orderLegCollection": [],
      "childOrderStrategies": [pending("SELL_TO_OPEN", status="AWAITING_PARENT_ORDER")]}],
    [pending("SELL", symbol="QQQ", quantity=100, asset_type="EQUITY")],
])
def test_pending_call_and_stock_reservations_block_sto(orders: list[dict[str, Any]]) -> None:
    with pytest.raises(SchwabError, match="Insufficient unreserved shares"):
        order_cli.check_preflight(spec(), account(100), orders, chain(), calendar(), NOW)


def test_malformed_short_option_identity_and_adjusted_deliverables_fail() -> None:
    bad_account = account(100)
    bad_account["positions"].append({
        "instrument": {"assetType": "OPTION", "symbol": OPTION, "putCall": "CALL"},
        "longQuantity": 0, "shortQuantity": 1})
    with pytest.raises(SchwabError, match="unresolved identity"):
        order_cli.check_preflight(spec(), bad_account, [], chain(), calendar(), NOW)
    with pytest.raises(SchwabError, match="standard 100-share CALL"):
        order_cli.check_preflight(spec(), account(100), [], chain(adjusted=True), calendar(), NOW)


def test_unknown_short_asset_and_malformed_pending_option_fail_closed() -> None:
    bad_account = account(100)
    bad_account["positions"].append({
        "instrument": {"symbol": "MYSTERY"}, "longQuantity": 0, "shortQuantity": 1})
    with pytest.raises(SchwabError, match="unresolved asset type"):
        order_cli.check_preflight(spec(), bad_account, [], chain(), calendar(), NOW)
    malformed = pending("SELL_TO_OPEN")
    malformed["orderLegCollection"][0]["instrument"]["assetType"] = "MYSTERY"
    with pytest.raises(SchwabError, match="unresolved identity or side"):
        order_cli.check_preflight(spec(), account(100), [malformed], chain(), calendar(), NOW)


def test_btc_respects_unreserved_shorts_and_cash() -> None:
    btc = spec("BUY_TO_CLOSE", price="1.25")
    result = order_cli.check_preflight(btc, account(0, 1, cash=200), [], chain(), calendar(), NOW)
    assert result["current_target_short_contracts"] == "1"
    with pytest.raises(SchwabError, match="unreserved short"):
        order_cli.check_preflight(btc, account(0, 1, cash=200),
                                  [pending("BUY_TO_CLOSE")], chain(), calendar(), NOW)
    with pytest.raises(SchwabError, match="Insufficient cash"):
        order_cli.check_preflight(btc, account(0, 1, cash=100), [], chain(), calendar(), NOW)


def test_stale_quote_and_closed_session_fail() -> None:
    with pytest.raises(SchwabError, match="within 60 seconds"):
        order_cli.check_preflight(spec(), account(), [], chain(quote_age=61), calendar(), NOW)
    with pytest.raises(SchwabError, match="regular equity session"):
        order_cli.check_preflight(spec(), account(), [], chain(), calendar(open_=False), NOW)


def test_collection_stability_ignores_market_value_but_blocks_quantity_change() -> None:
    before = account(100)
    after = deepcopy(before)
    after["positions"][0]["marketValue"] = 14_250
    order_cli.check_collection_stability(before, after, [], NOW, "QQQ")
    after["positions"][0]["longQuantity"] = 99
    with pytest.raises(SchwabError, match="Positions changed"):
        order_cli.check_collection_stability(before, after, [], NOW, "QQQ")


@pytest.mark.parametrize("close_time,blocked", [
    (NOW.isoformat(), True),
    ((NOW - timedelta(seconds=1)).isoformat(), False),
    (None, True),
])
def test_collection_stability_detects_latent_fill_with_unchanged_positions(
    close_time: str | None, blocked: bool,
) -> None:
    filled = {"status": "FILLED", "orderLegCollection": [
        {"instruction": "SELL_TO_OPEN", "quantity": 1,
         "instrument": {"assetType": "OPTION", "symbol": OPTION}}]}
    if close_time is not None:
        filled["closeTime"] = close_time
    if blocked:
        with pytest.raises(SchwabError, match="filled|clock"):
            order_cli.check_collection_stability(account(100), account(100), [filled], NOW, "QQQ")
    else:
        order_cli.check_collection_stability(account(100), account(100), [filled], NOW, "QQQ")
