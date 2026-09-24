from __future__ import annotations

import copy
import json

import pytest

from longarc.analytics import decisions
from longarc.analytics.replay import replay, replay_and_log
from longarc.cli import main
from longarc.data.option_capture import ingest_capture
from longarc.storage import store


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "replay.sqlite3"
    store.initialize(db)
    policy = {"policy_parameters": {
        "watch_delta": .2, "defend_delta": .3, "profit_capture_fraction": .5,
        "latest_exit_dte": 7, "entry_delta_range": [.05, .1], "entry_dte_range": [21, 35],
        "entry_max_spread_fraction_of_midpoint": .1, "entry_min_open_interest": 100,
    }, "roll": {"max_rolls_per_episode": 1, "max_extra_calendar_days": 21}}
    fees = {"as_of": "2026-09-20", "base_fee_u": 0, "per_contract_fee_u": 650000,
            "btc_waiver_threshold_u": 50000}
    request = {"episode_id": "synthetic-cycle", "as_of": "2026-10-01T20:00:00Z",
               "contract": {"expiry_date": "2026-10-16", "strike_u": 110000000},
               "assumptions": {"contracts": 1, "multiplier": 100, "covered_shares": 100,
                               "max_gap_hours": 72, "max_quote_age_seconds": 60,
                               "max_greeks_age_seconds": 60, "max_underlying_age_seconds": 60,
                               "opening_slippage_u": 0, "closing_slippage_u": 0,
                               "opening_extra_fees_u": 0, "closing_extra_fees_u": 0,
                               "allow_roll": True}, "observations": []}
    return db, request, policy, fees


def frame(db, day, *, bid="1", ask="1.05", delta=".08", replacement=False,
          closed=False, stamp=True, symbol="QQQ"):
    time = f"2026-09-{day:02}T19:00:00Z"
    headers = ["Strike", "Bid", "Ask", "Delta", "Open Interest"]
    slices = [{"expiry_date": "2026-10-16", "headers": headers,
               "rows": [["110", bid, ask, delta, "200"]]}]
    if replacement:
        slices.append({"expiry_date": "2026-10-23", "headers": headers,
                       "rows": [["115", "2.05", "2.1", ".08", "200"]]})
    for s in slices:
        s.update(underlying_price="100", underlying_at=time if stamp else None,
                 quote_at=time if stamp else None, greeks_at=time if stamp else None)
    raw = {"format": "schwab-browser-chain-v1", "symbol": symbol,
           "captured_at": time, "slices": slices}
    result = ingest_capture(db, raw, mode="shadow",
                            closed_session="2026-09-18" if closed else None)
    checks = dict.fromkeys(["market_open", "dividend_window_clear", "quote_usable",
                           "greeks_usable", "underlying_usable", "standard_contract",
                           "reentry_cooldown_clear"], True)
    return {"record_id": result["record_id"], "checks": checks}


