from __future__ import annotations

import copy

import pytest

from longarc.analytics.decisions import decide_and_log, evaluate
from longarc.storage import store


@pytest.fixture
def policy():
    # Synthetic test configuration, not published personal policy.
    return {"policy_parameters": {
        "watch_delta": .22, "defend_delta": .32, "profit_capture_fraction": .6,
        "latest_exit_dte": 6, "entry_delta_range": [.04, .12], "entry_dte_range": [20, 40],
        "entry_max_spread_fraction_of_midpoint": .12, "entry_min_open_interest": 80,
    }, "roll": {"max_rolls_per_episode": 1, "max_extra_calendar_days": 21}}


@pytest.fixture
def scenario():
    return {"as_of": "2026-09-21T19:00:00Z", "mode": "shadow", "source": "fixture",
            "idempotency_key": "decision-1", "evidence_ids": ["synthetic-evidence"],
            "facts": {"symbol": "QQQ", "option_type": "CALL", "position_contracts": 1,
                      "position_verified": True, "coverage_verified": True, "orders_clear": True,
                      "quote_usable": True, "greeks_usable": True, "underlying_usable": True,
                      "dividend_window_clear": True, "market_open": True,
                      "expiry_date": "2026-10-16", "delta": .1, "strike_u": 110000000,
                      "underlying_price_u": 100000000, "bid_u": 750000, "ask_u": 800000,
                      "opening_premium_u": 1000000, "multiplier": 100,
                      "allocated_opening_fees_u": 650000,
                      "estimated_closing_total_fees_u": 650000, "episode_roll_count": 0}}


@pytest.mark.parametrize("delta,action", [(.1, "HOLD"), (.22, "WATCH"), (.32, "BTC_RISK")])
def test_delta_boundaries(scenario, policy, delta, action):
    scenario["facts"]["delta"] = delta
    assert evaluate(scenario, policy)["action"] == action


def test_missing_profit_evidence_is_not_hold_but_known_risk_wins(scenario, policy):
    scenario["facts"]["opening_premium_u"] = None
    assert evaluate(scenario, policy)["action"] == "INSUFFICIENT_DATA"
    scenario["facts"]["underlying_price_u"] = 110000000
    assert evaluate(scenario, policy)["action"] == "BTC_RISK"
    scenario["facts"]["underlying_usable"] = False
    assert evaluate(scenario, policy)["action"] == "INSUFFICIENT_DATA"


def test_profit_boundary_fees_and_risk_priority(scenario, policy):
    scenario["facts"].update(bid_u=380000, ask_u=400000)
    assert evaluate(scenario, policy)["action"] == "BTC_PROFIT"
    scenario["facts"]["estimated_closing_total_fees_u"] = None
    assert evaluate(scenario, policy)["action"] == "INSUFFICIENT_DATA"
    scenario["facts"]["delta"] = .32
    assert evaluate(scenario, policy)["action"] == "BTC_RISK"


@pytest.mark.parametrize("field,value", [("dividend_window_clear", None), ("orders_clear", False),
                                        ("coverage_verified", False), ("greeks_usable", False)])
def test_critical_unknowns_and_failed_checks_block_hold(scenario, policy, field, value):
    scenario["facts"][field] = value
    assert evaluate(scenario, policy)["action"] == "INSUFFICIENT_DATA"


def test_roll_calculates_credit_and_cannot_delay_exit(scenario, policy):
    scenario["facts"]["delta"] = .22
    scenario["replacement"] = {**copy.deepcopy(scenario["facts"]), "contracts": 1,
                              "strike_u": 115000000, "expiry_date": "2026-10-23",
                              "delta": .08, "bid_u": 1000000, "ask_u": 1050000,
                              "standard_contract": True, "sizing_approved": True,
                              "reentry_cooldown_clear": True,
                              "open_interest": 100, "opening_total_fees_u": 650000}
    assert evaluate(scenario, policy)["action"] == "ROLL_CANDIDATE"
    assert evaluate(scenario, policy)["metrics"]["net_roll_cashflow_u"] == 18700000
    scenario["facts"]["episode_roll_count"] = 1
    assert evaluate(scenario, policy)["action"] == "WATCH"
    scenario["facts"]["delta"] = .32
    assert evaluate(scenario, policy)["action"] == "BTC_RISK"


def test_expired_reconcile_closed_market_and_unrealized_loss(scenario, policy):
    scenario["facts"].update(ask_u=1200000, bid_u=1150000, market_open=False)
    result = evaluate(scenario, policy)
    assert result["action"] == "HOLD"  # No fixed premium-loss stop in this policy.
    assert result["execution"] == "recheck_before_execution"
    scenario["facts"]["expiry_date"] = "2026-09-20"
    assert evaluate(scenario, policy)["action"] == "RECONCILE"


