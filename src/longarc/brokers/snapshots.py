"""Broker response adapters for explicit account inventories and current positions."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from longarc.brokers.profiles import BrokerProfile


def _quantity(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return str(number) if number.is_finite() else None


def account_inventory(profile: BrokerProfile, raw: Any) -> list[dict[str, Any]]:
    if profile.broker == "schwab":
        accounts = raw
    else:
        root = raw.get("AccountListResponse") if isinstance(raw, dict) else None
        group = root.get("Accounts", root.get("accounts")) if isinstance(root, dict) else None
        accounts = group.get("Account", group.get("account")) if isinstance(group, dict) else None
    if not isinstance(accounts, list):
        raise ValueError("Broker account inventory is missing or malformed.")
    result = []
    seen = set()
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("Malformed account inventory row.")
        key = account.get("hashValue" if profile.broker == "schwab" else "accountIdKey")
        identifier = account.get("accountNumber" if profile.broker == "schwab" else "accountId")
        if isinstance(identifier, int) and not isinstance(identifier, bool):
            identifier = str(identifier)
        if not isinstance(key, str) or not key or not isinstance(identifier, str) or not identifier:
            raise ValueError("Broker inventory lacks an exact account key or ID.")
        if key in seen:
            raise ValueError("Broker inventory contains duplicate account keys.")
        seen.add(key)
        result.append(
            {
                **profile.identity(),
                "account_key": key,
                "account_id": identifier,
                "description": account.get("accountDesc"),
                "account_type": account.get("accountType"),
                "account_status": account.get("accountStatus"),
            }
        )
    return result


def verify_balance_account(profile: BrokerProfile, raw: Any) -> None:
    profile.require_account()
    if profile.broker == "schwab":
        body = raw.get("securitiesAccount") if isinstance(raw, dict) else None
        identifier = body.get("accountNumber") if isinstance(body, dict) else None
    else:
        body = raw.get("BalanceResponse") if isinstance(raw, dict) else None
        identifier = body.get("accountId") if isinstance(body, dict) else None
    if identifier is None or str(identifier) != profile.account_id:
        raise ValueError("Balance response belongs to a different or unidentified account.")


def normalize_positions(profile: BrokerProfile, raw: Any) -> dict[str, Any]:
    profile.require_account()
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if profile.broker == "schwab":
        account = raw.get("securitiesAccount") if isinstance(raw, dict) else None
        if not isinstance(account, dict) or str(account.get("accountNumber")) != profile.account_id:
            raise ValueError("Schwab position response belongs to a different account.")
        positions = account.get("positions", [])
        if not isinstance(positions, list):
            raise ValueError("Malformed Schwab positions.")
        for position in positions:
            if not isinstance(position, dict) or not isinstance(position.get("instrument"), dict):
                raise ValueError("Malformed Schwab position row.")
            instrument = position["instrument"]
            long = _quantity(position.get("longQuantity"))
            short = _quantity(position.get("shortQuantity"))
            net = (
                str(Decimal(long) - Decimal(short))
                if long is not None
                and short is not None
                and Decimal(long) >= 0
                and Decimal(short) >= 0
                else None
            )
            if net is None:
                rejected.append(
                    {"symbol": instrument.get("symbol"), "reason": "unknown_signed_quantity"}
                )
            rows.append(
                {
                    "symbol": instrument.get("symbol"),
                    "asset_type": instrument.get("assetType"),
                    "quantity": net,
                    "long_quantity": long,
                    "short_quantity": short,
                    "put_call": instrument.get("putCall"),
                    "average_price": position.get("averagePrice"),
                    "market_value": position.get("marketValue"),
                    "raw": position,
                }
            )
    else:
        if not isinstance(raw, list):
            raise ValueError("E*TRADE positions require the complete list of portfolio pages.")
        seen_ids: set[str] = set()
        for page in raw:
            root = page.get("PortfolioResponse") if isinstance(page, dict) else None
            accounts = (
                root.get("AccountPortfolio", root.get("accountPortfolio"))
                if isinstance(root, dict)
                else None
            )
            if (
                not isinstance(accounts, list)
                or len(accounts) != 1
                or not isinstance(accounts[0], dict)
            ):
                raise ValueError("Malformed E*TRADE portfolio page.")
            account = accounts[0]
            if str(account.get("accountId")) != profile.account_id:
                raise ValueError("E*TRADE portfolio belongs to a different account.")
            positions = account.get("Position", account.get("position", []))
            if not isinstance(positions, list):
                raise ValueError("Malformed E*TRADE position rows.")
            for position in positions:
                if not isinstance(position, dict):
                    raise ValueError("Malformed E*TRADE position.")
                identifier = position.get("positionId")
                if identifier is not None:
                    if str(identifier) in seen_ids:
                        raise ValueError("Repeated E*TRADE position across portfolio pages.")
                    seen_ids.add(str(identifier))
                product = position.get("Product", position.get("product"))
                if not isinstance(product, dict):
                    raise ValueError("E*TRADE position lacks product identity.")
                quantity = _quantity(position.get("quantity"))
                direction = position.get("positionType")
                net = None
                if quantity is not None and direction in ("LONG", "SHORT"):
                    number = Decimal(quantity)
                    if number >= 0:
                        net = str(number if direction == "LONG" else -number)
                if net is None:
                    rejected.append(
                        {"position_id": identifier, "reason": "unknown_signed_quantity"}
                    )
                rows.append(
                    {
                        "symbol": product.get("symbol"),
                        "asset_type": product.get("securityType"),
                        "quantity": net,
                        "put_call": product.get("callPut"),
                        "contract": product,
                        "average_price": position.get("pricePaid"),
                        "market_value": position.get("marketValue"),
                        "raw": position,
                    }
                )
    return {
        "format": "broker-positions-v1",
        **profile.identity(),
        "positions": rows,
        "rejected": rejected,
        "actual_fills_reconciled": False,
        "coverage_reservations_checked": False,
        "warnings": ["positions_are_not_execution_history", "account_coverage_not_trade_approval"],
    }
