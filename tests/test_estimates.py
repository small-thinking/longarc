import pytest

from longarc.analytics.estimates import summarize_replays


def episode(identity="a", pnl=100, day="21", **extra):
    return dict(episode_id=identity, policy_hash="a" * 64, assumptions_hash="b" * 64,
                status="closed", started_at=f"2026-09-{day}T18:00:00Z",
                ended_at="2026-10-20T18:00:00Z", net_option_pnl_u=pnl,
                evidence_ids=["source-a"], **extra)


def test_empty_and_single_win_do_not_claim_zero_risk():
    assert summarize_replays([])["groups"] == []
    group = summarize_replays([episode()])["groups"][0]
    assert group["closed_episode_mean_pnl_u"] == 100
    assert group["cohort_mean_standard_error_u"] is None
    assert group["cohort_loss_wilson_95"][1] > 0.7


def test_date_clusters_and_explicit_monthly_scenario():
    rows = [episode(), episode("b", 300), episode("c", -100, "22")]
    group = summarize_replays(rows, cycles_per_month=2)["groups"][0]
    assert group["entry_date_cohort_count"] == 2
    assert group["closed_episode_mean_pnl_u"] == 100
    assert group["equal_weight_cohort_mean_pnl_u"] == 50
    assert group["cohort_mean_standard_error_u"] == pytest.approx(150)
    assert group["monthly_scenario"]["mean_pnl_u"] == 100
    assert group["cohort_loss_fraction"] == 0.5


def test_incomplete_open_and_no_entry_not_zero_returns():
    rows = []
    for status in ("incomplete", "open", "no_entry"):
        row = episode(status)
        row.update(status=status, ended_at=None, net_option_pnl_u=None, observed_pnl_u=-1000)
        rows.append(row)
    group = summarize_replays(rows, cycles_per_month=2)["groups"][0]
    assert group["entry_date_cohort_count"] == 0
    assert group["closed_episode_mean_pnl_u"] is None
    assert group["cohort_loss_fraction"] is None
    assert group["cohort_loss_wilson_95"] == [0, 1]
    assert group["monthly_scenario"]["mean_pnl_u"] is None
    assert group["counts"] == dict(closed=0, open=1, incomplete=1, no_entry=1)


def test_idempotence_conflicts_and_policy_separation():
    row = episode()
    assert summarize_replays([row, row])["duplicates_removed"] == 1
    changed = dict(row, policy_hash="c" * 64)
    assert len(summarize_replays([row, changed])["groups"]) == 2
    changed = dict(row, assumptions_hash="c" * 64)
    assert len(summarize_replays([row, changed])["groups"]) == 2
    with pytest.raises(ValueError, match="conflicting"):
        summarize_replays([row, dict(row, net_option_pnl_u=12)])
    assert summarize_replays([changed, row]) == summarize_replays([row, changed])


def test_cohort_date_uses_new_york():
    a = episode()
    b = episode("b")
    a["started_at"] = "2026-09-22T01:00:00Z"
    assert summarize_replays([a, b])["groups"][0]["entry_date_cohort_count"] == 1


@pytest.mark.parametrize("key,value", [
    ("net_option_pnl_u", True), ("net_option_pnl_u", float("nan")),
    ("net_option_pnl_u", 1.2), ("net_option_pnl_u", 10**1000),
    ("started_at", "2026-09-21T00:00:00"), ("ended_at", "2020-01-01T00:00:00Z"),
    ("started_at", None), ("policy_hash", "none"), ("assumptions_hash", None),
    ("evidence_ids", "x"), ("status", "nonsense"), ("mode", "manual"),
    ("quality", "verified"),
])
def test_invalid_input_rejected(key, value):
    with pytest.raises(ValueError):
        summarize_replays([dict(episode(), **{key: value})])


@pytest.mark.parametrize("cycles", [0, -1, True, float("inf"), float("nan"), "2"])
def test_invalid_scenario(cycles):
    with pytest.raises(ValueError):
        summarize_replays([], cycles_per_month=cycles)


def test_compare_paired_only_and_date_clustering():
    from longarc.analytics.estimates import compare_replays

    rows = []
    for identity, day, difference in [("a", "21", 100), ("b", "21", 300), ("c", "22", -100)]:
        left = episode(identity, 200, day)
        rows.extend([left, dict(left, policy_hash="c" * 64, net_option_pnl_u=200 + difference)])
    rows.append(episode("unmatched", 999999))
    left = episode("open")
    rows.extend([left, dict(left, policy_hash="c" * 64, status="open", ended_at=None,
                           net_option_pnl_u=None)])
    result = compare_replays(rows, "a" * 64, "c" * 64)
    group = result["groups"][0]
    assert group["counts"] == dict(matched_closed=3, unmatched=1, incomplete=1,
                                    conflicting_entry_dates=0)
    assert group["paired_episode_mean_difference_u"] == 100
    assert group["equal_weight_cohort_mean_difference_u"] == 50
    assert group["entry_date_cohort_count"] == 2
    assert group["cohort_mean_difference_standard_error_u"] == pytest.approx(150)
    assert compare_replays(list(reversed(rows)), "a" * 64, "c" * 64) == result


def test_compare_different_assumptions_and_dates_excluded():
    from longarc.analytics.estimates import compare_replays

    left = episode()
    right = dict(left, policy_hash="c" * 64, assumptions_hash="d" * 64)
    groups = compare_replays([left, right], "a" * 64, "c" * 64)["groups"]
    assert len(groups) == 2
    assert all(g["counts"]["unmatched"] == 1 for g in groups)
    right.update(assumptions_hash=left["assumptions_hash"], started_at="2026-09-22T18:00:00Z")
    group = compare_replays([left, right], "a" * 64, "c" * 64)["groups"][0]
    assert group["counts"]["conflicting_entry_dates"] == 1
    assert group["paired_episode_mean_difference_u"] is None
    assert group["cohort_mean_difference_standard_error_u"] is None


def test_compare_single_pair_uncertainty_and_conflict_rejection():
    from longarc.analytics.estimates import compare_replays

    left = episode()
    right = dict(left, policy_hash="c" * 64, net_option_pnl_u=-200)
    group = compare_replays([left, right, right], "a" * 64, "c" * 64)["groups"][0]
    assert group["paired_episode_mean_difference_u"] == -300
    assert group["cohort_mean_difference_standard_error_u"] is None
    with pytest.raises(ValueError, match="conflicting"):
        compare_replays([left, dict(left, net_option_pnl_u=200)], "a" * 64, "c" * 64)
    with pytest.raises(ValueError, match="distinct"):
        compare_replays([left], "a" * 64, "a" * 64)


def test_symbols_are_not_pooled_or_paired_even_with_identical_policy_hashes():
    from longarc.analytics.estimates import compare_replays

    qqq = episode(pnl=100)
    iau = dict(qqq, symbol='IAU', net_option_pnl_u=-50)
    report = summarize_replays([qqq, iau])
    assert report['unique_episodes'] == 2
    assert {g['symbol']: g['closed_episode_mean_pnl_u'] for g in report['groups']} == {
        'QQQ': 100, 'IAU': -50}
    other_policy_iau = dict(iau, policy_hash='c' * 64)
    comparison = compare_replays([qqq, other_policy_iau], 'a' * 64, 'c' * 64)
    assert all(g['counts']['matched_closed'] == 0 for g in comparison['groups'])
    assert sum(g['counts']['unmatched'] for g in comparison['groups']) == 2
