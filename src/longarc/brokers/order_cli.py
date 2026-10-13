"""Named E*TRADE order plans and explicitly requested GET order observations.

No command in this module enables order writes, even when credentials exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from longarc.brokers.profiles import load_profiles, private_path
from longarc.core.private_files import PrivateFileError, read_private_json, write_private_json
from longarc.etrade.client import ETradeError
from longarc.etrade.orders import ETradeOrderClient, _id, build_limit_call_order

_OPERATIONS = {"preview", "place", "change-preview", "change-place", "cancel"}


def _run(args: argparse.Namespace) -> int:
    profiles = load_profiles(private_path(args.profiles_file))
    if not args.profile or args.profile not in profiles:
        raise ValueError("Select an explicit configured --profile")
    profile = profiles[args.profile]
    if profile.broker != "etrade":
        raise ValueError("Use the existing schwab order-* commands for Schwab")
    profile.require_account()  # Binding must precede credential/OAuth access.
    assert profile.account_id is not None
    output = private_path(args.output)
    if output.exists():
        raise ValueError("Order evidence output must be a new private file")
    if output.name in {"app.json", "token.json", "pending-auth.json", "authorization.json",
                       "trading.json", "brokers.json"}:
        raise ValueError("Order evidence cannot overwrite broker configuration")
    action = args.broker_action
    if action == "order-plan":
        if args.operation not in _OPERATIONS:
            raise ValueError("Unsupported E*TRADE order operation")
        if args.operation in {"cancel", "change-preview", "change-place"}:
            current: str | None = _id(args.order_id)
        elif args.order_id is not None:
            raise ValueError("Only changes and cancellations take --order-id")
        else:
            current = None
        spec = None
        if args.operation != "cancel":
            if not args.file or not args.client_order_id:
                raise ValueError("CALL plans require --file and --client-order-id")
            intent = read_private_json(private_path(args.file))
            spec = build_limit_call_order(intent, args.client_order_id)
        elif args.file or args.client_order_id:
            raise ValueError("Cancellation does not take CALL terms or a client ID")
        plan: dict[str, Any] = {
            "format": "etrade-order-plan-v1", "identity": profile.identity(),
            "operation": args.operation, "order_id": current, "spec": spec,
            "network_request_made": False, "live_write_available": False,
            "requires_preview": args.operation in {"place", "change-place"},
            "requires_fresh_preflight": True,
        }
        plan["request_digest"] = hashlib.sha256(
            json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        write_private_json(output, plan, overwrite=False)
        print(f"Local E*TRADE order plan saved: {output}; no OAuth or broker request")
        return 0
    if action not in {"orders", "order-status"}:
        raise ValueError("Unsupported order observation")
    current = _id(args.order_id) if action == "order-status" else None
    # Reuse the ordinary profile scope and renewal, never a separate login/config.
    from longarc.brokers.cli import _session

    session = _session(profile)
    token = session.renew()
    client = ETradeOrderClient(session.config, token, profile.require_account(), profile.account_id)
    result = client.list_orders() if current is None else client.get_order(current)
    write_private_json(output, {
        "provider": "etrade-api", "resource": action, "request": profile.identity(),
        "captured_at": datetime.now(UTC).isoformat(), "data": result,
        "synthetic": profile.environment == "sandbox", "order_writes_enabled": False,
    }, overwrite=False)
    print(f"E*TRADE GET order observation saved: {output}; no order write")
    return 0 if result["outcome"] == "OBSERVED" else 1


def _handler(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except (ETradeError, PrivateFileError, ValueError, OSError, KeyError, TypeError):
        print("E*TRADE order operation failed; check profile, binding, session and private input")
        return 1


def add_parsers(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plan = actions.add_parser("order-plan", help="Local E*TRADE limit CALL plan; no broker call")
    plan.add_argument("--operation", choices=sorted(_OPERATIONS), default="preview")
    plan.add_argument("--file", help="Private shared CALL intent JSON")
    plan.add_argument("--client-order-id", help="Unique 1-20 alphanumeric developer ID")
    plan.add_argument("--order-id", help="Existing order ID for change/cancel")
    plan.add_argument("--output", required=True, help="New private JSON plan")
    plan.set_defaults(handler=_handler)
    for name in ("orders", "order-status"):
        command = actions.add_parser(name, help="Explicit read-only E*TRADE order observation")
        command.add_argument("--output", required=True, help="New private JSON observation")
        if name == "order-status":
            command.add_argument("--order-id", required=True)
        command.set_defaults(handler=_handler)
