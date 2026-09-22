import json

import pytest

from longarc.cli import main
from longarc.storage import store


@pytest.fixture
def paths(tmp_path):
    db = tmp_path / "reports.sqlite3"
    store.initialize(db)
    fees = tmp_path / "fees.json"
    fees.write_text(json.dumps(dict(as_of="2026-09-01", base_fee_u=0,
                                   per_contract_fee_u=650000, btc_waiver_threshold_u=50000)))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"policy_parameters": {
        "entry_delta_range": [.05, .1], "entry_dte_range": [21, 35],
        "entry_max_spread_fraction_of_midpoint": .2, "entry_min_open_interest": 10}}))
    return db, fees, policy


def test_audit_cli_exports_and_does_not_mutate(paths, tmp_path, capsys):
    db, _, _ = paths
    before = db.read_bytes()
    output = tmp_path / "audit.json"
    markdown = tmp_path / "audit.md"
    assert main(["options", "data-audit", "--db", str(db), "--symbol", "QQQ",
                 "--json-output", str(output), "--markdown-output", str(markdown)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert json.loads(output.read_text())["inventory"]["table_rows"]["observations"] == 0
    assert "No matching canonical quote batches" in markdown.read_text()
    assert db.read_bytes() == before


def test_candidate_cli_uses_only_named_batch(paths, tmp_path, capsys):
    db, fees, policy = paths
    event = dict(idempotency_key="chain", scope="options:QQQ", mode="observe",
                 kind="observation", observed_at="2026-09-21T18:00:00Z", source="fixture",
                 quality="unverified", code_version="test", evidence_ids=[],
                 inputs={"format": "option-chain-v1"}, results={"complete": False,
                 "quotes": [{"contract": dict(symbol="QQQ", option_type="CALL",
                              expiry_date="2026-10-16", strike_u=790000000, multiplier=None),
                             "snapshot": dict(captured_at="2026-09-21T18:00:00Z",
                                              bid_u=1000000, ask_u=1100000, delta=.08,
                                              underlying_price_u=740000000, open_interest=500)}]})
    record = store.save_observation(db, event)["record_id"]
    markdown = tmp_path / "candidates.md"
    assert main(["options", "compare-candidates", "--db", str(db),
                 "--observation-id", record, "--as-of", "2026-09-21T18:01:00Z",
                 "--fees", str(fees), "--policy", str(policy),
                 "--markdown-output", str(markdown)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    content = markdown.read_text()
    assert record in content and "unknown_closing_extra_fees" in content
    assert "Every entry eligibility remains unknown" in content


def test_position_cli_and_input_protection(paths, tmp_path, capsys):
    db, fees, _ = paths
    args = ["options", "position-report", "--db", str(db), "--account", "example",
            "--fees", str(fees), "--as-of", "2026-09-21T20:00:00Z"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["open_lots"] == []
    before = db.read_bytes()
    for output in (db, fees):
        assert main([*args, "--json-output", str(output)]) == 1
        assert "cannot overwrite" in capsys.readouterr().out
    assert db.read_bytes() == before
    assert main([*args, "--json-output", str(tmp_path / "same"),
                 "--markdown-output", str(tmp_path / "same")]) == 1
    assert "distinct" in capsys.readouterr().out


def test_cli_validation_errors_are_json(paths, capsys):
    db, _, _ = paths
    assert main(["options", "data-audit", "--db", str(db), "--symbol", "qqq"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    _, fees, policy = paths
    fees.write_text("[]")
    assert main(["options", "compare-candidates", "--db", str(db),
                 "--observation-id", "unused", "--fees", str(fees),
                 "--policy", str(policy)]) == 1
    assert "must be objects" in json.loads(capsys.readouterr().out)["error"]
