"""Shared underlying-quote and expiry views without merging broker entitlements."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from longarc.brokers.profiles import BrokerProfile
from longarc.core.market_fields import epoch_time, money_u
from longarc.core.symbols import canonical_symbol


def normalize_quotes(profile: BrokerProfile, raw: Any, symbols: list[str]) -> dict[str, Any]:
    requested = {canonical_symbol(symbol.upper()) for symbol in symbols}
    rows = []
    seen = set()
    if profile.broker == "schwab":
        if not isinstance(raw, dict) or not set(raw) <= requested:
            raise ValueError("Schwab quotes include unexpected symbols.")
        candidates = []
        for symbol, item in raw.items():
            if not isinstance(item, dict) or canonical_symbol(item.get("symbol")) != symbol:
                raise ValueError("Schwab quote identity conflicts with request.")
            quote = item.get("quote")
            if not isinstance(quote, dict):
                raise ValueError("Schwab quote fields are missing.")
            candidates.append(
                (
                    symbol,
                    quote.get("bidPrice"),
                    quote.get("askPrice"),
                    quote.get("lastPrice"),
                    epoch_time(quote.get("quoteTime"), milliseconds=True),
                    not item["realtime"] if isinstance(item.get("realtime"), bool) else None,
                )
            )
    else:
        root = raw.get("QuoteResponse") if isinstance(raw, dict) else None
        items = root.get("QuoteData", root.get("quoteData")) if isinstance(root, dict) else None
        if not isinstance(items, list):
            raise ValueError("E*TRADE quotes are missing.")
        candidates = []
        for item in items:
            product = item.get("Product", item.get("product")) if isinstance(item, dict) else None
            quote = item.get("All", item.get("all")) if isinstance(item, dict) else None
            if not isinstance(product, dict) or not isinstance(quote, dict):
                raise ValueError("E*TRADE underlying quote lacks product or ALL detail.")
            symbol = canonical_symbol(product.get("symbol"))
            status = item.get("quoteStatus")
            delayed = (
                True
                if status == "DELAYED"
                else False
                if status in ("REALTIME", "EH_REALTIME", "INDICATIVE_REALTIME")
                else None
            )
            candidates.append(
                (
                    symbol,
                    quote.get("bid"),
                    quote.get("ask"),
                    quote.get("lastTrade"),
                    epoch_time(item.get("dateTimeUTC")),
                    delayed,
                )
            )
    for symbol, bid, ask, last, clock, delayed in candidates:
        if symbol not in requested or symbol in seen:
            raise ValueError("Quote response has conflicting or duplicate symbol identity.")
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "bid_u": money_u(bid),
                "ask_u": money_u(ask),
                "last_u": money_u(last),
                "quote_at": clock,
                "is_delayed": delayed,
            }
        )
    return {
        "format": "broker-quotes-v1",
        **profile.identity(),
        "quotes": rows,
        "missing_symbols": sorted(requested - seen),
        "synthetic": profile.environment == "sandbox",
    }


def normalize_expiries(profile: BrokerProfile, raw: Any, symbol: str) -> dict[str, Any]:
    dates = set()
    symbol = canonical_symbol(symbol.upper())
    if profile.broker == "schwab":
        if not isinstance(raw, dict) or canonical_symbol(raw.get("symbol")) != symbol:
            raise ValueError("Schwab expiry response conflicts with symbol.")
        mapping = raw.get("callExpDateMap")
        if not isinstance(mapping, dict):
            raise ValueError("Schwab CALL expiry map is missing.")
        for key in mapping:
            dates.add(date.fromisoformat(key.split(":", 1)[0]).isoformat())
    else:
        root = raw.get("OptionExpireDateResponse") if isinstance(raw, dict) else None
        items = (
            root.get("ExpirationDate", root.get("expirationDates"))
            if isinstance(root, dict)
            else None
        )
        if not isinstance(items, list):
            raise ValueError("E*TRADE expiry list is missing.")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid E*TRADE expiry.")
            parts = [item.get(field) for field in ("year", "month", "day")]
            if any(isinstance(value, bool) or not isinstance(value, int) for value in parts):
                raise ValueError("E*TRADE expiry fields must be integer dates.")
            dates.add(date(*(cast(int, value) for value in parts)).isoformat())
    return {
        "format": "broker-expiries-v1",
        **profile.identity(),
        "symbol": symbol,
        "expiry_dates": sorted(dates),
        "full_strike_coverage_verified": False,
        "synthetic": profile.environment == "sandbox",
    }
