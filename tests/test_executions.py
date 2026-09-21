import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from longarc.analytics.executions import performance, record_execution
from longarc.cli import main
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


def test_cli_records_and_filters_two_symbols(db, tmp_path, capsys):
    for symbol in ("QQQ", "IAU"):
        payload = event(symbol, episode_id=symbol)
        payload["contract"]["symbol"] = symbol
        file = tmp_path / f"{symbol}.json"
        file.write_text(json.dumps(payload))
        assert main(["options", "execution-add", "--db", str(db), "--file", str(file)]) == 0
        capsys.readouterr()
    args = ["options", "performance", "--db", str(db), "--account", "test",
            "--mode", "shadow"]
    assert main(args) == 0
    aggregate = json.loads(capsys.readouterr().out)
    assert aggregate["execution_count"] == 2
    assert set(aggregate["by_symbol"]) == {"QQQ", "IAU"}
    assert main(args + ["--symbol", "IAU"]) == 0
    filtered = json.loads(capsys.readouterr().out)
    assert filtered["execution_count"] == 1
    assert filtered["open_lots"][0]["contract"]["symbol"] == "IAU"


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


def symbol_event(symbol, key, action="STO", **updates):
    result = event(key, action, episode_id=f"{symbol}-episode", **updates)
    result["contract"]["symbol"] = symbol
    return result


def test_mixed_symbols_partial_roll_assignment_and_expiration(db):
    events = [
        symbol_event("QQQ", "qqq-open"),
        symbol_event("QQQ", "qqq-close", "BTC", opening_execution_id="qqq-open"),
        symbol_event(
            "QQQ", "qqq-roll", quantity=1, fees_u=650000,
            executed_at="2026-09-21T20:01:00Z",
        ),
        symbol_event("IAU", "iau-open", quantity=1, price_u=3000000, fees_u=650000),
        symbol_event(
            "IAU", "iau-assign", "ASSIGN", opening_execution_id="iau-open",
            price_u=0, fees_u=0, executed_at="2026-09-22T20:00:00Z",
        ),
        symbol_event("BRK.B", "brk-open", quantity=1, price_u=4000000, fees_u=0),
        symbol_event(
            "BRK.B", "brk-expire", "EXPIRE", opening_execution_id="brk-open",
            price_u=0, fees_u=0, executed_at="2026-10-17T00:01:00Z",
        ),
    ]
    for execution in events:
        created = record_execution(db, execution)
        replayed = record_execution(db, execution)
        assert replayed["status"] == "existing"
        assert replayed["record_id"] == created["record_id"]
    report = performance(db, "test", "shadow")
    assert report["symbol"] is None
    assert report["execution_count"] == 7
    assert report["realized_gross_option_pnl_u"] == "800000000"
    assert report["realized_net_option_pnl_u"] == "798050000"
    assert {
        key: value["realized_net_option_pnl_u"] for key, value in report["by_symbol"].items()
    } == {
        "QQQ": "98700000", "IAU": "299350000", "BRK.B": "400000000",
    }
    assert {e["symbol"] for e in report["closed_events"]} == {"QQQ", "IAU", "BRK.B"}
    assert {e["symbol"] for e in report["episodes"]} == {"QQQ", "IAU", "BRK.B"}
    assigned = next(e for e in report["closed_events"] if e["symbol"] == "IAU")
    assert assigned["stock_pnl_unknown"]
    assert [
        (lot["opening_execution_id"], lot["remaining_quantity"]) for lot in report["open_lots"]
    ] == [
        ("qqq-open", 1), ("qqq-roll", 1),
    ]
    qqq = performance(db, "test", "shadow", symbol="QQQ")
    assert qqq["symbol"] == "QQQ"
    assert qqq["execution_count"] == 3
    assert qqq["realized_net_option_pnl_u"] == "98700000"
    assert set(qqq["by_symbol"]) == {"QQQ"}
    assert all(e["symbol"] == "QQQ" for e in qqq["closed_events"] + qqq["episodes"])
    assert performance(db, "test", "shadow", symbol="SPY")["execution_count"] == 0


