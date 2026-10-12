"""Local Schwab setup and private, read-only JSON snapshots."""

from __future__ import annotations

import argparse
import getpass
import json
from datetime import UTC, datetime
from pathlib import Path

from longarc.schwab.auth import (
    DEFAULT_REDIRECT,
    AppConfig,
    OAuthSession,
    read_private_json,
    write_private_json,
)
from longarc.schwab.client import SchwabError, SchwabReadOnlyClient


def _private_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((Path.cwd() / "private").resolve()):
        raise SchwabError("Schwab credentials and snapshots must stay under ./private/.")
    return resolved


def _run(args: argparse.Namespace) -> int:
    directory = _private_path(Path(args.directory))
    requested_output = _private_path(Path(args.output)) if getattr(args, "output", None) else None
    if requested_output in {
        directory / name
        for name in (
            "app.json",
            "token.json",
            "pending-auth.json",
            "authorization.json",
        )
    }:
        raise SchwabError("Snapshot output must not overwrite Schwab authorization files.")
    if requested_output and requested_output.exists():
        raise SchwabError("Snapshot output already exists; choose a new private file.")
    action = args.schwab_action
    if action == "configure":
        config = AppConfig(
            getpass.getpass("App Client ID (hidden): ").strip(),
            getpass.getpass("App Client Secret (hidden): ").strip(),
            args.callback,
        )
        write_private_json(
            directory / "app.json",
            {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "redirect_uri": config.redirect_uri,
            },
        )
        print("App configuration saved privately. Next: schwab auth-start.")
        return 0
    if action == "status":
        AppConfig.load(directory / "app.json")
        authorized = (directory / "token.json").exists()
        result: dict[str, object] = {"configured": True, "token_present": authorized}
        if authorized:
            token = read_private_json(directory / "token.json")
            for name in ("expires_at", "refresh_created_at"):
                result[name] = token.get(name)
        print(json.dumps(result))
        return 0
    session = OAuthSession(directory)
    if action == "auth-start":
        url = session.start()
        path = directory / "authorization.json"
        write_private_json(path, {"authorization_url": url})
        print(f"Authorization URL saved privately: {path}")
        print("Open that URL, review Schwab's consent screen and select your account(s).")
        print("Then run schwab auth-finish and paste the full redirected local URL.")
        print("The callback may show a connection error; no local server is required.")
        return 0
    if action == "auth-finish":
        session.finish(getpass.getpass("Full redirected callback URL (hidden): "))
        (directory / "authorization.json").unlink(missing_ok=True)
        print("OAuth completed; tokens saved privately. Account reads are now available.")
        return 0

    client = SchwabReadOnlyClient(session.access_token())
    if action == "accounts":
        data = client.accounts()
    elif action == "account-numbers":
        data = client.account_numbers()
    elif action == "quotes":
        data = client.quotes(args.symbols)
    elif action == "chain":
        data = client.option_chain(args.symbol)
    else:
        numbers = client.account_numbers()
        index = args.account_index
        if not isinstance(numbers, list) or not 0 <= index < len(numbers):
            raise SchwabError("Account index is out of range; inspect account-numbers snapshot.")
        account = numbers[index]
        if not isinstance(account, dict) or not isinstance(account.get("hashValue"), str):
            raise SchwabError("Schwab account lookup did not provide an account hash.")
        if action == "transactions":
            data = client.transactions(account["hashValue"], args.start, args.end)
        else:
            data = client.orders(account["hashValue"], args.start, args.end)
    captured_at = datetime.now(UTC)
    output = requested_output or (
        directory / "snapshots" / f"{action}-{captured_at:%Y%m%dT%H%M%S%fZ}.json"
    )
    write_private_json(
        output,
        {
            "provider": "schwab-api",
            "captured_at": captured_at.isoformat(),
            "resource": action,
            "request": {
                name: getattr(args, name)
                for name in ("symbols", "symbol", "start", "end", "account_index")
                if hasattr(args, name)
            },
            "data": data,
        },
        overwrite=False,
    )
    print(f"Read-only Schwab snapshot saved: {output}")
    return 0


def _handler(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except (SchwabError, OSError, EOFError, ValueError) as error:
        # Do not emit raw exception chains, credentials or account response bodies.
        # SchwabError's messages are designed to be safe and actionable.
        print(
            str(error)
            if isinstance(error, SchwabError)
            else "Local Schwab operation could not complete; check private files and input."
        )
        return 1


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("schwab", help="Local OAuth and read-only Schwab snapshots")
    parser.add_argument(
        "--directory", default="private/schwab", help="Private credential directory"
    )
    actions = parser.add_subparsers(dest="schwab_action", required=True)
    config = actions.add_parser("configure", help="Enter App credentials without echo")
    config.add_argument("--callback", default=DEFAULT_REDIRECT)
    actions.add_parser("status", help="Show setup status without credentials or account data")
    actions.add_parser("auth-start", help="Prepare browser consent URL in a private file")
    actions.add_parser("auth-finish", help="Exchange redirected URL entered without echo")
    for name in ("accounts", "account-numbers", "quotes", "chain", "transactions", "orders"):
        command = actions.add_parser(name, help=f"Save read-only {name} snapshot")
        command.add_argument("--output", help="Private JSON destination (default: dated snapshot)")
        if name == "quotes":
            command.add_argument("--symbols", nargs="+", required=True)
        elif name == "chain":
            command.add_argument("--symbol", required=True)
        elif name in ("transactions", "orders"):
            command.add_argument(
                "--account-index",
                type=int,
                required=True,
                help="Explicit index in the linked account list",
            )
            command.add_argument("--start", required=True, help="ISO timestamp with timezone")
            command.add_argument("--end", required=True, help="ISO timestamp with timezone")
    parser.set_defaults(handler=_handler)
