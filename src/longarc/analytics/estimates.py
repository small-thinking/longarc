"""Descriptive, provenance-separated estimates from explicitly synthetic replay episodes."""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from longarc.core.symbols import canonical_symbol

_HASH = re.compile(r"[0-9a-f]{64}")
_STATUSES = ("closed", "open", "no_entry", "incomplete")


def _timestamp(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include timezone")
    return parsed


def _wilson(losses: int, count: int) -> list[float]:
    if not count:
        return [0.0, 1.0]
    z = 1.959963984540054
    p = losses / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    width = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count**2)) / denominator
    return [max(0.0, center - width), min(1.0, center + width)]


def summarize_replays(
    results: list[dict[str, Any]], *, cycles_per_month: float | None = None,
) -> dict[str, Any]:
    """Never treat unclosed episodes as zero P&L or independent same-day bets."""
    if not isinstance(results, list):
        raise ValueError("results must be a list")
    if cycles_per_month is not None and (
        isinstance(cycles_per_month, bool)
        or not isinstance(cycles_per_month, (int, float))
        or not math.isfinite(cycles_per_month)
        or cycles_per_month <= 0
    ):
        raise ValueError("cycles_per_month must be finite and positive")
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("each replay must be an object")
        episode = item.get("episode_id")
        if not isinstance(episode, str) or not episode.strip():
            raise ValueError("episode_id is required")
        for key in ("policy_hash", "assumptions_hash"):
            if not isinstance(item.get(key), str) or not _HASH.fullmatch(item[key]):
                raise ValueError(f"{key} must be a lowercase SHA-256 digest")
        if item.get("status") not in _STATUSES:
            raise ValueError("invalid replay status")
        if item.get("mode", "shadow") != "shadow":
            raise ValueError("estimates accept shadow replay outputs only")
        if item.get("quality", "synthetic") != "synthetic":
            raise ValueError("estimates accept synthetic replay outputs only")
        evidence = item.get("evidence_ids")
        if not isinstance(evidence, list) or any(
            not isinstance(value, str) or not value.strip() for value in evidence
        ):
            raise ValueError("evidence_ids must be a list of nonempty strings")
        start = _timestamp(item.get("started_at"), "started_at")
        end = _timestamp(item.get("ended_at"), "ended_at")
        if start and end and end < start:
            raise ValueError("ended_at precedes started_at")
        pnl = item.get("net_option_pnl_u")
        if pnl is not None and type(pnl) is not int:
            raise ValueError("net_option_pnl_u must be an exact integer or null")
        if pnl is not None:
            try:
                finite = math.isfinite(pnl)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("net_option_pnl_u exceeds finite numeric range")
        if item["status"] == "closed" and (start is None or end is None or pnl is None):
            raise ValueError("closed episodes require timestamps and net P&L")
        if item["status"] != "closed" and pnl is not None:
            raise ValueError("unclosed episodes cannot have closed net P&L")
        symbol = canonical_symbol(item.get("symbol", "QQQ"))
        identity = (symbol, episode, item["policy_hash"], item["assumptions_hash"])
        if identity in unique:
            if unique[identity] != item:
                raise ValueError(f"conflicting duplicate episode: {episode}")
            continue
        unique[identity] = item
        groups[(symbol, item["policy_hash"], item["assumptions_hash"])].append(item)
    summaries = []
    for (symbol, policy, assumptions), items in sorted(groups.items()):
        counts = {status: sum(i["status"] == status for i in items) for status in _STATUSES}
        closed = [i for i in items if i["status"] == "closed"]
        cohorts: dict[str, list[int]] = defaultdict(list)
        for item in closed:
            start = _timestamp(item["started_at"], "started_at")
            assert start is not None
            cohorts[start.astimezone(ZoneInfo("America/New_York")).date().isoformat()].append(
                item["net_option_pnl_u"]
            )
        means = [statistics.mean(cohorts[day]) for day in sorted(cohorts)]
        cohort_mean = statistics.mean(means) if means else None
        standard_deviation = statistics.stdev(means) if len(means) >= 2 else None
        summaries.append({
            "symbol": symbol, "policy_hash": policy, "assumptions_hash": assumptions,
            "episode_ids": sorted(i["episode_id"] for i in items), "counts": counts,
            "evidence_ids": sorted({e for i in items for e in i["evidence_ids"]}),
            "closed_episode_mean_pnl_u": statistics.mean(
                i["net_option_pnl_u"] for i in closed
            ) if closed else None,
            "closed_episode_loss_fraction": sum(
                i["net_option_pnl_u"] < 0 for i in closed
            ) / len(closed) if closed else None,
            "entry_date_cohort_count": len(means),
            "cohorts": [{"entry_date_ny": day, "closed_episodes": len(cohorts[day]),
                         "mean_pnl_u": statistics.mean(cohorts[day])} for day in sorted(cohorts)],
            "equal_weight_cohort_mean_pnl_u": cohort_mean,
            "cohort_mean_sample_stddev_u": standard_deviation,
            "cohort_mean_standard_error_u": standard_deviation / math.sqrt(len(means))
            if standard_deviation is not None else None,
            "cohort_loss_fraction": sum(m < 0 for m in means) / len(means) if means else None,
            "cohort_loss_wilson_95": _wilson(sum(m < 0 for m in means), len(means)),
            "monthly_scenario": None if cycles_per_month is None else {
                "cycles_per_month": cycles_per_month,
                "mean_pnl_u": cohort_mean * cycles_per_month if cohort_mean is not None else None,
                "interpretation": "scenario_only_not_empirical_monthly_income",
                "assumptions": ["sequential_comparable_cycles", "constant_position_sizing"],
            },
        })
    return {
        "format": "replay-estimates-v1", "mode": "shadow", "quality": "synthetic",
        "unique_episodes": len(unique), "duplicates_removed": len(results) - len(unique),
        "groups": summaries,
        "warnings": [
            "Only closed episodes enter P&L estimates; partial eligibility may bias results.",
            "Same New York entry date is one cohort; nearby or overlapping cohorts may correlate.",
            "Wilson intervals describe negative cohort means, not individual-trade loss risk.",
            "Intervals assume independent comparable cohorts; they are not calibrated guarantees.",
            "Descriptive standard error; tiny samples and heavy tails prevent reliable normal CI.",
            "More observations do not guarantee lower strategy variance or estimation error.",
        ],
    }


