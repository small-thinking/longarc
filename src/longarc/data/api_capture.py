"""Shared persistence of normalized broker chain evidence, without copying analytics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from longarc.storage import holdings, store


def ingest_api_chain(
    path: Path,
    chain_file: Path,
    quotes_file: Path,
    *,
    source: str,
    adapter_tag: str,
    adapter_file: Path,
    normalize: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    synthetic: bool = False,
) -> dict[str, Any]:
    if len({p.resolve() for p in (path, chain_file, quotes_file)}) != 3:
        raise ValueError("Database and API input paths must be distinct")
    chain_bytes, quote_bytes = chain_file.read_bytes(), quotes_file.read_bytes()
    chain, quotes = json.loads(chain_bytes), json.loads(quote_bytes)
    if not isinstance(chain, dict) or not isinstance(quotes, dict):
        raise ValueError("API input envelopes must be objects")
    if not isinstance(chain.get("request"), dict) or not isinstance(quotes.get("request"), dict):
        raise ValueError("API input envelopes require request objects")
    for field in (
        "profile",
        "broker",
        "login_alias",
        "environment",
        "account_key",
        "account_id",
        "expected_account_last4",
    ):
        left = chain.get("request", {}).get(field)
        right = quotes.get("request", {}).get(field)
        if left != right:
            raise ValueError("API chain and quotes belong to different broker profiles")
    provider = chain.get("provider")
    if provider != quotes.get("provider"):
        raise ValueError("API inputs belong to different providers")
    if provider == "schwab-api":
        expected_source, expected_synthetic = "schwab_api", False
    elif provider == "etrade-api":
        environment = chain["request"].get("environment")
        if environment not in ("production", "sandbox"):
            raise ValueError("E*TRADE environment is required for ingestion")
        expected_synthetic = environment == "sandbox"
        expected_source = "etrade_sandbox_synthetic" if expected_synthetic else "etrade_api"
    else:
        raise ValueError("Unsupported API evidence provider")
    if source != expected_source or synthetic != expected_synthetic:
        raise ValueError("API source and synthetic mode must match the evidence environment")
    evidence = [
        {
            "resource": "chain",
            "file": str(chain_file.resolve()),
            "sha256": hashlib.sha256(chain_bytes).hexdigest(),
            "captured_at": holdings.timestamp(chain["captured_at"]),
        },
        {
            "resource": "quotes",
            "file": str(quotes_file.resolve()),
            "sha256": hashlib.sha256(quote_bytes).hexdigest(),
            "captured_at": holdings.timestamp(quotes["captured_at"]),
        },
    ]
    key = adapter_tag + ":" + store.digest(evidence)
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        existing = db.execute(
            "SELECT record_id FROM observations WHERE idempotency_key=?", (key,)
        ).fetchone()
    if existing is not None:
        saved = store.get_observation(path, existing["record_id"])["payload"]
        if (
            saved["source"] != source
            or saved["inputs"].get("format") != "option-chain-v1"
            or saved["inputs"].get("api_evidence") != evidence
        ):
            raise ValueError("Existing API observation conflicts with saved evidence")
        return {
            "record_id": existing["record_id"],
            "write_status": "existing",
            "status": saved["results"]["status"],
            "quote_count": len(saved["results"]["quotes"]),
        }
    normalized = normalize(chain, quotes)
    event = {
        "idempotency_key": key,
        "scope": f"options:{normalized['symbol']}",
        "mode": "shadow" if synthetic else "observe",
        "kind": "observation" if normalized["results"]["quotes"] else "error",
        "observed_at": normalized["observed_at"],
        "source": source,
        "quality": "synthetic" if synthetic else "unverified",
        "code_version": adapter_tag + ":" + hashlib.sha256(adapter_file.read_bytes()).hexdigest(),
        "inputs": {
            "format": "option-chain-v1",
            "symbol": normalized["symbol"],
            "requested": {
                "symbol": normalized["symbol"],
                "option_type": "CALL",
                "coverage": "all_returned_calls",
            },
            "api_requests": {"chain": chain["request"], "quotes": quotes["request"]},
            "api_evidence": evidence,
        },
        "results": normalized["results"],
        "evidence_ids": [item["file"] for item in evidence],
    }
    receipt = store.save_observation(path, event)
    saved = store.get_observation(path, receipt["record_id"])
    if saved["payload"] != store.validate(event):
        raise ValueError("API observation readback mismatch")
    return {
        "record_id": receipt["record_id"],
        "write_status": receipt["status"],
        "status": saved["payload"]["results"]["status"],
        "quote_count": len(saved["payload"]["results"]["quotes"]),
    }
