import json
import math

import pytest

from longarc.analytics.probabilities import call_probabilities, estimate_probabilities
from longarc.cli import main


def inputs(**overrides):
    return dict(spot=100, strike=110, volatility=.2, years=1,
                risk_free_rate=.02, dividend_yield=0) | overrides


def request(**overrides):
    return {k: v for k, v in inputs().items() if k != "years"} | {
        "option_symbol": "EXAMPLE-CALL", "source_ref": "synthetic fixture",
        "as_of": "2025-01-01T20:00:00Z", "expires_at": "2026-01-01T20:00:00Z",
    } | overrides


def test_independent_first_passage_fixtures():
    result = call_probabilities(**inputs())
    assert result["probability_otm"] == pytest.approx(.683159022680827, abs=1e-14)
    assert result["probability_touch"] == pytest.approx(.633681954638345, abs=1e-14)
    # Zero log drift: reflection doubles the terminal-above probability.
    assert result["probability_touch"] == pytest.approx(2 * (1 - result["probability_otm"]))
    result = call_probabilities(**inputs(risk_free_rate=.05, dividend_yield=.02))
    assert result["probability_otm"] == pytest.approx(.665146766227905, abs=1e-14)
    assert result["probability_touch"] == pytest.approx(.648712164280616, abs=1e-14)


@pytest.mark.parametrize("years,volatility,spot,strike,rate,otm,touch", [
    (0, .2, 100, 110, .02, 1, 0), (0, .2, 110, 110, .02, 0, 1),
    (1, 0, 100, 110, .02, 1, 0), (1, 0, 100, 110, .2, 0, 1),
    (1, 0, 110, 100, -.2, 1, 1),
])
def test_deterministic_and_already_touched(years, volatility, spot, strike, rate, otm, touch):
    result = call_probabilities(**inputs(years=years, volatility=volatility, spot=spot,
                                        strike=strike, risk_free_rate=rate))
    assert result == {"probability_otm": otm, "probability_touch": touch}


def test_extreme_tail_and_tiny_volatility_do_not_overflow():
    for rate, expected in ((.2, 1), (-.2, 0)):
        p = call_probabilities(**inputs(volatility=1e-10, risk_free_rate=rate))
        assert p["probability_touch"] == expected
        assert p["probability_otm"] == 1 - expected
    assert call_probabilities(**inputs(strike=1e100))["probability_touch"] == 0
    # Independent Decimal continued-fraction reference. The weighted tail
    # contributes about 25% here, despite its unweighted CDF underflow risk.
    p = call_probabilities(**inputs(spot=1, strike=math.exp(.08), volatility=.005,
                                   risk_free_rate=.0400125))
    assert p["probability_touch"] == pytest.approx(8.322437687450903e-16,
                                                  rel=1e-10, abs=0)


def test_tiny_volatility_near_terminal_mean_retains_variance_term():
    rate = math.log1p(.1)
    p = call_probabilities(**inputs(volatility=1e-10, risk_free_rate=rate))
    assert p["probability_otm"] > .5
    assert p["probability_otm"] == pytest.approx(.5 + .5e-10 / math.sqrt(2 * math.pi),
                                                abs=1e-15)
    p = call_probabilities(**inputs(volatility=0, risk_free_rate=rate))
    assert p == {"probability_otm": 0, "probability_touch": 1}


def test_probability_bounds_and_strike_monotonicity():
    lower = call_probabilities(**inputs(strike=105))
    higher = call_probabilities(**inputs(strike=120))
    assert lower["probability_touch"] > higher["probability_touch"]
    assert lower["probability_otm"] < higher["probability_otm"]
    for p in (lower, higher):
        assert p["probability_touch"] >= 1 - p["probability_otm"]
    assert call_probabilities(**inputs(strike=90))["probability_touch"] == 1


@pytest.mark.parametrize("name,value", [("spot", 0), ("strike", -1), ("years", -1),
                                      ("volatility", -.1), ("risk_free_rate", math.nan),
                                      ("dividend_yield", math.inf), ("spot", True)])
def test_invalid_model_inputs(name, value):
    with pytest.raises(ValueError):
        call_probabilities(**inputs(**{name: value}))


def test_comparison_keeps_sources_time_gap_and_percentage_point_errors():
    provider = {"option_symbol": "EXAMPLE-CALL", "source_ref": "web fixture",
                "observed_at": "2025-01-01T19:59:00Z", "probability_touch": .63,
                "probability_otm": None}
    r = estimate_probabilities(request(provider_comparison=provider))
    assert r["measure"] == "risk_neutral" and r["time_years"] == 1
    comparison = r["comparison"]
    assert comparison["provider"] == provider
    assert comparison["observation_gap_seconds"] == 60
    assert comparison["differences"]["probability_otm"] is None
    d = comparison["differences"]["probability_touch"]
    assert d["signed_difference_pp"] == pytest.approx(.3681954638345)
    assert d["absolute_difference_pp"] == abs(d["signed_difference_pp"])
    assert any("different timestamps" in w for w in r["warnings"])


def test_expiry_clock_uses_actual_seconds_across_dst():
    r = estimate_probabilities(request(as_of="2026-10-30T16:00:00-04:00",
                                       expires_at="2026-11-06T16:00:00-05:00"))
    assert r["time_years"] == (7 * 86400 + 3600) / (365 * 86400)


@pytest.mark.parametrize("override", [
    {"expires_at": "2024-01-01T20:00:00Z"}, {"as_of": "2025-01-01T20:00:00"},
    {"dividend_yield": None}, {"volatility": "20%"}, {"option_symbol": ""},
    {"provider_comparison": {"option_symbol": "OTHER"}},
    {"provider_comparison": {"option_symbol": "EXAMPLE-CALL", "source_ref": "web",
     "observed_at": "2025-01-01T20:00:00Z", "probability_touch": 63}},
])
def test_incomplete_or_mismatched_requests_fail(override):
    with pytest.raises(ValueError):
        estimate_probabilities(request(**override))


def test_cli_exports_private_output_and_never_overwrites(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(request()))
    original = source.read_bytes()
    output = tmp_path / "result.json"
    args = ["options", "probabilities", "--file", str(source)]
    assert main([*args, "--json-output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["model_probabilities"]["probability_touch"] > .63
    before = output.read_bytes()
    assert main([*args, "--json-output", str(output)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert main([*args, "--json-output", str(source)]) == 1
    capsys.readouterr()
    assert source.read_bytes() == original and output.read_bytes() == before


def test_cli_bad_input_returns_json_error(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text("[]")
    assert main(["options", "probabilities", "--file", str(source)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