def compare_replays(
    results: list[dict[str, Any]], left_policy_hash: str, right_policy_hash: str,
) -> dict[str, Any]:
    """Compare only matched closed episodes under identical execution assumptions."""
    for value in (left_policy_hash, right_policy_hash):
        if not isinstance(value, str) or not _HASH.fullmatch(value):
            raise ValueError("comparison policies must be lowercase SHA-256 digests")
    if left_policy_hash == right_policy_hash:
        raise ValueError("comparison requires distinct policies")
    # Validate all episodes using the same contract and duplicate-conflict rules.
    summarize_replays(results)
    unique = {
        (canonical_symbol(r.get("symbol", "QQQ")), r["episode_id"],
         r["policy_hash"], r["assumptions_hash"]): r for r in results
        if r["policy_hash"] in (left_policy_hash, right_policy_hash)
    }
    assumptions = sorted({(key[0], key[3]) for key in unique})
    groups = []
    for symbol, assumption in assumptions:
        episodes = sorted({key[1] for key in unique
                           if key[0] == symbol and key[3] == assumption})
        counts = {"matched_closed": 0, "unmatched": 0, "incomplete": 0,
                  "conflicting_entry_dates": 0}
        pairs = []
        cohorts: dict[str, list[int]] = defaultdict(list)
        for episode_id in episodes:
            left = unique.get((symbol, episode_id, left_policy_hash, assumption))
            right = unique.get((symbol, episode_id, right_policy_hash, assumption))
            if left is None or right is None:
                counts["unmatched"] += 1
                continue
            if left["status"] != "closed" or right["status"] != "closed":
                counts["incomplete"] += 1
                continue
            start_left = _timestamp(left["started_at"], "started_at")
            start_right = _timestamp(right["started_at"], "started_at")
            assert start_left is not None and start_right is not None
            date_left = start_left.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            date_right = start_right.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            if date_left != date_right:
                counts["conflicting_entry_dates"] += 1
                continue
            difference = right["net_option_pnl_u"] - left["net_option_pnl_u"]
            counts["matched_closed"] += 1
            cohorts[date_left].append(difference)
            pairs.append({
                "episode_id": episode_id, "entry_date_ny": date_left,
                "right_minus_left_pnl_u": difference,
                "evidence_ids": sorted(set(left["evidence_ids"] + right["evidence_ids"])),
            })
        means = [statistics.mean(cohorts[day]) for day in sorted(cohorts)]
        stdev = statistics.stdev(means) if len(means) >= 2 else None
        groups.append({
            "symbol": symbol, "assumptions_hash": assumption, "counts": counts, "pairs": pairs,
            "paired_episode_mean_difference_u": statistics.mean(
                p["right_minus_left_pnl_u"] for p in pairs
            ) if pairs else None,
            "entry_date_cohort_count": len(means),
            "equal_weight_cohort_mean_difference_u": statistics.mean(means) if means else None,
            "cohort_mean_difference_standard_error_u": stdev / math.sqrt(len(means))
            if stdev is not None else None,
            "cohorts": [{"entry_date_ny": day, "matched_pairs": len(cohorts[day]),
                         "mean_difference_u": statistics.mean(cohorts[day])}
                        for day in sorted(cohorts)],
        })
    return {
        "format": "paired-replay-comparison-v1", "mode": "shadow", "quality": "synthetic",
        "left_policy_hash": left_policy_hash, "right_policy_hash": right_policy_hash,
        "difference_direction": "right_minus_left", "groups": groups,
        "warnings": [
            "Only both-closed same-episode same-assumptions same-entry-date pairs are included.",
            "Unmatched and incomplete episodes are excluded, which may bias comparisons.",
            "Entry-date cohorts may correlate; cohort count is not effective sample size.",
            "Mean differences and standard errors are descriptive, not significance claims.",
        ],
    }
