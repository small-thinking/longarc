"""Offline E*TRADE option-chain adapter for the shared quote journal."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from longarc.analytics.metrics import calculate
from longarc.core.market_fields import count as _count
from longarc.core.market_fields import epoch_time
from longarc.core.market_fields import money_u as _money
from longarc.core.market_fields import number as _number
from longarc.core.symbols import canonical_symbol
from longarc.storage import holdings

_OSI = re.compile(r"([A-Z0-9. -]{6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})\Z")


def _epoch_seconds(value: Any) -> str | None:
    return epoch_time(value)


def _envelope(raw: Any, resource: str) -> tuple[dict[str, Any], str]:
    if (not isinstance(raw, dict) or raw.get("provider") != "etrade-api"
            or raw.get("resource") != resource or not isinstance(raw.get("request"), dict)
            or not isinstance(raw.get("data"), dict)):
        raise ValueError(f"Expected saved E*TRADE API {resource} envelope")
    request = raw["request"]
    if (request.get("broker") != "etrade" or request.get("environment") not in
            ("sandbox", "production") or not isinstance(request.get("login_alias"), str)
            or not request["login_alias"]):
        raise ValueError("E*TRADE request lacks broker, environment, or login alias")
    root = "OptionChainResponse" if resource == "chain" else "QuoteResponse"
    data = raw["data"]
    if root in data:
        if len(data) != 1 or not isinstance(data[root], dict):
            raise ValueError(f"Invalid E*TRADE {root} wrapper")
        data = data[root]
    if root in data:
        raise ValueError(f"Nested E*TRADE {root} wrapper")
    return data, holdings.timestamp(raw["captured_at"])


def _expiry(parts: Any) -> str | None:
    if not isinstance(parts, dict):
        return None
    try:
        return date(int(parts["year"]), int(parts["month"]), int(parts["day"])).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _option_identity(row: dict[str, Any], symbol: str, expiry: str) -> tuple[str, int]:
    osi = row.get("osiKey")
    match = _OSI.fullmatch(osi) if isinstance(osi, str) else None
    if match is None:
        raise ValueError("E*TRADE CALL row lacks a valid OSI key")
    root, yy, mm, dd, side, strike_digits = match.groups()
    try:
        encoded_expiry = date(2000 + int(yy), int(mm), int(dd)).isoformat()
    except ValueError as exc:
        raise ValueError("E*TRADE CALL row has invalid OSI expiry") from exc
    strike_u = _money(row.get("strikePrice"))
    if (root.rstrip(" -") != symbol or row.get("optionRootSymbol") != symbol
            or side != "C" or row.get("optionType") != "CALL"
            or encoded_expiry != expiry or strike_u is None
            or strike_u != int(strike_digits) * 1000):
        raise ValueError("E*TRADE CALL row identity conflicts with request or selected expiry")
    assert isinstance(osi, str) and strike_u is not None
    return osi, strike_u


def normalize_api_chain(
    chain_envelope: dict[str, Any], quotes_envelope: dict[str, Any]
) -> dict[str, Any]:
    """Map returned E*TRADE calls; reject contradictory identity before persistence."""
    chain, chain_at = _envelope(chain_envelope, "chain")
    quotes, quotes_at = _envelope(quotes_envelope, "quotes")
    chain_request = chain_envelope["request"]
    quote_request = quotes_envelope["request"]
    if any(chain_request[k] != quote_request[k] for k in ("broker", "environment", "login_alias")):
        raise ValueError("E*TRADE chain and quotes belong to different profiles")
    symbol = canonical_symbol(chain_request.get("symbol"))
    expiry = chain_request.get("expiry")
    if not isinstance(expiry, str) or date.fromisoformat(expiry).isoformat() != expiry:
        raise ValueError("E*TRADE chain request requires YYYY-MM-DD expiry")
    if _expiry(chain.get("SelectedED", chain.get("selected"))) != expiry:
        raise ValueError("E*TRADE selected expiry conflicts with request")
    raw_requested = quote_request.get("symbols")
    if not isinstance(raw_requested, list):
        raise ValueError("E*TRADE quotes request does not cover chain symbol")
    requested = {canonical_symbol(s.upper() if isinstance(s, str) else s)
                 for s in raw_requested}
    if symbol not in requested:
        raise ValueError("E*TRADE quotes request does not cover chain symbol")
    quote_rows = quotes.get("QuoteData", quotes.get("quoteData"))
    if not isinstance(quote_rows, list):
        raise ValueError("E*TRADE QuoteResponse lacks QuoteData")
    matching = []
    for item in quote_rows:
        product = item.get("Product", item.get("product")) if isinstance(item, dict) else None
        if not isinstance(product, dict):
            raise ValueError("Invalid E*TRADE QuoteData row")
        quoted_symbol = canonical_symbol(product.get("symbol"))
        if quoted_symbol not in requested:
            raise ValueError("E*TRADE quote response contains an unrequested symbol")
        if quoted_symbol == symbol:
            matching.append(item)
    if len(matching) != 1:
        raise ValueError("E*TRADE quote response must contain exactly one requested underlying")
    underlying_row = matching[0]
    underlying_data = underlying_row.get("All", underlying_row.get("all"))
    if not isinstance(underlying_data, dict):
        raise ValueError("E*TRADE underlying quote requires ALL detail")
    underlying_price = _money(underlying_data.get("lastTrade"))
    underlying_at = _epoch_seconds(underlying_row.get("dateTimeUTC"))
    pairs = chain.get("OptionPair", chain.get("optionPairs"))
    if not isinstance(pairs, list):
        raise ValueError("E*TRADE OptionChainResponse lacks OptionPair")
    observed_at = max(chain_at, quotes_at)
    quote_type = chain.get("quoteType")
    quote_status = underlying_row.get("quoteStatus")
    real_time_quote_statuses = {"REALTIME", "EH_REALTIME", "INDICATIVE_REALTIME"}
    delayed = (True if "DELAYED" in (quote_type, quote_status) else
               False if quote_type == "REALTIME" and
               quote_status in real_time_quote_statuses else None)
    warnings = ["api_snapshot_not_trade_approval", "source_clocks_independent",
                "full_listed_chain_not_verified", "unknown_iv_unit"]
    if delayed:
        warnings.append("delayed_chain_or_underlying")
    if delayed is None:
        warnings.append("unknown_delay_status")
    if chain_request["environment"] == "sandbox":
        warnings.append("sandbox_stored_data_not_current_market")
    if chain_at != quotes_at:
        warnings.append("asynchronous_capture")
    if underlying_at is None:
        warnings.append("missing_underlying_at")
    if underlying_price is None:
        warnings.append("missing_underlying_price_u")
    rows: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            rejected.append({"pair": index, "reason": "invalid_pair"})
            continue
        call = pair.get("Call", pair.get("optioncall"))
        if call is None:
            continue
        if not isinstance(call, dict):
            rejected.append({"pair": index, "reason": "invalid_call"})
            continue
        osi, strike_u = _option_identity(call, symbol, expiry)
        greek = call.get("OptionGreeks", call.get("optionGreek"))
        if greek is not None and not isinstance(greek, dict):
            greek = None
        adjusted = call.get("adjustedFlag")
        category = call.get("optionCategory")
        contract = {"symbol": symbol, "option_type": "CALL", "expiry_date": expiry,
                    "strike_u": strike_u, "multiplier": None, "option_symbol": osi,
                    "non_standard": adjusted if isinstance(adjusted, bool) else None,
                    "mini": category == "MINI" if category in ("MINI", "STANDARD") else None,
                    "deliverables": None, "deliverable_note": None}
        snapshot = {"captured_at": observed_at, "quote_at": _epoch_seconds(call.get("timeStamp")),
                    "greeks_at": None, "underlying_at": underlying_at,
                    "underlying_price_u": underlying_price, "bid_u": _money(call.get("bid")),
                    "ask_u": _money(call.get("ask")), "last_u": _money(call.get("lastPrice")),
                    "bid_size": _count(call.get("bidSize")),
                    "ask_size": _count(call.get("askSize")),
                    "volume": _count(call.get("volume")),
                    "open_interest": _count(call.get("openInterest")),
                    "delta": _number(greek.get("delta"), lower=-1, upper=1) if greek else None,
                    "theta": _number(greek.get("theta")) if greek else None,
                    "gamma": _number(greek.get("gamma"), nonnegative=True) if greek else None,
                    "vega": _number(greek.get("vega"), nonnegative=True) if greek else None,
                    "iv": _number(greek.get("iv"), nonnegative=True) if greek else None,
                    "iv_unit": "provider_raw", "probability_touch": None,
                    "probability_otm": None}
        row_warnings = ["missing_greeks_at", "missing_probability_touch",
                        "missing_probability_otm", "unknown_multiplier", "unknown_deliverables",
                        "unknown_iv_unit"]
        current_value = greek.get("currentValue") if greek else None
        if current_value is False:
            row_warnings.append("greeks_not_current")
        elif current_value is not True:
            row_warnings.append("unknown_greeks_current_value")
        for field in ("quote_at", "bid_u", "ask_u", "delta", "theta", "gamma", "vega", "iv"):
            if snapshot[field] is None:
                row_warnings.append(f"missing_{field}")
        calculated = calculate(contract, snapshot, observed_at)
        row_warnings.extend(calculated["warnings"])
        item = {"contract": contract, "snapshot": snapshot,
                "warnings": sorted(set(row_warnings)), "metrics": calculated["metrics"]}
        if osi in rows and rows[osi] != item:
            raise ValueError("Duplicate E*TRADE contract rows disagree")
        rows[osi] = item
    if rejected:
        warnings.append("partial_collection")
    if not rows:
        warnings.append("no_usable_call_rows")
    result = {"status": "partial" if rows else "error", "complete": False,
              "requested_coverage_complete": False, "quotes": [rows[k] for k in sorted(rows)],
              "coverage": [{"expiry_date": expiry, "rows": len(pairs), "parsed_rows": len(rows)}],
              "missing_expiries": [], "missing_contracts": [], "rejected": rejected,
              "errors": [], "warnings": warnings, "is_delayed": delayed,
              "chain_quote_type": quote_type, "underlying_quote_status": quote_status,
              "chain_source_at": _epoch_seconds(chain.get("timeStamp")),
              "is_chain_truncated": None, "reported_contract_count": None}
    return {"symbol": symbol, "observed_at": observed_at, "results": result}
