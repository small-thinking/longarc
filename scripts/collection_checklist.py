"""Plan and audit collection attempts; never approve a trade or reject quote ingestion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED = (
    "session_calendar", "positions", "orders", "buyback_funds", "execution_reconciliation",
    "expiry_inventory", "held_and_watch_contracts", "four_week_candidates", "two_week_candidates",
    "underlying_quote", "quote_times", "greek_times", "delay_disclosure", "contract_terms",
    "dividend_events", "fees",
)
QUOTE_FIELDS = (
    "bid", "ask", "last", "delta", "theta", "gamma", "vega", "iv",
    "volume", "open_interest", "bid_size", "ask_size", "probability_touch", "probability_otm",
)
STATUSES = {"observed", "not_provided", "blocked", "not_applicable", "not_attempted"}


def template(symbol: str) -> dict:
    return {
        "format": "collection-checklist-v1", "symbol": symbol, "run_id": None,
        "started_at": None, "finished_at": None, "capture_record_ids": [],
        "items": {key: {"status": "not_attempted", "reason": None, "source_ref": None,
                        "checked_at": None, "evidence_ref": None}
                  for key in REQUIRED},
        "contracts": [],
    }


def audit(doc: dict) -> dict:
    errors, omissions, unavailable = [], [], []
    if doc.get("format") != "collection-checklist-v1":
        errors.append("unsupported format")
    for key in ("symbol", "run_id", "started_at", "finished_at"):
        if not doc.get(key):
            errors.append(f"missing {key}")

    def check(name: str, item: object) -> None:
        if not isinstance(item, dict) or item.get("status") not in STATUSES:
            errors.append(f"{name}: missing item or invalid status")
            omissions.append(name)
            return
        status = item["status"]
        if status == "not_attempted":
            omissions.append(name)
        elif status in {"not_provided", "blocked"}:
            unavailable.append(name)
        if status != "observed" and not item.get("reason"):
            errors.append(f"{name}: reason required")
        if status in {"observed", "not_provided", "blocked"}:
            for key in ("source_ref", "checked_at", "evidence_ref"):
                if not item.get(key):
                    errors.append(f"{name}: {key} required")

    items = doc.get("items", {})
    if not isinstance(items, dict):
        items = {}
        errors.append("items must be an object")
    for name in REQUIRED:
        check(name, items.get(name))
    contracts = doc.get("contracts", [])
    if not isinstance(contracts, list):
        errors.append("contracts must be an array")
        contracts = []
    if not contracts and any(isinstance(items.get(k), dict) and
                             items[k].get("status") == "observed" for k in
                             ("four_week_candidates", "two_week_candidates")):
        errors.append("observed candidates require contract field inventory")
    for index, contract in enumerate(contracts):
        if not isinstance(contract, dict):
            errors.append(f"contracts[{index}]: must be an object")
            continue
        name = contract.get("contract_id") or f"contracts[{index}]"
        if not contract.get("contract_id"):
            errors.append(f"{name}: contract_id required")
        fields = contract.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        for field in QUOTE_FIELDS:
            check(f"{name}.{field}", fields.get(field))
    return {
        "status": "needs_followup" if errors or omissions else
                  "documented_with_missing_data" if unavailable else "documented",
        "errors": errors, "not_attempted": omissions, "unavailable": unavailable,
        "analysis_allowed": True, "trade_approval": False,
        "limitations": ["Self-reported evidence references are not independently verified.",
                        "This audit does not establish freshness, coverage or policy eligibility."],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", metavar="SYMBOL")
    group.add_argument("--file", type=Path)
    args = parser.parse_args()
    result = template(args.init) if args.init else audit(json.loads(args.file.read_text()))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