def test_decision_journal_versions_and_idempotency(tmp_path, scenario, policy):
    db = tmp_path / "decisions.sqlite3"
    store.initialize(db)
    first = decide_and_log(db, scenario, policy)
    assert decide_and_log(db, scenario, policy)["record_id"] == first["record_id"]
    saved = store.get_observation(db, first["record_id"])
    assert saved["policy_hash"] == store.digest(policy)
    assert saved["kind"] == "no_action"
    policy["policy_parameters"]["watch_delta"] = .15
    with pytest.raises(ValueError, match="Idempotency"):
        decide_and_log(db, scenario, policy)


def test_missing_expiry_retains_known_risk(scenario, policy):
    scenario["facts"].pop("expiry_date")
    assert evaluate(scenario, policy)["action"] == "INSUFFICIENT_DATA"
    scenario["facts"]["delta"] = .32
    assert evaluate(scenario, policy)["action"] == "BTC_RISK"


def test_entry_cooldown_and_explicit_size(scenario, policy):
    scenario["facts"].update(position_contracts=0, contracts=1, standard_contract=True,
                             sizing_approved=True, open_interest=100)
    assert evaluate(scenario, policy)["action"] == "NO_ENTRY"
    scenario["facts"]["reentry_cooldown_clear"] = True
    assert evaluate(scenario, policy)["action"] == "STO_CANDIDATE"
    scenario["facts"].pop("contracts")
    assert evaluate(scenario, policy)["action"] == "NO_ENTRY"


def test_invalid_decision_logged(tmp_path, scenario, policy):
    db = tmp_path / "invalid.sqlite3"
    store.initialize(db)
    scenario["facts"]["delta"] = 2
    result = decide_and_log(db, scenario, policy)
    assert result["action"] == "ERROR"
    assert store.get_observation(db, result["record_id"])["kind"] == "error"


def test_decision_cli(tmp_path, scenario, policy, capsys):
    import json

    from longarc.cli import main

    db = tmp_path / "cli.sqlite3"
    store.initialize(db)
    inputs, config = tmp_path / "request.json", tmp_path / "policy.json"
    inputs.write_text(json.dumps(scenario))
    config.write_text(json.dumps(policy))
    assert main(["options", "decide", "--db", str(db), "--file", str(inputs),
                 "--policy", str(config)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "HOLD" and result["readback_verified"]


@pytest.mark.parametrize('symbol', ['QQQ', 'IAU', 'SPY'])
@pytest.mark.parametrize('delta,action', [(.1, 'HOLD'), (.22, 'WATCH'), (.32, 'BTC_RISK')])
def test_scoped_policy_uses_same_exit_engine(scenario, policy, symbol, delta, action):
    policy['scope'] = {'underlying': symbol}
    scenario['facts'].update(symbol=symbol, delta=delta)
    result = evaluate(scenario, policy)
    assert result['action'] == action
    assert result['symbol'] == symbol


def test_iau_requires_own_policy_and_error_is_logged_under_iau(tmp_path, scenario, policy):
    db = tmp_path / 'symbols.sqlite3'
    store.initialize(db)
    scenario['facts']['symbol'] = 'IAU'
    result = decide_and_log(db, scenario, policy)
    assert result['action'] == 'ERROR'
    assert 'Policy underlying' in result['error']
    assert store.get_observation(db, result['record_id'])['scope'] == 'options:IAU:decisions'
    policy['scope'] = {'underlying': 'QQQ'}
    with pytest.raises(ValueError, match='Policy underlying'):
        evaluate(scenario, policy)
    policy['scope']['underlying'] = 'IAU'
    scenario['idempotency_key'] = 'iau-correct-policy'
    result = decide_and_log(db, scenario, policy)
    assert result['action'] == 'HOLD'
    assert store.get_observation(db, result['record_id'])['policy_hash'] == store.digest(policy)


def test_iau_entry_and_roll_never_borrow_other_symbol(scenario, policy):
    policy['scope'] = {'underlying': 'IAU'}
    scenario['facts'].update(symbol='IAU', position_contracts=0, contracts=1,
                             standard_contract=True, sizing_approved=True, open_interest=100,
                             reentry_cooldown_clear=True)
    assert evaluate(scenario, policy)['action'] == 'STO_CANDIDATE'
    scenario['facts'].update(position_contracts=1, delta=.22)
    scenario['replacement'] = {**scenario['facts'], 'symbol': 'QQQ', 'delta': .08,
                              'strike_u': 115000000, 'expiry_date': '2026-10-23',
                              'bid_u': 1000000, 'ask_u': 1050000,
                              'opening_total_fees_u': 650000}
    result = evaluate(scenario, policy)
    assert result['action'] == 'WATCH'
    assert result['checks']['roll_same_underlying'] is False
    scenario['replacement']['symbol'] = 'IAU'
    assert evaluate(scenario, policy)['action'] == 'ROLL_CANDIDATE'


def test_iau_does_not_infer_no_dividend_without_evidence(scenario, policy):
    policy['scope'] = {'underlying': 'IAU'}
    scenario['facts'].update(symbol='IAU', dividend_window_clear=None)
    assert evaluate(scenario, policy)['action'] == 'INSUFFICIENT_DATA'
    scenario['facts']['delta'] = .32
    assert evaluate(scenario, policy)['action'] == 'BTC_RISK'
