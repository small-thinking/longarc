"""Synthetic E*TRADE response fixtures; no account or live market access."""

from __future__ import annotations

import copy

import pytest

from longarc.etrade.capture import normalize_api_chain


def examples() -> tuple[dict, dict]:
    chain = {
        "provider": "etrade-api", "resource": "chain", "captured_at": "2026-10-12T20:00:00Z",
        "request": {"broker": "etrade", "login_alias": "primary", "environment": "production",
                    "symbol": "XYZ", "expiry": "2026-10-16"},
        "data": {"OptionChainResponse": {
            "OptionPair": [{"Call": {
                "optionCategory": "STANDARD", "optionRootSymbol": "XYZ",
                "timeStamp": 1791835198, "adjustedFlag": False,
                "optionType": "CALL", "strikePrice": 150.0, "symbol": "XYZ",
                "osiKey": "XYZ   261016C00150000", "bid": 1.25, "ask": 1.35,
                "bidSize": 8, "askSize": 9, "lastPrice": 1.30,
                "volume": 17, "openInterest": 300,
                "OptionGreeks": {"delta": 0.32, "theta": -0.07, "gamma": 0.04,
                                 "vega": 0.12, "iv": 0.24, "currentValue": True},
            }}], "timeStamp": 1791835197, "quoteType": "DELAYED",
            "SelectedED": {"year": 2026, "month": 10, "day": 16},
        }},
    }
    quotes = {
        "provider": "etrade-api", "resource": "quotes", "captured_at": "2026-10-12T20:00:01Z",
        "request": {"broker": "etrade", "login_alias": "primary", "environment": "production",
                    "symbols": ["XYZ"]},
        "data": {"QuoteResponse": {"QuoteData": [{
            "dateTimeUTC": 1791835199, "quoteStatus": "DELAYED",
            "All": {"lastTrade": 142.5}, "Product": {"symbol": "XYZ", "securityType": "EQ"},
        }]}},
    }
    return chain, quotes


def test_maps_official_shapes_and_preserves_unknown_terms() -> None:
    chain, quotes = examples()
    result = normalize_api_chain(chain, quotes)
    row = result["results"]["quotes"][0]
    contract, snap = row["contract"], row["snapshot"]
    assert result["symbol"] == "XYZ"
    assert contract["option_symbol"] == "XYZ   261016C00150000"
    assert contract["multiplier"] is None and contract["deliverables"] is None
    assert contract["non_standard"] is False
    assert snap["bid_u"] == 1_250_000 and snap["underlying_price_u"] == 142_500_000
    assert snap["quote_at"] == "2026-10-12T19:59:58Z"
    assert snap["underlying_at"] == "2026-10-12T19:59:59Z"
    assert snap["greeks_at"] is None
    assert snap["iv"] == 0.24 and snap["iv_unit"] == "provider_raw"
    assert snap["probability_touch"] is None and snap["probability_otm"] is None
    assert result["results"]["is_delayed"] is True
    assert result["results"]["requested_coverage_complete"] is False
    assert "unknown_multiplier" in row["warnings"]


@pytest.mark.parametrize("mutation", [
    lambda chain, quotes: chain["data"]["OptionChainResponse"]["SelectedED"].update(day=17),
    lambda chain, quotes: chain["data"]["OptionChainResponse"]["OptionPair"][0]["Call"].update(
        optionRootSymbol="ABC"),
    lambda chain, quotes: chain["data"]["OptionChainResponse"]["OptionPair"][0]["Call"].update(
        osiKey="ABC   261016C00150000"),
    lambda chain, quotes: quotes["data"]["QuoteResponse"]["QuoteData"][0]["Product"].update(
        symbol="ABC"),
    lambda chain, quotes: quotes["request"].update(login_alias="other"),
])
def test_rejects_profile_and_identity_conflicts(mutation) -> None:
    chain, quotes = examples()
    mutation(chain, quotes)
    with pytest.raises(ValueError):
        normalize_api_chain(chain, quotes)


def test_sandbox_stored_symbol_mismatch_is_rejected() -> None:
    chain, quotes = examples()
    chain["request"].update(environment="sandbox", symbol="QQQ")
    quotes["request"].update(environment="sandbox", symbols=["QQQ"])
    with pytest.raises(ValueError):
        normalize_api_chain(chain, quotes)


def test_sandbox_source_warning_and_single_root_unwrap() -> None:
    chain, quotes = examples()
    chain["request"]["environment"] = quotes["request"]["environment"] = "sandbox"
    result = normalize_api_chain(chain, quotes)
    assert "sandbox_stored_data_not_current_market" in result["results"]["warnings"]
    nested = copy.deepcopy(chain)
    nested["data"] = {"OptionChainResponse": nested["data"]}
    with pytest.raises(ValueError):
        normalize_api_chain(nested, quotes)


def test_absent_delay_disclosure_and_stale_greeks_stay_explicit() -> None:
    chain, quotes = examples()
    body = chain["data"]["OptionChainResponse"]
    body.pop("quoteType")
    body["OptionPair"][0]["Call"]["OptionGreeks"]["currentValue"] = False
    quotes["data"]["QuoteResponse"]["QuoteData"][0].pop("quoteStatus")
    result = normalize_api_chain(chain, quotes)["results"]
    assert result["is_delayed"] is None
    assert "unknown_delay_status" in result["warnings"]
    assert "greeks_not_current" in result["quotes"][0]["warnings"]


@pytest.mark.parametrize("chain_status, quote_status, expected", [
    ("REALTIME", "REALTIME", False),
    ("REALTIME", "EH_REALTIME", False),
    ("REALTIME", "CLOSING", None),
    ("UNKNOWN", "REALTIME", None),
    ("INVALID", "INVALID", None),
    ("DELAYED", "UNKNOWN", True),
])
def test_delay_requires_documented_statuses(chain_status, quote_status, expected) -> None:
    chain, quotes = examples()
    chain["data"]["OptionChainResponse"]["quoteType"] = chain_status
    quotes["data"]["QuoteResponse"]["QuoteData"][0]["quoteStatus"] = quote_status
    result = normalize_api_chain(chain, quotes)["results"]
    assert result["is_delayed"] is expected
    assert ("unknown_delay_status" in result["warnings"]) is (expected is None)


def test_quote_request_case_normalizes_once_for_response_membership() -> None:
    chain, quotes = examples()
    quotes["request"]["symbols"] = ["xyz"]
    assert normalize_api_chain(chain, quotes)["symbol"] == "XYZ"


def test_invalid_delta_and_unknown_greek_current_value() -> None:
    chain, quotes = examples()
    greek = chain["data"]["OptionChainResponse"]["OptionPair"][0]["Call"]["OptionGreeks"]
    greek["delta"] = 1.5
    greek.pop("currentValue")
    result = normalize_api_chain(chain, quotes)["results"]["quotes"][0]
    assert result["snapshot"]["delta"] is None
    assert "missing_delta" in result["warnings"]
    assert "unknown_greeks_current_value" in result["warnings"]
    assert result["contract"]["multiplier"] is None
    assert result["snapshot"]["iv_unit"] == "provider_raw"
