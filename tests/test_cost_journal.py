from __future__ import annotations

import json
from pathlib import Path

from longarc.cli import main
from longarc.storage import store


def test_cost_cli_audit_preserves_fee_assumptions_and_partial_net(tmp_path: Path, capsys) -> None:
    db = tmp_path / "db.sqlite3"
    store.initialize(db)
    request = {
        "idempotency_key": "cost-fixture", "as_of": "2026-09-21T19:45:00Z",
        "source": "synthetic_fixture", "mode": "shadow", "evidence_ids": [],
        "scenario": {"contracts": 1, "multiplier": 100, "opening_premium_u": 1000000,
                     "closing_bid_u": 480000, "closing_ask_u": 500000},
    }
    f = tmp_path / "request.json"
    f.write_text(json.dumps(request))
    args = ["options", "costs", "--db", str(db), "--file", str(f),
            "--fees", "config/options-costs.json"]
    assert main(args) == 0
    a = json.loads(capsys.readouterr().out)
    assert a["metrics"]["known_cost_net_option_pnl_u"] == "48700000"
    assert a["metrics"]["net_option_pnl_u"] is None
    saved = store.get_observation(db, a["record_id"])["payload"]
    assert saved["inputs"]["schedule"]["as_of"] == "2026-09-20"
    assert saved["inputs"]["scenario"] == request["scenario"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["write_status"] == "existing"


def test_invalid_cost_scenario_logged(tmp_path: Path, capsys) -> None:
    db = tmp_path / "db.sqlite3"
    store.initialize(db)
    f = tmp_path / "request.json"
    f.write_text(json.dumps({"idempotency_key": "bad", "as_of": "2026-09-21T19:45:00Z",
                            "source": "fixture", "mode": "shadow", "evidence_ids": [],
                            "scenario": {"contracts": -1}}))
    assert main(["options", "costs", "--db", str(db), "--file", str(f),
                 "--fees", "config/options-costs.json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert store.get_observation(db, result["record_id"])["kind"] == "error"


def test_iau_costs_stay_in_iau_scope(tmp_path: Path) -> None:
    from longarc.analytics.cost_journal import estimate_and_log

    db = tmp_path / 'iau.sqlite3'
    store.initialize(db)
    request = {'idempotency_key': 'iau-costs', 'as_of': '2026-09-21T19:45:00Z',
               'source': 'fixture', 'mode': 'shadow', 'symbol': 'IAU', 'evidence_ids': [],
               'scenario': {'contracts': 2, 'multiplier': 100, 'opening_premium_u': 250000,
                            'closing_bid_u': 100000, 'closing_ask_u': 150000}}
    schedule = json.loads(Path('config/options-costs.json').read_text())
    result = estimate_and_log(db, request, schedule)
    assert result['symbol'] == 'IAU'
    assert result['metrics']['gross_option_pnl_u'] == '20000000'
    assert store.get_observation(db, result['record_id'])['scope'] == 'options:IAU:costs'
    assert estimate_and_log(db, request, schedule)['write_status'] == 'existing'