def test_cli_replay_estimate_and_history_select_iau(setup, tmp_path, capsys):
    db, request, policy, fees = setup
    request["contract"]["symbol"] = "IAU"
    policy["scope"] = {"underlying": "IAU"}
    request["observations"] = [frame(db, 21, symbol="IAU"),
                               frame(db, 22, symbol="IAU", bid=".35", ask=".4")]
    frame(db, 21, symbol="QQQ")
    paths = {}
    for name, payload in (("request", request), ("policy", policy), ("fees", fees)):
        paths[name] = str(tmp_path / f"{name}.json")
        (tmp_path / f"{name}.json").write_text(json.dumps(payload))
    assert main(["options", "replay", "--db", str(db), "--file", paths["request"],
                 "--policy", paths["policy"], "--fees", paths["fees"]]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "closed"
    assert main(["options", "estimate", "--db", str(db), "--symbol", "IAU"]) == 0
    assert json.loads(capsys.readouterr().out)["symbol"] == "IAU"
    assert main(["options", "history", "--db", str(db), "--symbol", "IAU",
                 "--mode", "shadow", "--start", "2026-09-21", "--end", "2026-09-22"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["series"]) == 1
    assert len(report["series"][0]["points"]) == 2
    assert report["series"][0]["contract"]["symbol"] == "IAU"


def test_replay_calls_shared_policy_and_logs_idempotently(setup, monkeypatch):
    db, r, p, fees = setup
    r["observations"] = [frame(db, 21), frame(db, 22, bid=".35", ask=".4")]
    original = decisions.evaluate
    calls = []

    def spy(request, policy):
        calls.append(policy)
        return original(request, policy)

    monkeypatch.setattr(decisions, "evaluate", spy)
    result = replay_and_log(db, r, p, fees)
    assert result["status"] == "closed"
    assert result["net_option_pnl_u"] == 58700000
    assert [d["action"] for d in result["decisions"]] == ["STO_CANDIDATE", "BTC_PROFIT"]
    assert calls == [p, p]
    assert replay_and_log(db, r, p, fees)["write_status"] == "existing"
    saved = store.get_observation(db, result["record_id"])
    assert saved["mode"] == "shadow" and saved["quality"] == "synthetic"


def test_replay_passes_opening_time_to_shared_profit_pace_rule(setup):
    db, r, p, fees = setup
    p["policy_parameters"].update(profit_capture_fraction=.6,
                                   profit_pace_min_capture_fraction=.5)
    r["observations"] = [frame(db, 21), frame(db, 22, bid=".43", ask=".45")]
    result = replay(db, r, p, fees)
    assert [d["action"] for d in result["decisions"]] == ["STO_CANDIDATE", "BTC_PROFIT"]
    assert result["decisions"][1]["checks"]["profit_pace_reached"] is True


def test_risk_loss_is_included_and_not_converted_to_roll(setup):
    db, r, p, fees = setup
    entry = frame(db, 21)
    end = frame(db, 22, bid="1.95", ask="2", delta=".35", replacement=True)
    end.update(replacement={"expiry_date": "2026-10-23", "strike_u": 115000000},
               replacement_checks=end["checks"])
    r["observations"] = [entry, end]
    result = replay(db, r, p, fees)
    assert result["net_option_pnl_u"] == -101300000
    assert result["legs"][0]["action"] == "BTC_RISK"


def test_roll_keeps_new_obligation_open_and_charges_both_legs(setup):
    db, r, p, fees = setup
    roll_frame = frame(db, 22, bid="1.95", ask="2", delta=".22", replacement=True)
    roll_frame.update(replacement={"expiry_date": "2026-10-23", "strike_u": 115000000},
                      replacement_checks=roll_frame["checks"])
    r["observations"] = [frame(db, 21), roll_frame]
    result = replay(db, r, p, fees)
    assert result["status"] == "open" and result["net_option_pnl_u"] is None
    assert result["realized_closed_legs_pnl_u"] == -101300000
    assert result["watch_contracts"] == [{"symbol": "QQQ", **roll_frame["replacement"]}]
    # Replacement finishes on a later frame; all prices still come from canonical evidence.
    later = frame(db, 23, replacement=True)
    payload = store.get_observation(db, later["record_id"])["payload"]["inputs"]["raw_capture"]
    payload["slices"][1]["rows"][0][1:3] = [".75", ".8"]
    later["record_id"] = ingest_capture(db, payload, mode="shadow")["record_id"]
    r["observations"].append(later)
    result = replay(db, r, p, fees)
    assert result["status"] == "closed"
    assert result["net_option_pnl_u"] == 22400000
    assert len(result["legs"]) == 2


@pytest.mark.parametrize("mode", ["missing_times", "closed", "unknown_checks", "stale"])
def test_incomplete_entry_does_not_fabricate_a_trade(setup, mode):
    db, r, p, fees = setup
    f = frame(db, 21, stamp=mode != "missing_times", closed=mode == "closed")
    if mode == "unknown_checks":
        f["checks"] = {}
    if mode == "stale":
        old = store.get_observation(db, f["record_id"])["payload"]["inputs"]["raw_capture"]
        old["captured_at"] = "2026-09-21T19:10:00Z"
        f["record_id"] = ingest_capture(db, old, mode="shadow")["record_id"]
    r["observations"] = [f]
    result = replay(db, r, p, fees)
    assert result["status"] == "incomplete" and not result["legs"]
    assert result["net_option_pnl_u"] is None


def test_gap_excludes_apparent_profit_and_keeps_diagnostic(setup):
    db, r, p, fees = setup
    r["observations"] = [frame(db, 21), frame(db, 25, bid=".35", ask=".4")]
    result = replay(db, r, p, fees)
    assert result["status"] == "incomplete" and result["net_option_pnl_u"] is None
    assert result["observed_pnl_u"] == 58700000


def test_pending_exit_missing_prices_does_not_become_zero(setup):
    db, r, p, fees = setup
    r["observations"] = [frame(db, 21), frame(db, 22, bid="-", ask="-", delta=".35")]
    result = replay(db, r, p, fees)
    assert result["status"] == "incomplete"
    assert result["net_option_pnl_u"] is None
    assert result["decisions"][-1]["action"] == "BTC_RISK"
    assert result["gaps"][-1]["reason"] == "exit_not_executable"


def test_slippage_fees_and_negative_roll_credit(setup):
    db, r, p, fees = setup
    r["assumptions"]["opening_slippage_u"] = 10000
    r["assumptions"]["closing_slippage_u"] = 20000
    r["observations"] = [frame(db, 21), frame(db, 22, bid=".35", ask=".4")]
    assert replay(db, r, p, fees)["net_option_pnl_u"] == 55700000
    roll_frame = frame(db, 22, bid="1.95", ask="2", delta=".22", replacement=True)
    roll_frame.update(replacement={"expiry_date": "2026-10-23", "strike_u": 115000000},
                      replacement_checks=roll_frame["checks"])
    r["observations"][1] = roll_frame
    r["assumptions"]["closing_slippage_u"] = 100000
    result = replay(db, r, p, fees)
    assert result["status"] == "open" and not result["legs"]
    assert result["decisions"][-1]["fill_blocked"]


def test_revision_changes_policy_hash_and_does_not_rewrite_old_result(setup):
    db, r, p, fees = setup
    r["observations"] = [frame(db, 21), frame(db, 22, bid=".55", ask=".6")]
    first = replay_and_log(db, r, p, fees)
    p2 = copy.deepcopy(p)
    p2["policy_parameters"]["profit_capture_fraction"] = .3
    second = replay_and_log(db, r, p2, fees)
    assert first["policy_hash"] != second["policy_hash"]
    assert first["status"] == "open" and second["status"] == "closed"


def test_invalid_order_or_assumptions_fail(setup):
    db, r, p, fees = setup
    r["observations"] = [frame(db, 22), frame(db, 21)]
    with pytest.raises(ValueError, match="chronological"):
        replay(db, r, p, fees)
    r["observations"].reverse()
    r["assumptions"]["opening_extra_fees_u"] = None
    with pytest.raises(ValueError, match="integer"):
        replay(db, r, p, fees)


def test_latest_revision_report_cli_and_path_immutability(setup, tmp_path, capsys):
    import json

    from longarc.analytics.research import estimate_history
    from longarc.cli import main

    db, r, p, fees = setup
    assert estimate_history(db)["status"] == "insufficient_completed_cycles"
    r["observations"] = [frame(db, 21)]
    first = replay_and_log(db, r, p, fees)
    assert estimate_history(db)["groups"][0]["counts"]["open"] == 1
    r["observations"].append(frame(db, 22, bid=".35", ask=".4"))
    for name, value in (("request", r), ("policy", p), ("fees", fees)):
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    assert main(["options", "replay", "--db", str(db), "--file", str(tmp_path / "request.json"),
                 "--policy", str(tmp_path / "policy.json"),
                 "--fees", str(tmp_path / "fees.json")]) == 0
    assert main(["options", "estimate", "--db", str(db), "--cycles-per-month", "1",
                 "--markdown-output", str(tmp_path / "report.md")]) == 0
    report = estimate_history(db)
    assert report["stored_revisions"] == 2 and report["latest_revisions"] == 1
    assert report["groups"][0]["counts"]["closed"] == 1
    assert first["record_id"] not in report["replay_record_ids"]
    assert "hypothetical" in (tmp_path / "report.md").read_text()
    with pytest.raises(ValueError, match="limit"):
        estimate_history(db, limit=1)
    r["observations"][0]["checks"]["dividend_window_clear"] = False
    with pytest.raises(ValueError, match="extend"):
        replay_and_log(db, r, p, fees)


def test_tenor_policy_is_explicit_and_does_not_change_live_policy(setup):
    db, r, policy, fees = setup
    f = frame(db, 21)
    raw = store.get_observation(db, f["record_id"])["payload"]["inputs"]["raw_capture"]
    raw["slices"][0]["expiry_date"] = "2026-10-02"
    f["record_id"] = ingest_capture(db, raw, mode="shadow")["record_id"]
    r["contract"]["expiry_date"] = "2026-10-02"
    r["observations"] = [f]
    assert replay(db, r, policy, fees)["status"] == "no_entry"
    research_policy = copy.deepcopy(policy)
    research_policy["policy_parameters"]["entry_dte_range"] = [10, 18]
    assert replay(db, r, research_policy, fees)["status"] == "open"
    assert policy["policy_parameters"]["entry_dte_range"] == [21, 35]


def test_profit_fill_requires_positive_net_after_known_slippage(setup):
    db, r, p, fees = setup
    r["assumptions"]["closing_slippage_u"] = 700000
    r["observations"] = [frame(db, 21), frame(db, 22, bid=".35", ask=".4")]
    result = replay(db, r, p, fees)
    assert result["status"] == "open" and result["net_option_pnl_u"] is None
    assert result["decisions"][-1]["fill_blocked"] == "profit_after_slippage_nonpositive"
    # A risk exit cannot be delayed because modeled execution would lose money.
    r["observations"][1] = frame(db, 22, bid=".35", ask=".4", delta=".35")
    result = replay(db, r, p, fees)
    assert result["status"] == "closed" and result["net_option_pnl_u"] < 0


@pytest.mark.parametrize('symbol', ['IAU', 'SPY'])
def test_scoped_replay_and_history_are_isolated(setup, symbol):
    from longarc.analytics.research import estimate_history, render_estimate

    db, r, p, fees = setup
    r['contract']['symbol'] = symbol
    p['scope'] = {'underlying': symbol}
    r['observations'] = [frame(db, 21, symbol=symbol),
                         frame(db, 22, symbol=symbol, bid='.35', ask='.4')]
    result = replay_and_log(db, r, p, fees)
    assert result['status'] == 'closed'
    assert result['symbol'] == symbol
    assert result['net_option_pnl_u'] == 58700000
    assert store.get_observation(db, result['record_id'])['scope'] == f'options:{symbol}:replays'
    report = estimate_history(db, symbol=symbol)
    assert report['unique_episodes'] == 1
    assert report['groups'][0]['symbol'] == symbol
    assert render_estimate(report).startswith(f'# {symbol} ')
    assert estimate_history(db)['unique_episodes'] == 0
    assert replay_and_log(db, r, p, fees)['write_status'] == 'existing'


def test_iau_replay_rejects_other_asset_policy_evidence_and_roll(setup):
    db, r, p, fees = setup
    r['contract']['symbol'] = 'IAU'
    r['observations'] = [frame(db, 21, symbol='IAU')]
    with pytest.raises(ValueError, match='Policy underlying'):
        replay(db, r, p, fees)
    p['scope'] = {'underlying': 'IAU'}
    other = frame(db, 22, symbol='QQQ', bid='.35', ask='.4')
    r['observations'].append(other)
    with pytest.raises(ValueError, match='same symbol'):
        replay(db, r, p, fees)
    roll_frame = frame(db, 22, symbol='IAU', bid='1.95', ask='2', delta='.22', replacement=True)
    roll_frame.update(replacement={'symbol': 'QQQ', 'expiry_date': '2026-10-23',
                                  'strike_u': 115000000}, replacement_checks=roll_frame['checks'])
    r['observations'][-1] = roll_frame
    with pytest.raises(ValueError, match='same underlying'):
        replay(db, r, p, fees)
    roll_frame['replacement']['symbol'] = 'IAU'
    result = replay(db, r, p, fees)
    assert result['status'] == 'open'
    assert result['watch_contracts'][0]['symbol'] == 'IAU'
    assert result['legs'][0]['action'] == 'ROLL_CANDIDATE'
