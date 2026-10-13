"""One read-only command surface for named Schwab and E*TRADE profiles."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from longarc.brokers.market import normalize_expiries, normalize_quotes
from longarc.brokers.order_cli import add_parsers as add_order_parsers
from longarc.brokers.profiles import BrokerProfile, load_profiles, private_path, save_profiles
from longarc.brokers.snapshots import (
    account_inventory,
    normalize_positions,
    verify_balance_account,
)
from longarc.core.private_files import PrivateFileError, read_private_json, write_private_json
from longarc.data.api_capture import ingest_api_chain
from longarc.etrade import capture as etrade_capture
from longarc.etrade.auth import AppConfig as ETradeConfig
from longarc.etrade.auth import OAuthSession as ETradeSession
from longarc.etrade.client import ETradeError, ETradeReadOnlyClient
from longarc.schwab.auth import AppConfig as SchwabConfig
from longarc.schwab.auth import OAuthSession as SchwabSession
from longarc.schwab.capture import ingest_chain as ingest_schwab_chain
from longarc.schwab.client import SchwabError, SchwabReadOnlyClient

_AUTH_FILES = {"app.json", "token.json", "pending-auth.json", "authorization.json", "trading.json"}
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9.]{0,15}\Z")


def _source(profile: BrokerProfile) -> str:
    if profile.broker == "schwab":
        return "schwab_api"
    return "etrade_sandbox_synthetic" if profile.environment == "sandbox" else "etrade_api"


def _config(profile: BrokerProfile) -> Any:
    if profile.broker == "schwab":
        return SchwabConfig.load(profile.directory / "app.json")
    config = ETradeConfig.load(profile.directory / "app.json")
    if config.login_alias != profile.login_alias or config.environment != profile.environment:
        raise ValueError("Chosen profile does not match the credential login/environment.")
    return config


def _session(profile: BrokerProfile) -> Any:
    _config(profile)
    return (
        SchwabSession(profile.directory)
        if profile.broker == "schwab"
        else ETradeSession(profile.directory)
    )


def _client(profile: BrokerProfile) -> Any:
    session = _session(profile)
    if profile.broker == "schwab":
        return SchwabReadOnlyClient(session.access_token())
    return ETradeReadOnlyClient(session.config, session.renew())


def _check_saved_identity(profile: BrokerProfile, envelope: dict[str, Any]) -> None:
    request = envelope.get("request")
    if not isinstance(request, dict):
        raise ValueError("Saved broker envelope requires a request identity.")
    for field, value in profile.identity().items():
        if request.get(field) != value:
            raise ValueError("Saved snapshot belongs to a different profile or account binding.")
    if envelope.get("provider") != profile.broker + "-api":
        raise ValueError("Saved snapshot belongs to a different broker.")


def _run(args: argparse.Namespace) -> int:
    action = args.broker_action
    config_path = private_path(args.profiles_file)
    if action == "add-profile":
        profiles = load_profiles(config_path) if config_path.exists() else {}
        if not args.profile or args.profile in profiles:
            raise ValueError("Choose a new, explicit --profile name.")
        profile = BrokerProfile(
            args.profile,
            args.broker,
            args.login,
            args.environment,
            args.directory,
            expected_account_last4=args.expected_account_last4,
        )
        profiles[profile.name] = profile
        save_profiles(config_path, profiles)
        print("Named broker profile saved privately; account is not yet bound.")
        return 0
    profiles = load_profiles(config_path)
    if action == "profiles":
        print(
            json.dumps(
                [
                    {
                        "profile": p.name,
                        "broker": p.broker,
                        "login_alias": p.login_alias,
                        "environment": p.environment,
                        "account_bound": p.account_key is not None,
                    }
                    for p in profiles.values()
                ]
            )
        )
        return 0
    if not args.profile or args.profile not in profiles:
        raise ValueError("Select a configured --profile; no default broker account is inferred.")
    profile = profiles[args.profile]
    if action == "bind-account":
        envelope = read_private_json(private_path(args.accounts_file))
        _check_saved_identity(profile, envelope)
        if envelope.get("resource") != "accounts":
            raise ValueError("Bind accounts from a saved accounts snapshot.")
        accounts = account_inventory(profile, envelope["data"])
        chosen = getpass.getpass("Exact account key from the private accounts snapshot (hidden): ")
        matches = [row for row in accounts if row["account_key"] == chosen]
        if len(matches) != 1:
            raise ValueError("Account key must match exactly one account in this login snapshot.")
        row = matches[0]
        if row.get("account_status") not in (None, "ACTIVE", "OPEN"):
            raise ValueError("Account is not active; review its broker status before binding.")
        profiles[profile.name] = replace(profile, account_key=chosen, account_id=row["account_id"])
        save_profiles(config_path, profiles)
        print("Exact account bound privately; no first-account fallback is used.")
        return 0
    if action == "ingest-chain":
        chain_file, quotes_file = private_path(args.chain_file), private_path(args.quotes_file)
        for file in (chain_file, quotes_file):
            _check_saved_identity(profile, read_private_json(file))
        if profile.broker == "schwab":
            receipt = ingest_schwab_chain(Path(args.db), chain_file, quotes_file)
        else:
            receipt = ingest_api_chain(
                Path(args.db),
                chain_file,
                quotes_file,
                source=_source(profile),
                adapter_tag="etrade-api-chain-v1",
                adapter_file=Path(etrade_capture.__file__),
                normalize=etrade_capture.normalize_api_chain,
                synthetic=profile.environment == "sandbox",
            )
        print(json.dumps(receipt))
        return 1 if receipt["status"] == "error" else 0
    _config(profile)
    if action == "status":
        print(
            json.dumps(
                {
                    "profile": profile.name,
                    "broker": profile.broker,
                    "login_alias": profile.login_alias,
                    "environment": profile.environment,
                    "configured": True,
                    "token_present": (profile.directory / "token.json").exists(),
                    "account_bound": profile.account_key is not None,
                    "ordinary_reads_only": True,
                    "order_commands_available_here": False,
                }
            )
        )
        return 0
    if action in {"auth-start", "auth-finish", "renew"}:
        session = _session(profile)
        if action == "auth-start":
            url = session.start()
            destination = profile.directory / "authorization.json"
            write_private_json(destination, {"authorization_url": url, **profile.identity()})
            print(f"Authorization URL saved privately: {destination}")
            print("Open the URL and authorize this exact login; never paste it into chat.")
        elif action == "auth-finish":
            label = (
                "E*TRADE verification code"
                if profile.broker == "etrade"
                else "Full Schwab callback URL"
            )
            session.finish(getpass.getpass(label + " (hidden): "))
            (profile.directory / "authorization.json").unlink(missing_ok=True)
            print("OAuth credentials saved privately for the chosen login/environment.")
        else:
            if profile.broker != "etrade":
                raise ValueError(
                    "Explicit renew is E*TRADE-only; Schwab refreshes on requested reads."
                )
            session.renew()
            print("E*TRADE token renewed within its existing Eastern-day session.")
        return 0
    requested_output = private_path(args.output) if args.output else None
    if requested_output and (
        requested_output.name in _AUTH_FILES or requested_output == config_path
    ):
        raise ValueError("Snapshot output must not overwrite broker configuration or credentials.")
    if requested_output and requested_output.exists():
        raise ValueError("Snapshot output already exists; choose a new private file.")
    if action in {"positions", "balance"}:
        profile.require_account()  # Fail before any OAuth refresh or provider traffic.
    if action in {"quotes", "chain", "expiries"}:
        symbols = args.symbols if action == "quotes" else [args.symbol]
        if not symbols or any(not _SYMBOL.fullmatch(symbol) for symbol in symbols):
            raise ValueError("Invalid underlying symbol.")
        if action == "quotes" and profile.broker == "etrade" and len(symbols) > 25:
            raise ValueError("E*TRADE quotes support at most 25 symbols per request.")
    if action == "chain":
        if profile.broker == "etrade":
            if not args.expiry or date.fromisoformat(args.expiry).isoformat() != args.expiry:
                raise ValueError("E*TRADE chain requires an explicit YYYY-MM-DD --expiry.")
        elif args.expiry:
            raise ValueError("Schwab returns a multi-expiry chain; omit --expiry.")
    client = _client(profile)
    request = profile.identity()
    normalized: dict[str, Any] | None = None
    if action == "accounts":
        raw = client.account_numbers() if profile.broker == "schwab" else client.accounts()
        normalized = {"format": "broker-accounts-v1", "accounts": account_inventory(profile, raw)}
    elif action == "positions":
        raw = (
            client.account(profile.account_key)
            if profile.broker == "schwab"
            else client.portfolio(profile.account_key)
        )
        normalized = normalize_positions(profile, raw)
    elif action == "balance":
        raw = (
            client.account(profile.account_key)
            if profile.broker == "schwab"
            else client.balance(profile.account_key)
        )
        verify_balance_account(profile, raw)
    elif action == "quotes":
        request["symbols"] = [symbol.upper() for symbol in args.symbols]
        raw = client.quotes(args.symbols)
        normalized = normalize_quotes(profile, raw, args.symbols)
    elif action == "expiries":
        request["symbol"] = args.symbol.upper()
        if profile.broker == "schwab":
            raw = client.option_chain(args.symbol)
        else:
            raw = client.expiry_dates(args.symbol)
        normalized = normalize_expiries(profile, raw, args.symbol)
    else:
        request["symbol"] = args.symbol.upper()
        if profile.broker == "schwab":
            raw = client.option_chain(args.symbol)
        else:
            request["expiry"] = args.expiry
            raw = client.option_chains(args.symbol, args.expiry)
    captured_at = datetime.now(UTC)
    output = (
        requested_output
        or profile.directory / "snapshots" / f"{action}-{captured_at:%Y%m%dT%H%M%S%fZ}.json"
    )
    write_private_json(
        output,
        {
            "provider": profile.broker + "-api",
            "source": _source(profile),
            "captured_at": captured_at.isoformat(),
            "resource": action,
            "request": request,
            "data": raw,
            "normalized": normalized,
            "synthetic": profile.environment == "sandbox",
        },
        overwrite=False,
    )
    print(f"Read-only broker snapshot saved: {output}")
    return 0


def _handler(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except (
        SchwabError,
        ETradeError,
        PrivateFileError,
        ValueError,
        OSError,
        EOFError,
        KeyError,
        TypeError,
        sqlite3.Error,
    ) as error:
        print(
            str(error)
            if isinstance(error, (SchwabError, ETradeError, PrivateFileError))
            else "Local broker operation failed; check profile, account binding and input."
        )
        return 1


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("broker", help="Shared named-account read-only broker commands")
    parser.add_argument("--profiles-file", default="private/brokers.json")
    parser.add_argument("--profile", help="Explicit configured account/profile alias")
    actions = parser.add_subparsers(dest="broker_action", required=True)
    add_order_parsers(actions)
    actions.add_parser("profiles", help="List aliases without credentials or account numbers")
    add = actions.add_parser("add-profile", help="Save a new named local profile")
    add.add_argument("--broker", required=True, choices=("schwab", "etrade"))
    add.add_argument("--login", required=True, help="Local login alias, not the broker username")
    add.add_argument("--environment", required=True, choices=("production", "sandbox"))
    add.add_argument("--directory", required=True)
    add.add_argument("--expected-account-last4", help="Optional extra account-selection check")
    bind = actions.add_parser("bind-account", help="Bind one exact account from a saved inventory")
    bind.add_argument("--accounts-file", required=True)
    for name in ("status", "auth-start", "auth-finish", "renew"):
        actions.add_parser(name)
    ingest = actions.add_parser("ingest-chain", help="Import saved quotes with shared calculations")
    ingest.add_argument("--db", required=True)
    ingest.add_argument("--chain-file", required=True)
    ingest.add_argument("--quotes-file", required=True)
    for name in ("accounts", "positions", "balance", "quotes", "expiries", "chain"):
        command = actions.add_parser(name)
        command.add_argument("--output", help="New private JSON snapshot path")
        if name == "quotes":
            command.add_argument("--symbols", nargs="+", required=True)
        elif name in ("chain", "expiries"):
            command.add_argument("--symbol", required=True)
        if name == "chain":
            command.add_argument("--expiry", help="Required for E*TRADE; omit for Schwab")
    parser.set_defaults(handler=_handler)