def test_unknown_fees_only_affect_own_symbol_and_aggregate(db):
    record_execution(db, symbol_event("QQQ", "qqq-open", quantity=1, fees_u=0))
    record_execution(db, symbol_event("IAU", "iau-open", quantity=1, fees_u=None))
    record_execution(db, symbol_event(
        "IAU", "iau-close", "BTC", opening_execution_id="iau-open", fees_u=0,
    ))
    record_execution(db, symbol_event(
        "QQQ", "qqq-close", "BTC", opening_execution_id="qqq-open", price_u=3000000,
        fees_u=0, executed_at="2026-09-22T20:00:00Z",
    ))
    report = performance(db, "test", "shadow")
    assert report["realized_gross_option_pnl_u"] == "0"
    assert report["realized_net_option_pnl_u"] is None
    assert report["realized_net_max_drawdown_u"] is None
    assert not report["fees_complete"]
    assert report["by_symbol"]["IAU"]["realized_net_option_pnl_u"] is None
    assert not report["by_symbol"]["IAU"]["fees_complete"]
    assert report["by_symbol"]["QQQ"]["fees_complete"]
    assert report["by_symbol"]["QQQ"]["realized_net_option_pnl_u"] == "-100000000"
    assert report["by_symbol"]["QQQ"]["realized_net_max_drawdown_u"] == "100000000"
    filtered = performance(db, "test", "shadow", symbol="QQQ")
    assert filtered["closed_events"][0]["cumulative_net_option_pnl_u"] == "-100000000"


@pytest.mark.parametrize("field,value", [
    ("symbol", "IAU"), ("expiry_date", "2026-11-20"),
    ("strike_u", 780000000), ("multiplier", 10),
])
def test_close_requires_full_contract_identity(db, field, value):
    record_execution(db, event())
    closing = event("bad-close", "BTC")
    closing["contract"][field] = value
    with pytest.raises(ValueError, match="Closing contract differs"):
        record_execution(db, closing)
    assert performance(db, "test", "shadow")["execution_count"] == 1


def test_episode_and_execution_ids_cannot_mix_symbols(db):
    record_execution(db, event())
    iau = event("iau-open")
    iau["contract"]["symbol"] = "IAU"
    with pytest.raises(ValueError, match="Episode ID already used for a different symbol"):
        record_execution(db, iau)
    with pytest.raises(ValueError, match="Execution ID already used with different content"):
        record_execution(db, iau | {"external_execution_id": "open", "episode_id": "iau"})
    # Account and mode are independent namespaces for episodes and broker IDs.
    assert record_execution(db, iau | {"account": "another"})["status"] == "created"
    assert record_execution(db, iau | {"mode": "manual"})["status"] == "created"
    assert performance(db, "test", "shadow")["execution_count"] == 1


@pytest.mark.parametrize("symbol", ["iau", " IAU", "IAU ", "I AU", "💰", "A" * 16])
def test_execution_and_performance_reject_noncanonical_symbols(db, symbol):
    with pytest.raises(ValueError):
        record_execution(db, symbol_event(symbol, "bad"))
    with pytest.raises(ValueError):
        performance(db, "test", "shadow", symbol=symbol)


def test_ledger_still_rejects_put_contracts(db):
    put = symbol_event("IAU", "bad-put")
    put["contract"]["option_type"] = "PUT"
    with pytest.raises(ValueError, match="CALL"):
        record_execution(db, put)


def test_existing_qqq_record_identity_is_unchanged(db):
    # Captured from the QQQ-only v1 ledger for this exact fixture. Existing rows
    # and imported broker IDs must remain replayable without a migration.
    legacy_record_id = "202346c471228b1f660101b84b70b389bee568495fec8ae37ce847b5c8b9a582"
    created = record_execution(db, event())
    assert created["record_id"] == legacy_record_id
    assert record_execution(db, event()) == {
        "status": "existing", "record_id": legacy_record_id, "readback_verified": True,
    }
