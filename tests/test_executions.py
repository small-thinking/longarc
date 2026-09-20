from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from longarc.analytics.executions import performance, record_execution
from longarc.storage.store import initialize


def event(key="open", action="STO", **updates):
    result = {
        "account": "test",
        "mode": "shadow",
        "external_execution_id": key,
        "action": action,
        "contract": {
            "symbol": "QQQ",
            "option_type": "CALL",
            "expiry_date": "2026-10-16",
            "strike_u": 770000000,
            "multiplier": 100,
        },
        "quantity": 2,
        "price_u": 2000000,
        "fees_u": 1300000,
        "executed_at": "2026-09-20T20:00:00Z",
        "source": "synthetic_confirmation",
        "evidence_ref": "fixture:fill",
        "episode_id": "strategy-1",
    }
    if action != "STO":
        result.update(
            opening_execution_id="open",
            quantity=1,
            price_u=1000000,
            fees_u=650000,
            executed_at="2026-09-21T20:00:00Z",
        )
    return result | updates


@pytest.fixture
def db(tmp_path: Path):
    path = tmp_path / "test.sqlite3"
    initialize(path)
    return path


def test_partial_close_and_fee_allocation(db):
    record_execution(db, event())
    record_execution(db, event("close", "BTC"))
    report = performance(db, "test", "shadow")
    assert report["realized_gross_option_pnl_u"] == "100000000"
    assert report["realized_net_option_pnl_u"] == "98700000"
    assert report["open_lots"][0]["remaining_quantity"] == 1
    assert performance(db, "test", "manual")["execution_count"] == 0


def test_roll_is_two_legs_and_episode_closed_pnl(db):
    record_execution(db, event(quantity=1, fees_u=650000))
    record_execution(db, event("close", "BTC", price_u=3000000))
    record_execution(
        db, event("rolled-open", quantity=1, fees_u=650000, executed_at="2026-09-21T20:01:00Z")
    )
    report = performance(db, "test", "shadow")
    assert report["realized_net_option_pnl_u"] == "-101300000"
    assert report["realized_net_max_drawdown_u"] == "101300000"
    assert report["episodes"][0]["realized_net_option_pnl_u"] == "-101300000"
    assert report["open_lots"][0]["opening_execution_id"] == "rolled-open"


def test_missing_fees_and_assignment(db):
    record_execution(db, event(fees_u=None))
    record_execution(db, event("assigned", "ASSIGN", price_u=0, fees_u=0))
    result = performance(db, "test", "shadow")
    assert result["realized_net_option_pnl_u"] is None
    assert result["realized_net_max_drawdown_u"] is None
    assert result["closed_events"][0]["stock_pnl_unknown"]
    assert result["realized_gross_option_pnl_u"] == "200000000"


def test_duplicate_conflict_and_chronology(db):
    original = event()
    assert record_execution(db, original)["status"] == "created"
    assert record_execution(db, original)["status"] == "existing"
    with pytest.raises(ValueError, match="different content"):
        record_execution(db, original | {"price_u": 1})
    with pytest.raises(ValueError, match="precedes"):
        record_execution(db, event("early", "BTC", executed_at="2026-09-19T20:00:00Z"))
    record_execution(db, event("close", "BTC", quantity=2))
    assert record_execution(db, event("close", "BTC", quantity=2))["status"] == "existing"
    with pytest.raises(ValueError, match="exceed"):
        record_execution(db, event("over", "BTC"))


def test_concurrent_closes_cannot_overclose(db):
    record_execution(db, event(quantity=1))

    def close(key):
        try:
            return record_execution(db, event(key, "BTC"))["status"]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(close, ["a", "b"])) == ["created", "rejected"]
    assert performance(db, "test", "shadow")["open_lots"] == []


def test_requires_evidence_and_explicit_contract_match(db):
    with pytest.raises(ValueError):
        record_execution(db, event(evidence_ref=""))
    record_execution(db, event())
    bad = event("close", "BTC")
    bad["contract"]["strike_u"] = 775000000
    with pytest.raises(ValueError, match="differs"):
        record_execution(db, bad)


def test_expiration_zero_price_and_proportional_fee_residual(db):
    record_execution(db, event(quantity=3, fees_u=1))
    for index in range(3):
        record_execution(
            db,
            event(
                f"expiry-{index}", "EXPIRE", price_u=0, fees_u=0, executed_at="2026-10-17T00:01:00Z"
            ),
        )
    result = performance(db, "test", "shadow")
    from decimal import Decimal

    assert Decimal(result["realized_net_option_pnl_u"]) == Decimal(600000000) - 1
    assert result["open_lots"] == []


def test_expiration_before_expiry_rejected(db):
    record_execution(db, event())
    with pytest.raises(ValueError, match="precede expiry"):
        record_execution(db, event("early-expiry", "EXPIRE", price_u=0))


def test_episode_and_policy_provenance(db):
    from longarc.storage.store import get_observation

    receipt = record_execution(db, event(policy_hash="a" * 64, decision_record_id="decision-1"))
    assert receipt["readback_verified"]
    assert get_observation(db, receipt["record_id"])["policy_hash"] == "a" * 64
    with pytest.raises(ValueError, match="episode differs"):
        record_execution(db, event("wrong", "BTC", episode_id="another"))
    record_execution(db, event("close", "BTC"))
    result = performance(db, "test", "shadow")
    assert result["closed_events"][0]["opening_decision_record_id"] == "decision-1"
    assert result["episodes"][0]["policy_hashes"] == ["a" * 64]


def test_execution_and_performance_cli(db, tmp_path, capsys):
    import json

    from longarc.cli import main

    source = tmp_path / "execution.json"
    source.write_text(json.dumps(event()))
    assert main(["options", "execution-add", "--db", str(db), "--file", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["readback_verified"]
    assert main(["options", "performance", "--db", str(db), "--account", "test",
                 "--mode", "shadow"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["open_lots"][0]["remaining_quantity"] == 2
    assert report["realized_net_option_pnl_u"] == "0"
