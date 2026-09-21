"""Normalize visible Schwab chain captures; append partial results without inventing data.

The browser bridge supplies rendered table cells, not cookies, network traffic or
account data. Capture time is the analysis clock. Source times remain independent.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from longarc.analytics.metrics import calculate
from longarc.core.symbols import canonical_symbol
from longarc.storage import holdings, store

FORMAT = "schwab-browser-chain-v1"
FIELDS = {
    "strike": "strike_u", "bid": "bid_u", "ask": "ask_u", "last": "last_u",
    "delta": "delta", "theta": "theta", "gamma": "gamma", "vega": "vega",
    "iv": "iv", "impvolatility": "iv", "impliedvolatility": "iv",
    "impliedvolitility": "iv",  # Spelling displayed by the Schwab table.
    "volume": "volume", "oi": "open_interest", "openinterest": "open_interest",
    "bidsize": "bid_size", "asksize": "ask_size",
    "probtouching": "probability_touch", "probotm": "probability_otm",
}
MONEY = {"strike_u", "bid_u", "ask_u", "last_u", "underlying_price_u"}


def _number(value: Any, field: str) -> int | float | None:
    if value is None or str(value).strip() in ("", "-", "--", "—", "N/A"):
        return None
    # Schwab accessibility annotations follow the first rendered numeric token.
    text = str(value).strip().split("\n")[0].replace(",", "").replace("$", "")
    match = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?", text)
    if not match:
        raise ValueError(f"invalid {field}")
    try:
        number = Decimal(text.rstrip("%"))
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field}") from exc
    if not number.is_finite():
        raise ValueError(f"invalid {field}")
    if field in MONEY:
        number *= 1_000_000
    if field.startswith("probability_") and not text.endswith("%"):
        raise ValueError("Probability unit must be explicit percent")
    if (field == "iv" or field.startswith("probability_")) and text.endswith("%"):
        number /= 100
    if field in MONEY or field in ("volume", "open_interest", "bid_size", "ask_size"):
        if number < 0 or number != number.to_integral_value() or number >= 2**63:
            raise ValueError(f"invalid {field}")
        return int(number)
    if field.startswith("probability_") and not 0 <= number <= 1:
        raise ValueError("Invalid probability")
    if field == "delta" and not 0 <= number <= 1:
        raise ValueError("invalid CALL delta")
    if field in ("iv", "gamma", "vega") and number < 0:
        raise ValueError(f"invalid {field}")
    return float(number)


def _header(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def normalize_capture(raw: dict[str, Any]) -> dict[str, Any]:
    """One bad row/field does not discard other expiries; bad identity is skipped.

    Only standard-looking, positive strikes are retained. A missing multiplier
    stays null. Completeness means requested watchlist coverage, never all listed
    contracts. Unknown/adjusted deliverables must not be used for sizing.
    """
    if raw.get("format") != FORMAT:
        raise ValueError("Expected schwab-browser-chain-v1 capture")
    symbol = canonical_symbol(raw.get("symbol"))
    if raw.get("option_type", "CALL") != "CALL":
        raise ValueError("Only CALL captures are supported")
    captured = holdings.timestamp(raw["captured_at"])
    requested = raw.get("requested", {})
    if not isinstance(requested, dict) or not isinstance(raw.get("slices"), list):
        raise ValueError("requested object and slices array required")
    for item in [*requested.get("contracts", []), *raw["slices"]]:
        if not isinstance(item, dict):
            continue
        if "symbol" in item and canonical_symbol(item["symbol"]) != symbol:
            raise ValueError("Capture symbol conflicts with requested contract or slice")
        if item.get("option_type", "CALL") != "CALL":
            raise ValueError("Only CALL captures are supported")
    warnings: list[str] = ["capture_time_analysis_clock", "full_listed_chain_not_verified"]
    if raw.get("selection") == "selected_rows_only":
        warnings.append("selected_rows_only")
    quotes: dict[tuple[str, int], dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for index, part in enumerate(raw["slices"]):
        try:
            expiry = date.fromisoformat(part["expiry_date"]).isoformat()
            stamp = holdings.timestamp(part.get("captured_at", captured))
            if stamp > captured:
                raise ValueError("slice captured after batch end")
            header = [_header(str(x)) for x in part["headers"]]
            rows = part["rows"]
            if not isinstance(rows, list) or not {"strike", "bid", "ask"} <= set(header):
                raise ValueError("unrecognized CALL table headers")
            if len(header) != len(set(header)):
                raise ValueError("ambiguous table columns")
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"slice": index, "reason": str(exc)})
            continue
        strikes: list[int] = []
        for row_index, original in enumerate(rows):
            row = list(original) if isinstance(original, list) else []
            # Observed Calls layout has a blank cell after strike and two trailing
            # cells for action controls. Never blindly zip a shifted row.
            if len(row) == len(header) + 2 and row[1] == "" and row[-1] == "":
                row = row[:1] + row[2:-1]
            if len(row) != len(header):
                rejected.append({"slice": index, "row": row_index, "reason": "column_mismatch"})
                continue
            values: dict[str, Any] = {}
            row_warnings: list[str] = []
            for name, cell in zip(header, row, strict=True):
                field = FIELDS.get(name)
                if field:
                    try:
                        values[field] = _number(cell, field)
                        if field == "iv":
                            values["iv_unit"] = (
                                "fraction" if "%" in str(cell) else "provider_display"
                            )
                    except ValueError:
                        values[field] = None
                        row_warnings.append(f"invalid_{field}")
            strike = values.pop("strike_u", None)
            if not strike or strike % 1_000_000:
                rejected.append({"slice": index, "row": row_index,
                                 "reason": "missing_or_nonstandard_strike"})
                continue
            # Zero placeholders in the UI are not proof of an existing contract.
            if (values.get("bid_u") == values.get("ask_u") == 0
                    and values.get("delta") is None):
                rejected.append({"slice": index, "row": row_index, "reason": "empty_placeholder"})
                continue
            snapshot: dict[str, Any] = {k: None for k in (
                "bid_u", "ask_u", "last_u", "delta", "theta", "gamma", "vega", "iv",
                "volume", "open_interest", "quote_at", "greeks_at", "underlying_at",
                "underlying_price_u", "bid_size", "ask_size", "iv_unit",
                "probability_touch", "probability_otm",
            )}
            snapshot.update(values, captured_at=stamp)
            for field in ("quote_at", "greeks_at", "underlying_at"):
                value = part.get(field)
                if value is not None:
                    try:
                        snapshot[field] = holdings.timestamp(value)
                    except (ValueError, TypeError):
                        row_warnings.append(f"invalid_{field}")
            try:
                snapshot["underlying_price_u"] = _number(part.get("underlying_price"),
                                                        "underlying_price_u")
            except ValueError:
                row_warnings.append("invalid_underlying_price")
            contract = {"symbol": symbol, "option_type": "CALL", "expiry_date": expiry,
                        "strike_u": strike, "multiplier": None}
            result = calculate(contract, snapshot, captured)
            row_warnings.extend(result["warnings"])
            if snapshot["iv_unit"] == "provider_display":
                row_warnings.append("iv_units_not_verified")
            row_warnings.extend(f"missing_{k}" for k in ("iv", "gamma", "vega", "open_interest")
                                if snapshot[k] is None)
            key = (expiry, strike)
            # Preserve every raw slice in the audit event; one most recent value
            # per contract in normalized batch avoids overlapping-window counts.
            previous = quotes.get(key)
            if previous is None or previous["snapshot"]["captured_at"] <= stamp:
                quotes[key] = {"contract": contract, "snapshot": snapshot,
                               "warnings": sorted(set(row_warnings)), "metrics": result["metrics"]}
            strikes.append(strike)
        coverage.append({"expiry_date": expiry, "rows": len(rows), "parsed_rows": len(strikes),
                         "min_strike_u": min(strikes, default=None),
                         "max_strike_u": max(strikes, default=None)})
    expected_expiries = requested.get("expiries", [])
    missing_expiries = sorted(set(expected_expiries) - {key[0] for key in quotes})
    missing_contracts = [c for c in requested.get("contracts", [])
                         if (c["expiry_date"], c["strike_u"]) not in quotes]
    errors = raw.get("errors", [])
    if missing_expiries:
        warnings.append("missing_requested_expiries")
    if missing_contracts:
        warnings.append("missing_requested_contracts")
    if errors or rejected:
        warnings.append("partial_collection")
    return {
        "status": "partial" if quotes else "error", "complete": False,
        "requested_coverage_complete": bool(expected_expiries or requested.get("contracts"))
        and not missing_expiries and not missing_contracts and not errors,
        "quotes": [quotes[k] for k in sorted(quotes)], "coverage": coverage,
        "missing_expiries": missing_expiries, "missing_contracts": missing_contracts,
        "rejected": rejected, "errors": errors, "warnings": warnings,
    }


def _without_collection_times(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_collection_times(v) for k, v in value.items()
                if k not in {"captured_at", "started_at"}}
    if isinstance(value, list):
        return [_without_collection_times(v) for v in value]
    return value


def _existing_capture(path: Path, key: str) -> dict[str, Any] | None:
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        row = db.execute("SELECT record_id FROM observations WHERE idempotency_key=?",
                         (key,)).fetchone()
    return store.get_observation(path, row["record_id"]) if row else None


def ingest_capture(path: Path, raw: dict[str, Any], *, mode: str = "observe",
                   closed_session: str | None = None) -> dict[str, Any]:
    if mode not in ("observe", "shadow"):
        raise ValueError("Capture mode must be observe or shadow")
    result = normalize_capture(raw)
    symbol = canonical_symbol(raw["symbol"])
    captured = holdings.timestamp(raw["captured_at"])
    key = "option-capture:" + store.digest({"mode": mode, "capture": raw})
    if closed_session is not None:
        session = date.fromisoformat(closed_session).isoformat()
        if session > date.fromisoformat(captured[:10]).isoformat():
            raise ValueError("Closed session cannot be in the future")
        # Explicit caller assertion: never infer a closed market from unchanged prices.
        key = "option-closed-v1:" + store.digest({
            "mode": mode, "session": session, "capture": _without_collection_times(raw),
        })
    event = {
        "idempotency_key": key,
        "scope": f"options:{symbol}", "mode": mode,
        "kind": "observation" if result["quotes"] else "error",
        "observed_at": captured, "source": "schwab_visible_browser",
        "quality": "synthetic" if mode == "shadow" else "unverified",
        "code_version": "capture-v1:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inputs": {"format": "option-chain-v1", "symbol": symbol,
                   "requested": raw.get("requested", {}), "raw_capture": raw},
        "results": result, "evidence_ids": raw.get("evidence_ids", []),
        "policy_hash": None,
    }
    if closed_session is not None:
        event["inputs"]["closed_session"] = closed_session
    saved = _existing_capture(path, key)
    if saved is None:
        try:
            receipt = store.save_observation(path, event)
        except ValueError:
            # Concurrent equivalent captures can carry different capture clocks.
            # The unique key selects the first writer without changing its evidence.
            saved = _existing_capture(path, key)
            if saved is None:
                raise
            receipt = {"status": "existing", "record_id": saved["record_id"]}
        else:
            saved = store.get_observation(path, receipt["record_id"])
            if saved["payload"]["results"] != result:
                raise ValueError("Capture readback mismatch")
    else:
        receipt = {"status": "existing", "record_id": saved["record_id"]}
    if saved["payload"]["inputs"]["raw_capture"] != raw:
        receipt = {"status": "existing", "record_id": saved["record_id"]}
    result = saved["payload"]["results"]
    return {"status": result["status"], "record_id": receipt["record_id"],
            "write_status": receipt["status"], "quote_count": len(result["quotes"]),
            "coverage": result["coverage"], "missing_expiries": result["missing_expiries"],
            "missing_contracts": result["missing_contracts"], "warnings": result["warnings"],
            "readback_verified": True, "stored_captured_at": saved["observed_at"]}
