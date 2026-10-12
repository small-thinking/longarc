"""Explicit order operations; local planning is the default, never scheduled trading."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from longarc.schwab.auth import OAuthSession, read_private_json, write_private_json
from longarc.schwab.client import SchwabError, SchwabReadOnlyClient
from longarc.schwab.orders import (
    SchwabOrderClient,
    build_limit_call_order,
    validate_order_spec,
)

_ACTIONS = {
    "order-plan",
    "order-preview",
    "order-submit",
    "order-replace",
    "order-cancel",
    "order-status",
}
_TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"}


def _private(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to((Path.cwd() / "private").resolve()):
        raise SchwabError("Order files must stay under ./private/.")
    return resolved


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise SchwabError("Missing or invalid broker quantity/value.")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise SchwabError("Missing or invalid broker quantity/value.") from None
    if not result.is_finite() or result < 0:
        raise SchwabError("Missing or invalid broker quantity/value.")
    return result


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchwabError("Missing or invalid broker object.")
    return value


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SchwabError("Missing or invalid broker rows.")
    return value


def _contract_rows(chain: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for strikes in _object(chain.get("callExpDateMap")).values():
        for rows in _object(strikes).values():
            for row in _rows(rows):
                symbol = row.get("symbol")
                if not isinstance(symbol, str) or symbol in result:
                    raise SchwabError("Missing or duplicate option identity.")
                result[symbol] = row
    return result


def _standard(row: dict[str, Any], underlying: str) -> None:
    deliveries = _rows(row.get("optionDeliverablesList"))
    if (
        row.get("putCall") != "CALL"
        or row.get("multiplier") != 100
        or row.get("nonStandard") is not False
        or row.get("mini") is not False
        or len(deliveries) != 1
        or deliveries[0].get("symbol") != underlying
        or deliveries[0].get("deliverableUnits") != 100
    ):
        raise SchwabError("Only verified standard 100-share CALLs are supported.")


def _pending_legs(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legs = []
    for order in orders:
        # Children may still be contingent even when their parent is terminal.
        if "childOrderStrategies" in order:
            legs.extend(_pending_legs(_rows(order["childOrderStrategies"])))
        status = order.get("status")
        if not isinstance(status, str):
            raise SchwabError("Order status missing; reservations cannot be verified.")
        if status in _TERMINAL:
            continue
        remaining = order.get("remainingQuantity", order.get("quantity"))
        for leg in _rows(order.get("orderLegCollection")):
            # Reserve all leg quantity if an exact remaining size is unavailable.
            quantity = max(_decimal(leg.get("quantity")), _decimal(remaining))
            legs.append({**leg, "reserved_quantity": quantity})
    return legs


def check_collection_stability(
    before: dict[str, Any],
    after: dict[str, Any],
    orders: list[dict[str, Any]],
    started_at: datetime,
    underlying: str,
) -> None:
    """Stop on cross-read position changes or newly filled relevant orders."""

    def quantities(account: dict[str, Any]) -> list[str]:
        values = []
        for position in _rows(account.get("positions")):
            instrument = _object(position.get("instrument"))
            values.append(
                json.dumps(
                    {
                        "instrument": {
                            key: instrument.get(key)
                            for key in ("symbol", "assetType", "underlyingSymbol", "putCall")
                        },
                        "long": str(_decimal(position.get("longQuantity", 0))),
                        "short": str(_decimal(position.get("shortQuantity"))),
                    },
                    sort_keys=True,
                )
            )
        return sorted(values)

    if quantities(before) != quantities(after):
        raise SchwabError("Positions changed during preflight; collect and review again.")
    for order in orders:
        if "childOrderStrategies" in order:
            check_collection_stability(
                before, after, _rows(order["childOrderStrategies"]), started_at, underlying
            )
        if order.get("status") != "FILLED":
            continue
        relevant = False
        for leg in _rows(order.get("orderLegCollection")):
            instrument = _object(leg.get("instrument"))
            symbol = instrument.get("symbol")
            if not isinstance(symbol, str):
                raise SchwabError("Filled order identity cannot be reconciled.")
            relevant |= symbol == underlying or symbol[:6].strip() == underlying
        if not relevant:
            continue
        closed = order.get("closeTime")
        if not isinstance(closed, str):
            raise SchwabError("Filled order clock missing; position reconciliation is required.")
        try:
            closed_at = datetime.fromisoformat(closed.replace("Z", "+00:00"))
        except ValueError:
            raise SchwabError("Filled order clock invalid; reconcile before submission.") from None
        if closed_at.tzinfo is None or closed_at >= started_at:
            raise SchwabError("An order filled during preflight; reconcile before submission.")


def check_preflight(
    spec: dict[str, Any],
    account: dict[str, Any],
    orders: list[dict[str, Any]],
    chain: dict[str, Any],
    calendar: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Check fresh broker resources, never caller-supplied coverage approval flags."""
    spec = validate_order_spec(spec)
    leg = spec["orderLegCollection"][0]
    symbol = leg["instrument"]["symbol"]
    underlying = symbol[:6].strip()
    if chain.get("isDelayed") is not False or chain.get("isChainTruncated") is not False:
        raise SchwabError("Live preflight requires a non-delayed, untruncated CALL chain.")
    contracts = _contract_rows(chain)
    row = contracts.get(symbol)
    if row is None:
        raise SchwabError("Exact option was not found in the current CALL chain.")
    _standard(row, underlying)
    expiry = datetime.fromisoformat(str(row.get("expirationDate")).replace("Z", "+00:00"))
    quote_at = datetime.fromtimestamp(float(_decimal(row.get("quoteTimeInLong"))) / 1000, UTC)
    if expiry.tzinfo is None or expiry <= now or not 0 <= (now - quote_at).total_seconds() <= 60:
        raise SchwabError("Option expired or current source quote is not within 60 seconds.")
    equity = _object(_object(calendar.get("equity")).get("EQ"))
    sessions = _rows(_object(equity.get("sessionHours")).get("regularMarket"))
    if equity.get("isOpen") is not True or not any(
        datetime.fromisoformat(s["start"]) <= now < datetime.fromisoformat(s["end"])
        for s in sessions
    ):
        raise SchwabError("Order submission is restricted to the regular equity session.")
    positions = _rows(account.get("positions"))
    pending = _pending_legs(orders)
    shares = Decimal(0)
    short_calls = Decimal(0)
    target_shorts = Decimal(0)
    for position in positions:
        instrument = _object(position.get("instrument"))
        short = _decimal(position.get("shortQuantity"))
        if (
            short
            and instrument.get("assetType") == "OPTION"
            and (
                not isinstance(instrument.get("underlyingSymbol"), str)
                or instrument.get("putCall") not in {"CALL", "PUT"}
                or not isinstance(instrument.get("symbol"), str)
            )
        ):
            raise SchwabError("A short derivative position has unresolved identity.")
        if short and instrument.get("assetType") is None:
            raise SchwabError("A short position has unresolved asset type.")
        if (
            short
            and instrument.get("assetType") != "OPTION"
            and (
                instrument.get("underlyingSymbol") is not None
                or instrument.get("putCall") is not None
                or (isinstance(instrument.get("symbol"), str) and len(instrument["symbol"]) == 21)
            )
        ):
            raise SchwabError("A short derivative has an unknown asset type.")
        if instrument.get("symbol") == underlying:
            shares += _decimal(position.get("longQuantity"))
            shares -= _decimal(position.get("shortQuantity"))
        elif (
            instrument.get("underlyingSymbol") == underlying
            and instrument.get("assetType") == "OPTION"
            and instrument.get("putCall") == "CALL"
        ):
            held_symbol = instrument.get("symbol")
            if not isinstance(held_symbol, str):
                raise SchwabError("An existing option identity is missing.")
            held = contracts.get(held_symbol)
            if short:
                if held is None:
                    raise SchwabError("An existing short CALL cannot be verified in the chain.")
                _standard(held, underlying)
                short_calls += short
                if held_symbol == symbol:
                    target_shorts += short
    reserved_sto = Decimal(0)
    reserved_btc = Decimal(0)
    for pending_leg in pending:
        instrument = _object(pending_leg.get("instrument"))
        pending_symbol = instrument.get("symbol")
        if not isinstance(pending_symbol, str):
            raise SchwabError("Pending order instrument identity is missing.")
        if pending_symbol == underlying and pending_leg.get("instruction") not in {
            "BUY",
            "SELL",
            "SELL_SHORT",
            "BUY_TO_COVER",
        }:
            raise SchwabError("A pending stock order side is unresolved.")
        if (
            pending_symbol[:6].strip() == underlying
            and pending_symbol != underlying
            and (
                instrument.get("assetType") != "OPTION"
                or pending_leg.get("instruction")
                not in {"SELL_TO_OPEN", "BUY_TO_CLOSE", "BUY_TO_OPEN", "SELL_TO_CLOSE"}
            )
        ):
            raise SchwabError("A pending derivative order has unresolved identity or side.")
        if instrument.get("assetType") == "OPTION" and pending_symbol[:6].strip() == underlying:
            if pending_leg.get("instruction") == "SELL_TO_OPEN":
                existing = contracts.get(pending_symbol)
                if existing is None:
                    raise SchwabError("Pending option deliverables cannot be verified.")
                _standard(existing, underlying)
                reserved_sto += pending_leg["reserved_quantity"]
            elif pending_leg.get("instruction") == "BUY_TO_CLOSE" and pending_symbol == symbol:
                reserved_btc += pending_leg["reserved_quantity"]
        elif instrument.get("symbol") == underlying and pending_leg.get("instruction") in {
            "SELL",
            "SELL_SHORT",
        }:
            shares -= pending_leg["reserved_quantity"]
    quantity = Decimal(leg["quantity"])
    free_shares = shares - (short_calls + reserved_sto) * 100
    if leg["instruction"] == "SELL_TO_OPEN" and free_shares < quantity * 100:
        raise SchwabError("Insufficient unreserved shares for this covered CALL.")
    if leg["instruction"] == "BUY_TO_CLOSE":
        cash = _decimal(_object(account.get("currentBalances")).get("cashAvailableForTrading"))
        if target_shorts - reserved_btc < quantity:
            raise SchwabError("Insufficient unreserved short quantity to buy to close.")
        if cash < Decimal(spec["price"]) * quantity * 100:
            raise SchwabError(
                "Insufficient cash for the limit buyback premium; fees are additional."
            )
    return {
        "checked_at": now.isoformat(),
        "holdings_and_orders_coverage_checked": True,
        "unreserved_shares": str(free_shares),
        "current_target_short_contracts": str(target_shorts),
        "pending_sto_contracts": str(reserved_sto),
        "pending_btc_contracts": str(reserved_btc),
        "quote_at": quote_at.isoformat(),
        "strategy_approval": False,
        "limitations": [
            "Broker resources are sequential and may change before submission.",
            "Execution preflight is not a strategy recommendation.",
        ],
    }


