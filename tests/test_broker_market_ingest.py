"""Shared market views and journal imports preserve provider and profile identity."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from test_etrade_capture import examples

from longarc.brokers.market import normalize_expiries, normalize_quotes
from longarc.brokers.profiles import BrokerProfile
from longarc.brokers.snapshots import account_inventory, verify_balance_account
from longarc.core.private_files import write_private_json
from longarc.data.api_capture import ingest_api_chain
from longarc.etrade import capture
from longarc.storage import store


def profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: str = "production"
) -> BrokerProfile:
    monkeypatch.chdir(tmp_path)
    return BrokerProfile(
        "primary",
        "etrade",
        "primary",
        environment,
        f"private/credentials/{environment}",
        "exact-key",
        "200",
    )


def test_underlying_quotes_common_units_missingness_and_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = profile(tmp_path, monkeypatch)
    _, quote = examples()
    row = quote["data"]["QuoteResponse"]["QuoteData"][0]
    row["All"].update(bid=142.4, ask=142.6)
    result = normalize_quotes(p, quote["data"], ["xyz", "IAU"])
    assert result["quotes"][0]["ask_u"] == 142_600_000
    assert result["quotes"][0]["is_delayed"] is True
    assert result["missing_symbols"] == ["IAU"]
    row["quoteStatus"] = "UNKNOWN"
    assert normalize_quotes(p, quote["data"], ["XYZ"])["quotes"][0]["is_delayed"] is None
    row["Product"]["symbol"] = "OTHER"
    with pytest.raises(ValueError, match="identity"):
        normalize_quotes(p, quote["data"], ["XYZ"])


def test_balance_account_pin_and_json_case_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = profile(tmp_path, monkeypatch)
    raw = {
        "AccountListResponse": {
            "accounts": {"account": [{"accountIdKey": "exact-key", "accountId": 200}]}
        }
    }
    assert account_inventory(p, raw)[0]["account_id"] == "200"
    verify_balance_account(p, {"BalanceResponse": {"accountId": 200}})
    for response in ({"BalanceResponse": {"accountId": 300}}, {"BalanceResponse": {}}):
        with pytest.raises(ValueError, match="unidentified|different"):
            verify_balance_account(p, response)
    schwab = BrokerProfile(
        "legacy", "schwab", "main", "production", "private/schwab", "hash", "100"
    )
    verify_balance_account(schwab, {"securitiesAccount": {"accountNumber": "100"}})
    with pytest.raises(ValueError):
        verify_balance_account(schwab, {"securitiesAccount": {"accountNumber": "200"}})


def test_expiry_views_do_not_claim_strike_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = profile(tmp_path, monkeypatch)
    response = {
        "OptionExpireDateResponse": {
            "ExpirationDate": [
                {"year": 2026, "month": 11, "day": 6},
                {"year": 2026, "month": 10, "day": 30},
            ]
        }
    }
    result = normalize_expiries(p, response, "xyz")
    assert result["expiry_dates"] == ["2026-10-30", "2026-11-06"]
    assert result["full_strike_coverage_verified"] is False
    response["OptionExpireDateResponse"]["ExpirationDate"][0]["day"] = True
    with pytest.raises(ValueError):
        normalize_expiries(p, response, "XYZ")


@pytest.mark.parametrize("environment", ["production", "sandbox"])
def test_generic_ingest_readback_idempotence_and_sandbox_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    p = profile(tmp_path, monkeypatch, environment)
    chain, quotes = examples()
    for envelope in (chain, quotes):
        envelope["request"].update(p.identity())
    a, b = Path("private/chain.json"), Path("private/quotes.json")
    write_private_json(a, chain)
    write_private_json(b, quotes)
    db = Path("private/observations.db")
    store.initialize(db)
    kwargs = {
        "source": "etrade_sandbox_synthetic" if environment == "sandbox" else "etrade_api",
        "adapter_tag": "etrade-api-chain-v1",
        "adapter_file": Path(capture.__file__),
        "normalize": capture.normalize_api_chain,
        "synthetic": environment == "sandbox",
    }
    first = ingest_api_chain(db, a, b, **kwargs)
    second = ingest_api_chain(db, a, b, **kwargs)
    assert first["record_id"] == second["record_id"] and second["write_status"] == "existing"
    payload = store.get_observation(db, first["record_id"])["payload"]
    assert payload["mode"] == ("shadow" if environment == "sandbox" else "observe")
    assert payload["quality"] == ("synthetic" if environment == "sandbox" else "unverified")
    assert payload["inputs"]["api_requests"]["chain"]["profile"] == "primary"
    assert payload["results"]["quotes"][0]["snapshot"]["iv_unit"] == "provider_raw"
    crossed = copy.deepcopy(quotes)
    crossed["request"]["profile"] = "secondary"
    write_private_json(b, crossed)
    with pytest.raises(ValueError, match="different broker profiles"):
        ingest_api_chain(db, a, b, **kwargs)
    write_private_json(b, quotes)
    wrong = dict(kwargs, synthetic=environment != "sandbox")
    with pytest.raises(ValueError, match="evidence environment"):
        ingest_api_chain(db, a, b, **wrong)
