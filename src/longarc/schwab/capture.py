"""Offline adapter from saved Schwab API envelopes to canonical option observations."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from longarc.analytics.metrics import calculate
from longarc.core.symbols import canonical_symbol
from longarc.storage import holdings, store

_OPTION = re.compile(r"([A-Z][A-Z0-9.]*)\s+(\d{2})(\d{2})(\d{2})C(\d{8})\Z")


def _money(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value)) * 1_000_000
    except InvalidOperation:
        return None
    if amount.is_finite() and 0 <= amount < 2**63 and amount == amount.to_integral_value():
        return int(amount)
    return None


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        count = Decimal(str(value))
    except InvalidOperation:
        return None
    if count.is_finite() and 0 <= count < 2**63 and count == count.to_integral_value():
        return int(count)
    return None


def _decimal(value: Any, *, nonnegative: bool = False, upper: int | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if (not number.is_finite() or (nonnegative and number < 0)
            or (upper is not None and not 0 <= number <= upper)):
        return None
    converted = float(number)
    return converted if math.isfinite(converted) else None


def _millis(value: Any) -> str | None:
    count = _count(value)
    if count is None or count == 0:
        return None
    try:
        return (datetime.fromtimestamp(count / 1000, UTC)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    except (OverflowError, OSError, ValueError):
        return None


def _envelope(raw: Any, resource: str) -> tuple[dict[str, Any], str]:
    if (not isinstance(raw, dict) or raw.get("provider") != "schwab-api"
            or raw.get("resource") != resource):
        raise ValueError(f"Expected saved Schwab API {resource} envelope")
    if not isinstance(raw.get("data"), dict) or not isinstance(raw.get("request"), dict):
        raise ValueError("Schwab envelope lacks data or request object")
    return raw["data"], holdings.timestamp(raw["captured_at"])


def normalize_api_chain(chain: dict[str, Any], quotes: dict[str, Any]) -> dict[str, Any]:
    """Map all returned CALL rows; reject identity conflicts before any database write."""
    data, chain_at = _envelope(chain, "chain")
    quote_data, quotes_at = _envelope(quotes, "quotes")
    symbol = canonical_symbol(data.get("symbol"))
    if canonical_symbol(chain["request"].get("symbol")) != symbol:
        raise ValueError("Chain request and response symbols conflict")
    symbols = quotes["request"].get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("Quotes request does not cover chain symbol")
    requested_symbols = {canonical_symbol(s) for s in symbols}
    if symbol not in requested_symbols:
        raise ValueError("Quotes request does not cover chain symbol")
    if symbol not in quote_data or not set(quote_data) <= requested_symbols:
        raise ValueError("Quotes response symbols conflict with chain symbol")
    underlying = quote_data[symbol]
    if not isinstance(underlying, dict) or canonical_symbol(underlying.get("symbol")) != symbol:
        raise ValueError("Underlying quote symbol conflicts with chain symbol")
    underlying_quote = underlying.get("quote")
    if not isinstance(underlying_quote, dict):
        raise ValueError("Underlying quote object is missing")
    underlying_at = _millis(underlying_quote.get("quoteTime"))
    underlying_price = _money(underlying_quote.get("lastPrice"))
    map_ = data.get("callExpDateMap")
    if not isinstance(map_, dict):
        raise ValueError("Schwab CALL expiry map is missing")
    observed_at = max(chain_at, quotes_at)
    warnings = ["api_snapshot_not_trade_approval", "source_clocks_independent",
                "full_listed_chain_not_verified"]
    if data.get("isDelayed") is True:
        warnings.append("delayed_chain")
    if data.get("isChainTruncated") is True:
        warnings.append("truncated_chain")
    if chain_at != quotes_at:
        warnings.append("asynchronous_capture")
    if underlying_at is None:
        warnings.append("missing_underlying_at")
    if underlying_price is None:
        warnings.append("missing_underlying_price_u")
    rows: dict[tuple[str, int, int | None, str], dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for key, strikes in map_.items():
        try:
            expiry = date.fromisoformat(key.split(":", 1)[0]).isoformat()
        except (ValueError, AttributeError):
            rejected.append({"expiry_key": str(key), "reason": "invalid_expiry_key"})
            continue
        if not isinstance(strikes, dict):
            rejected.append({"expiry_key": key, "reason": "invalid_strike_map"})
            continue
        found = 0
        parsed = 0
        for strike_key, candidates in strikes.items():
            strike_u = _money(strike_key)
            if strike_u is None or strike_u == 0 or not isinstance(candidates, list):
                rejected.append({"expiry_key": key, "strike_key": str(strike_key),
                                 "reason": "invalid_strike_or_rows"})
                continue
            for index, row in enumerate(candidates):
                found += 1
                location = {"expiry_key": key, "strike_key": str(strike_key), "row": index}
                if not isinstance(row, dict):
                    rejected.append({**location, "reason": "invalid_row"})
                    continue
                raw_symbol = row.get("symbol")
                match = _OPTION.fullmatch(raw_symbol) if isinstance(raw_symbol, str) else None
                if match is None:
                    rejected.append({**location, "reason": "missing_or_invalid_option_symbol"})
                    continue
                option_symbol = match.string
                root, yy, mm, dd, encoded_strike = match.groups()
                try:
                    occ_expiry = date(2000 + int(yy), int(mm), int(dd)).isoformat()
                except ValueError:
                    rejected.append({**location, "reason": "invalid_option_symbol_expiry"})
                    continue
                row_strike = _money(row.get("strikePrice"))
                row_expiry = row.get("expirationDate")
                try:
                    stated_expiry = (datetime.fromisoformat(
                        row_expiry.replace("Z", "+00:00")).date().isoformat()
                        if isinstance(row_expiry, str) else None)
                except ValueError:
                    stated_expiry = None
                if (root != symbol or row.get("putCall") != "CALL" or occ_expiry != expiry
                    or stated_expiry != expiry or row_strike != strike_u
                    or int(encoded_strike) * 1000 != strike_u):
                    raise ValueError(
                        "Schwab CALL row identity conflicts with expiry, strike, or symbol")
                multiplier = _count(row.get("multiplier"))
                if multiplier == 0:
                    multiplier = None
                contract = {"symbol": symbol, "option_type": "CALL", "expiry_date": expiry,
                            "strike_u": strike_u, "multiplier": multiplier,
                            "option_symbol": option_symbol,
                            "non_standard": (row.get("nonStandard")
                                             if isinstance(row.get("nonStandard"), bool)
                                             else None),
                            "mini": row.get("mini") if isinstance(row.get("mini"), bool) else None,
                            "deliverables": row.get("optionDeliverablesList"),
                            "deliverable_note": row.get("deliverableNote")}
                snapshot: dict[str, Any] = {
                    "captured_at": observed_at, "quote_at": _millis(row.get("quoteTimeInLong")),
                    "greeks_at": None, "underlying_at": underlying_at,
                    "underlying_price_u": underlying_price,
                    "bid_u": _money(row.get("bid")), "ask_u": _money(row.get("ask")),
                    "last_u": _money(row.get("last")),
                    "bid_size": _count(row.get("bidSize")), "ask_size": _count(row.get("askSize")),
                    "volume": _count(row.get("totalVolume")),
                    "open_interest": _count(row.get("openInterest")),
                    "delta": _decimal(row.get("delta"), upper=1),
                    "theta": (None if row.get("theta") == -999
                              else _decimal(row.get("theta"))),
                    "gamma": _decimal(row.get("gamma"), nonnegative=True),
                    "vega": _decimal(row.get("vega"), nonnegative=True),
                    "iv": None, "iv_unit": "fraction",
                    "probability_touch": None, "probability_otm": None,
                }
                iv = _decimal(row.get("volatility"), nonnegative=True)
                snapshot["iv"] = None if iv is None else iv / 100
                row_warnings = ["missing_greeks_at", "missing_probability_touch",
                                "missing_probability_otm"]
                for provider, canonical in (("volatility", "iv"), ("delta", "delta"),
                                            ("theta", "theta"), ("gamma", "gamma"),
                                            ("vega", "vega"), ("bid", "bid_u"),
                                            ("ask", "ask_u"), ("quoteTimeInLong", "quote_at")):
                    if snapshot[canonical] is None:
                        row_warnings.append(f"missing_or_invalid_{provider}")
                for field in ("multiplier", "open_interest", "bid_size", "ask_size", "volume"):
                    if (contract if field == "multiplier" else snapshot)[field] is None:
                        row_warnings.append(f"missing_{field}")
                calculated = calculate(contract, snapshot, observed_at)
                row_warnings.extend(calculated["warnings"])
                identity = (expiry, strike_u, multiplier, option_symbol)
                item = {"contract": contract, "snapshot": snapshot,
                        "warnings": sorted(set(row_warnings)), "metrics": calculated["metrics"]}
                previous = rows.get(identity)
                if previous is not None and previous != item:
                    raise ValueError("Duplicate Schwab contract rows disagree")
                rows[identity] = item
                parsed += 1
        coverage.append({"expiry_date": expiry, "rows": found, "parsed_rows": parsed})
    if rejected:
        warnings.append("partial_collection")
    if not rows:
        warnings.append("no_usable_call_rows")
    result = {"status": "partial" if rows else "error", "complete": False,
              "requested_coverage_complete": False, "quotes": [rows[k] for k in sorted(rows)],
              "coverage": coverage, "missing_expiries": [], "missing_contracts": [],
              "rejected": rejected, "errors": [], "warnings": warnings,
              "is_delayed": data.get("isDelayed"),
              "is_chain_truncated": data.get("isChainTruncated"),
              "reported_contract_count": data.get("numberOfContracts")}
    return {"symbol": symbol, "observed_at": observed_at, "results": result}


def ingest_chain(path: Path, chain_file: Path, quotes_file: Path) -> dict[str, Any]:
    """Persist one offline API observation and verify readback; reruns are idempotent."""
    if len({path.resolve(), chain_file.resolve(), quotes_file.resolve()}) != 3:
        raise ValueError("Database, chain file and quotes file must be distinct")
    chain_bytes = chain_file.read_bytes()
    quote_bytes = quotes_file.read_bytes()
    chain = json.loads(chain_bytes)
    quotes = json.loads(quote_bytes)
    evidence = [{"resource": "chain", "file": str(chain_file.resolve()),
                 "sha256": hashlib.sha256(chain_bytes).hexdigest(),
                 "captured_at": holdings.timestamp(chain["captured_at"])},
                {"resource": "quotes", "file": str(quotes_file.resolve()),
                 "sha256": hashlib.sha256(quote_bytes).hexdigest(),
                 "captured_at": holdings.timestamp(quotes["captured_at"])}]
    key = "schwab-api-chain-v1:" + store.digest(evidence)
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        existing = db.execute(
            "SELECT record_id FROM observations WHERE idempotency_key=?", (key,)
        ).fetchone()
    if existing is not None:
        saved = store.get_observation(path, existing["record_id"])["payload"]
        if (saved["source"] != "schwab_api" or saved["inputs"].get("format") != "option-chain-v1"
                or saved["inputs"].get("api_evidence") != evidence):
            raise ValueError("Existing API observation conflicts with saved evidence")
        return {"record_id": existing["record_id"], "write_status": "existing",
                "status": saved["results"]["status"],
                "quote_count": len(saved["results"]["quotes"])}
    normalized = normalize_api_chain(chain, quotes)
    event = {
        "idempotency_key": key,
        "scope": f"options:{normalized['symbol']}", "mode": "observe",
        "kind": "observation" if normalized["results"]["quotes"] else "error",
        "observed_at": normalized["observed_at"], "source": "schwab_api",
        "quality": "unverified",
        "code_version": "schwab-api-chain-v1:" + hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest(),
        "inputs": {"format": "option-chain-v1", "symbol": normalized["symbol"],
                   "requested": {"symbol": normalized["symbol"], "option_type": "CALL",
                                 "coverage": "all_returned_calls"},
                   "api_requests": {"chain": chain["request"], "quotes": quotes["request"]},
                   "api_evidence": evidence},
        "results": normalized["results"], "evidence_ids": [item["file"] for item in evidence],
    }
    receipt = store.save_observation(path, event)
    saved = store.get_observation(path, receipt["record_id"])
    if saved["payload"] != store.validate(event):
        raise ValueError("Schwab API observation readback mismatch")
    return {"record_id": receipt["record_id"], "write_status": receipt["status"],
            "status": saved["payload"]["results"]["status"],
            "quote_count": len(saved["payload"]["results"]["quotes"])}
