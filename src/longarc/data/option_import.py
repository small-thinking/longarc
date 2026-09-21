"""Explicit selected-row adapters with legacy QQQ identity preserved."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from longarc.core.symbols import canonical_symbol
from longarc.data.option_capture import FORMAT, ingest_capture
from longarc.storage import store

# The known manual-tenor-comparison-v1 collector's fixed visible column order.
HEADERS = ["Strike", "Bid", "Ask", "Last", "Change", "Prob.Touching", "Prob.OTM",
           "Delta", "Theta", "Volume", "Open Interest", "Implied Volitility",
           "Gamma", "Vega", "Bid Size", "Ask Size", "Menu"]
MONTHS = {name: i for i, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def selected_capture(raw: dict[str, Any], *, tenor: bool = False) -> dict[str, Any]:
    """Convert only recognized layouts; never infer missing quote/source times."""
    explicit_symbol = raw.get("format") == "manual-schwab-selected-rows-v2" and not tenor
    symbol = canonical_symbol(raw.get("symbol")) if explicit_symbol else "QQQ"
    if "symbol" in raw and canonical_symbol(raw["symbol"]) != symbol:
        raise ValueError("Legacy selected-row captures must retain QQQ symbol")
    if raw.get("option_type", "CALL") != "CALL":
        raise ValueError("Only CALL captures are supported")
    slices: list[dict[str, Any]] = []
    if tenor:
        for row in raw["rows"]:
            slices.append({"expiry_date": date.fromisoformat(row["expiry"]).isoformat(),
                           "headers": HEADERS, "rows": [row["raw"]]})
    elif raw.get("format") in ("manual-schwab-selected-rows-v1", "manual-schwab-selected-rows-v2"):
        for table in raw["tables"]:
            if "symbol" in table and canonical_symbol(table["symbol"]) != symbol:
                raise ValueError("Selected-row table symbol conflicts with capture symbol")
            if table.get("option_type", "CALL") != "CALL":
                raise ValueError("Only CALL captures are supported")
            match = re.match(r"^([A-Z][a-z]{2})\.?\s+(\d{1,2}),\s+(\d{4})", table["label"])
            if not match or match[1] not in MONTHS:
                raise ValueError("Unrecognized expiry label")
            expiry = date(int(match[3]), MONTHS[match[1]], int(match[2]))
            slices.append({"expiry_date": expiry.isoformat(), "headers": table["headers"],
                           "rows": table["rows"]})
    else:
        raise ValueError("Unsupported selected-row capture format")
    for part in slices:
        for field in ("quote_at", "greeks_at", "underlying_at", "underlying_price"):
            part[field] = raw.get(field)
    return {"format": FORMAT, "symbol": symbol, "captured_at": raw["captured_at"],
            "requested": {}, "slices": slices, "selection": "selected_rows_only",
            "original_capture": raw}


def ingest_observation(path: Path, record_id: str, *, mode: str | None = None,
                       closed_session: str | None = None) -> dict[str, Any]:
    original = store.get_observation(path, record_id)["payload"]
    source_mode = original["mode"]
    if source_mode not in ("observe", "shadow") or (mode and mode != source_mode):
        raise ValueError("Import must preserve observe/shadow mode")
    inputs = original["inputs"]
    version = original["code_version"]
    if version in ("manual-evidence-v1", "manual-evidence-v2"):
        raw = selected_capture(inputs["quotes"])
        if not original["scope"].endswith(":" + raw["symbol"]):
            raise ValueError("Selected-row symbol conflicts with observation scope")
    elif (version == "manual-tenor-comparison-v1"
          and original["scope"] == "research:QQQ:tenor14-vs28"):
        raw = selected_capture(inputs["raw"], tenor=True)
    else:
        raise ValueError("Unsupported observation collector version or scope")
    if "symbol" in inputs and canonical_symbol(inputs["symbol"]) != raw["symbol"]:
        raise ValueError("Selected-row symbol conflicts with declared input symbol")
    known_session = original["results"].get("closed_session")
    if known_session is not None:
        if closed_session is not None and closed_session != known_session:
            raise ValueError("Closed session conflicts with source evidence")
        closed_session = known_session
    raw["evidence_ids"] = [record_id]
    return ingest_capture(path, raw, mode=source_mode, closed_session=closed_session)
