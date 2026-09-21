"""Ad-hoc reports select the newest revision of each shadow episode and policy."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from longarc.analytics.estimates import compare_replays, summarize_replays
from longarc.analytics.replay import FORMAT
from longarc.core.symbols import canonical_symbol
from longarc.storage import store


def estimate_history(path: Path, *, cycles_per_month: float | None = None,
                     left_policy: str | None = None, right_policy: str | None = None,
                     limit: int = 10000, symbol: str = "QQQ") -> dict[str, Any]:
    symbol = canonical_symbol(symbol)
    if type(limit) is not int or not 1 <= limit <= 100000:
        raise ValueError("limit must be 1..100000")
    if bool(left_policy) != bool(right_policy):
        raise ValueError("Both comparison policy hashes required")
    with store.connect(path, readonly=True) as db:
        store.check_schema(db)
        rows = db.execute(
            "SELECT record_id,payload_json FROM observations WHERE scope=? AND mode='shadow' "
            "ORDER BY observed_at,recorded_at,record_id LIMIT ?",
            (f"options:{symbol}:replays", limit + 1),
        ).fetchall()
    if len(rows) > limit:
        raise ValueError("Replay history exceeds limit; increase limit, never truncate estimates")
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    records: dict[tuple[str, str, str], str] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload["inputs"].get("format") != FORMAT:
            continue
        r = payload["results"]
        if canonical_symbol(r.get("symbol", "QQQ")) != symbol:
            raise ValueError("Replay result symbol conflicts with journal scope")
        key = (r["episode_id"], r["policy_hash"], r["assumptions_hash"])
        latest[key] = r
        records[key] = row["record_id"]
    results = list(latest.values())
    report = summarize_replays(results, cycles_per_month=cycles_per_month)
    report["symbol"] = symbol
    report["replay_record_ids"] = sorted(records.values())
    report["stored_revisions"] = len(rows)
    report["latest_revisions"] = len(results)
    report["watch_contracts"] = [dict(symbol=symbol, expiry_date=expiry, strike_u=strike)
                                  for expiry, strike in sorted({
                                      (c["expiry_date"], c["strike_u"])
                                      for r in results for c in r.get("watch_contracts", [])})]
    report["comparison"] = (compare_replays(results, left_policy, right_policy)
                            if left_policy and right_policy else None)
    report["status"] = "descriptive" if any(r["status"] == "closed" for r in results) \
        else "insufficient_completed_cycles"
    return report


def render_estimate(report: dict[str, Any]) -> str:
    lines = [f"# {report.get('symbol', 'QQQ')} observed-checkpoint replay estimates",
             "", f"Status: {report['status']}",
             "", "All monetary values below are dollars. Results are hypothetical option overlay "
             "P&L, not actual fills or a forecast of total portfolio returns.", ""]

    def money(value: Any) -> str:
        return "unknown" if value is None else f"${value / 1_000_000:,.2f}"

    for g in report["groups"]:
        lines.extend([f"## Policy {g['policy_hash'][:12]} / assumptions "
                      f"{g['assumptions_hash'][:12]}", "",
                      f"Episode counts: {g['counts']}",
                      f"Entry-date cohorts: {g['entry_date_cohort_count']}",
                      f"Closed episode mean: {money(g['closed_episode_mean_pnl_u'])}",
                      f"Equal-weight cohort mean: {money(g['equal_weight_cohort_mean_pnl_u'])}",
                      f"Descriptive standard error: {money(g['cohort_mean_standard_error_u'])}",
                      f"Observed episode loss fraction: {g['closed_episode_loss_fraction']}",
                      f"Cohort-loss Wilson interval: {g['cohort_loss_wilson_95']}"])
        if g["monthly_scenario"]:
            m = g["monthly_scenario"]
            lines.append(f"Monthly scenario ({m['cycles_per_month']} assumed cycles/month): "
                         f"{money(m['mean_pnl_u'])}; NOT empirical monthly income.")
        lines.append("")
    if report["comparison"]:
        lines.extend(["## Paired policy comparison", "",
                      "Right minus left; only matching closed episodes contribute.", "",
                      "```json", json.dumps(report["comparison"], indent=2), "```", ""])
    lines.extend(["## Limitations", "", *["- " + w for w in report["warnings"]], "",
                  "## Pending observation contracts", "", json.dumps(report["watch_contracts"]),
                  "", "Replay evidence: " + ", ".join(report["replay_record_ids"])])
    return "\n".join(lines)