def _load_spec(path: str) -> dict[str, Any]:
    doc = read_private_json(_private(path))
    if doc.get("format") == "schwab-limit-call-plan-v1":
        return validate_order_spec(_object(doc.get("order")))
    return build_limit_call_order(doc)


def _digest(request: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def run(args: argparse.Namespace) -> int:
    action = args.schwab_action
    directory = _private(args.directory)
    profile_file = directory / "trading.json"
    profile = read_private_json(profile_file) if profile_file.exists() else {}
    spec = _load_spec(args.file) if getattr(args, "file", None) else None
    order_id = getattr(args, "order_id", None)
    if args.account_index < 0:
        raise SchwabError("Account index must be nonnegative.")
    if order_id is not None and not re.fullmatch(r"[1-9][0-9]{0,19}", order_id):
        raise SchwabError("Invalid order ID.")
    request = {
        "operation": action,
        "account_index": getattr(args, "account_index", None),
        "account_hash": profile.get("account_hash"),
        "order_id": order_id,
        "order": spec,
    }
    confirmation = _digest(request)
    planned = {
        "format": "schwab-limit-call-plan-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "operation": action,
        "order": spec,
        "confirmation_digest": confirmation,
        "network_used": False,
        "outcome": "LOCAL_PLAN",
        "broker_validated": False,
        "warnings": [
            "Not a broker preview, accepted order, or fill.",
            "An OTM or remote limit order can still fill.",
        ],
    }
    output = (
        _private(args.output)
        if getattr(args, "output", None)
        else (
            directory / "orders" / "plans" / f"{action}-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
        )
    )
    if output.exists() or (getattr(args, "file", None) and output == _private(args.file)):
        raise SchwabError("Output must be a new private file.")
    if action == "order-plan" or not args.live:
        write_private_json(output, planned, overwrite=False)
        print(
            json.dumps(
                {
                    "outcome": "LOCAL_PLAN",
                    "network_used": False,
                    "output": str(output),
                    "confirmation_digest": confirmation,
                }
            )
        )
        return 0
    index = args.account_index
    mutation = action != "order-status"
    if mutation:
        if profile.get("enabled") is not True or not isinstance(profile.get("account_hash"), str):
            raise SchwabError(
                "Live orders are disabled; an explicit private trading profile is required."
            )
        if args.confirm_digest != confirmation:
            raise SchwabError("Live request digest must match the exact locally reviewed request.")
        if action in {"order-submit", "order-replace"} and (
            profile.get("open_orders_window_verified") is not True
        ):
            raise SchwabError(
                "Submission blocked: verify complete open-order query coverage first."
            )
        maximum = profile.get("max_contracts")
        if spec is not None and (
            type(maximum) is not int
            or maximum < 1
            or spec["orderLegCollection"][0]["quantity"] > maximum
        ):
            raise SchwabError("Order exceeds the explicit profile contract limit.")
        if spec and spec["orderLegCollection"][0]["instruction"] == "BUY_TO_CLOSE":
            maximum_debit = profile.get("max_buy_debit_u")
            debit = Decimal(spec["price"]) * spec["orderLegCollection"][0]["quantity"] * 100000000
            if type(maximum_debit) is not int or maximum_debit < 1 or debit > maximum_debit:
                raise SchwabError("Buyback exceeds the explicit profile premium debit limit.")
        attempt = args.attempt_id
        if not isinstance(attempt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", attempt):
            raise SchwabError("A unique attempt ID is required for live operations.")
        attempt_dir = directory / "orders" / "attempts" / attempt
        attempt_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            attempt_dir.mkdir(mode=0o700)
        except FileExistsError:
            raise SchwabError(
                "Attempt already exists; inspect/reconcile it, never resend automatically."
            ) from None
        # This durable intent exists before OAuth refresh, broker reads, or writes.
        write_private_json(
            attempt_dir / "intent.json",
            {
                "request": request,
                "confirmation_digest": confirmation,
                "outcome": "INTENT_RECORDED",
                "created_at": datetime.now(UTC).isoformat(),
            },
            overwrite=False,
        )
    else:
        attempt_dir = None
    try:
        token = OAuthSession(directory).access_token()
        reads = SchwabReadOnlyClient(token)
        numbers = _rows(reads.account_numbers())
        if not 0 <= index < len(numbers):
            raise SchwabError("Account index out of range.")
        account_hash = numbers[index].get("hashValue")
        if not isinstance(account_hash, str):
            raise SchwabError("Account hash missing.")
        if profile.get("account_hash") is not None and account_hash != profile["account_hash"]:
            raise SchwabError("Selected account does not match the pinned trading profile.")
        client = SchwabOrderClient(token, account_hash, enabled=mutation)
        preflight_started = datetime.now(UTC)
        result: dict[str, Any]
        if action == "order-status":
            assert isinstance(order_id, str)
            result = client.get_order(order_id)
        else:
            assert attempt_dir is not None
            prior = None
            if action in {"order-replace", "order-cancel"}:
                assert isinstance(order_id, str)
                prior = client.get_order(order_id)
                write_private_json(attempt_dir / "target-order.json", prior, overwrite=False)
                if prior.get("outcome") != "OBSERVED" or prior.get("terminal") is not False:
                    raise SchwabError(
                        "Target order is terminal or unverified; no mutation was sent."
                    )
                if action == "order-replace":
                    assert spec is not None
                    original = _object(prior.get("raw_order"))
                    old_legs = _rows(original.get("orderLegCollection"))
                    remaining = _object(prior.get("order")).get("remainingQuantity")
                    filled = _object(prior.get("order")).get("filledQuantity")
                    new_leg = spec["orderLegCollection"][0]
                    if (
                        original.get("orderStrategyType") != "SINGLE"
                        or len(old_legs) != 1
                        or old_legs[0].get("instruction") != new_leg["instruction"]
                        or _object(old_legs[0].get("instrument")).get("symbol")
                        != new_leg["instrument"]["symbol"]
                        or _decimal(remaining) < new_leg["quantity"]
                        or filled != 0
                    ):
                        raise SchwabError(
                            "Replace only an unfilled same-leg order within remaining size."
                        )
            if action in {"order-submit", "order-replace"}:
                assert spec is not None
                selected_number = numbers[index].get("accountNumber")
                time.sleep(1)
                accounts = _rows(reads.accounts())
                matches = [
                    a["securitiesAccount"]
                    for a in accounts
                    if _object(a.get("securitiesAccount")).get("accountNumber") == selected_number
                ]
                if selected_number is None or len(matches) != 1:
                    raise SchwabError("Selected account positions could not be matched.")
                underlying = spec["orderLegCollection"][0]["instrument"]["symbol"][:6].strip()
                time.sleep(1)
                chain = _object(reads.option_chain(underlying))
                time.sleep(1)
                current = datetime.now(UTC)
                orders = _rows(
                    reads.orders(
                        account_hash,
                        (current - timedelta(days=190)).isoformat(),
                        current.isoformat(),
                    )
                )
                if len(orders) >= 3000:
                    raise SchwabError("Order-list completeness is uncertain; submission blocked.")
                if action == "order-replace":
                    targets = [o for o in orders if str(o.get("orderId")) == order_id]
                    if len(targets) != 1:
                        raise SchwabError(
                            "Replacement target missing from the verified order list."
                        )
                    if (
                        targets[0].get("status") in _TERMINAL
                        or targets[0].get("filledQuantity") != 0
                        or _decimal(targets[0].get("remainingQuantity"))
                        < spec["orderLegCollection"][0]["quantity"]
                    ):
                        raise SchwabError("Replacement target changed or partially filled.")
                    orders = [o for o in orders if str(o.get("orderId")) != order_id]
                time.sleep(1)
                calendar = _object(reads.market_hours(current.date().isoformat()))
                time.sleep(1)
                refreshed = _rows(reads.accounts())
                refreshed_matches = [
                    a["securitiesAccount"]
                    for a in refreshed
                    if _object(a.get("securitiesAccount")).get("accountNumber") == selected_number
                ]
                if len(refreshed_matches) != 1:
                    raise SchwabError("Refreshed positions could not be matched.")
                check_collection_stability(
                    _object(matches[0]),
                    _object(refreshed_matches[0]),
                    orders,
                    preflight_started,
                    underlying,
                )
                preflight = check_preflight(
                    spec, _object(refreshed_matches[0]), orders, chain, calendar, datetime.now(UTC)
                )
                write_private_json(
                    attempt_dir / "preflight.json",
                    {
                        "result": preflight,
                        "account": refreshed_matches[0],
                        "orders": orders,
                        "chain": chain,
                        "calendar": calendar,
                    },
                    overwrite=False,
                )
            write_private_json(attempt_dir / "request-started.json", request, overwrite=False)
            if action == "order-preview":
                assert spec is not None
                result = client.preview_order(spec)
            elif action == "order-submit":
                assert spec is not None
                result = client.submit_order(spec)
            elif action == "order-replace":
                assert spec is not None
                assert isinstance(order_id, str)
                result = client.replace_order(order_id, spec)
            else:
                assert isinstance(order_id, str)
                result = client.cancel_order(order_id)
            write_private_json(attempt_dir / "response.json", result, overwrite=False)
            known_id = result.get("order_id")
            if known_id and action != "order-preview":
                time.sleep(1)
                result = {
                    "mutation": result,
                    "status_readback": client.get_order(known_id),
                    "no_execution_journal_write": True,
                }
            write_private_json(attempt_dir / "result.json", result, overwrite=False)
        write_private_json(
            output,
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "operation": action,
                "network_used": True,
                "result": result,
            },
            overwrite=False,
        )
        print(
            json.dumps(
                {
                    "outcome": "RESULT_SAVED",
                    "output": str(output),
                    "attempt_directory": str(attempt_dir) if attempt_dir else None,
                }
            )
        )
        reported = result.get("mutation", result)
        readback = result.get("status_readback", {})
        return (
            1
            if reported.get("outcome") in {"UNKNOWN", "REJECTED", "UNAVAILABLE"}
            or readback.get("outcome") in {"UNKNOWN", "UNAVAILABLE"}
            else 0
        )
    except Exception:
        if attempt_dir is not None:
            write_private_json(
                attempt_dir / "failure.json",
                {
                    "outcome": "STOPPED",
                    "reconciliation_required": True,
                    "instruction": "Inspect saved attempt and broker state before any new attempt.",
                },
            )
        raise


def add_parsers(actions: Any) -> None:
    for name in sorted(_ACTIONS):
        command = actions.add_parser(name, help="Local order plan; broker access only with --live")
        if name in {"order-plan", "order-preview", "order-submit", "order-replace"}:
            command.add_argument(
                "--file", required=True, help="Private intent or generated plan JSON"
            )
        if name in {"order-cancel", "order-status", "order-replace"}:
            command.add_argument("--order-id", required=True, help="Exact broker order ID")
        command.add_argument("--output", help="New private JSON destination")
        command.add_argument("--account-index", type=int, default=0)
        command.add_argument(
            "--live", action="store_true", help="Explicit broker access; default offline"
        )
        command.add_argument(
            "--confirm-digest", help="Exact digest from this operation's local plan"
        )
        command.add_argument(
            "--attempt-id", help="Unique durable live-operation attempt identifier"
        )
