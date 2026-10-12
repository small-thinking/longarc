"""Explicit risk-neutral GBM estimates; never broker-provided probabilities."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

MODEL = "risk-neutral-gbm-call-v1"
YEAR_SECONDS = 365 * 24 * 60 * 60


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return result


def _cdf(z: float) -> float:
    return math.erfc(-z / math.sqrt(2)) / 2


def _weighted_tail(z1: float, z2: float, exponent: float) -> float:
    # For large negative z2, combine the exponent with the Gaussian density
    # algebraically: exponent - z2**2/2 == -z1**2/2. This avoids inf * 0
    # and cancellation when volatility is tiny. Mills' ratio is asymptotic.
    if z2 < -12:
        term = total = 1.0
        for n in range(1, 13):
            term *= -(2 * n - 1) / (z2 * z2)
            total += term
        log_term = -z1 * z1 / 2 - math.log(-z2) - math.log(2 * math.pi) / 2
        return math.exp(log_term) * total
    return math.exp(exponent + math.log(_cdf(z2)))


def call_probabilities(
    *, spot: float, strike: float, volatility: float, years: float,
    risk_free_rate: float, dividend_yield: float,
) -> dict[str, float]:
    """P_Q(S_T < K) and P_Q(max S_t >= K), including t=0, for a CALL.

    Volatility and continuously compounded r/q are annual fractions. Touch
    counts an already reached upper barrier as 1. Constant volatility, continuous
    monitoring and continuous dividend yield do not model assignment or jumps.
    """
    values = {name: _number(value, name) for name, value in {
        "spot": spot, "strike": strike, "volatility": volatility, "years": years,
        "risk_free_rate": risk_free_rate, "dividend_yield": dividend_yield,
    }.items()}
    spot, strike = values["spot"], values["strike"]
    volatility, years = values["volatility"], values["years"]
    if spot <= 0 or strike <= 0 or volatility < 0 or years < 0:
        raise ValueError("spot/strike must be positive; volatility/years nonnegative")
    relative_distance = (strike - spot) / spot
    distance = (math.log1p(relative_distance) if -.5 < relative_distance < .5
                else math.log(strike) - math.log(spot))
    drift = (values["risk_free_rate"] - values["dividend_yield"]) * years
    deviation = volatility * math.sqrt(years)
    if deviation == 0:
        otm = float(drift < distance)
        touch = float(spot >= strike or drift >= distance)
    else:
        log_drift = math.fsum((values["risk_free_rate"] * years,
                              -values["dividend_yield"] * years,
                              -deviation * deviation / 2))
        z1 = math.fsum((values["risk_free_rate"] * years,
                       -values["dividend_yield"] * years,
                       -deviation * deviation / 2, -distance)) / deviation
        otm = _cdf(-z1)
        if spot >= strike:
            touch = 1.0
        else:
            z2 = math.fsum((-values["risk_free_rate"] * years,
                           values["dividend_yield"] * years,
                           deviation * deviation / 2, -distance)) / deviation
            exponent = 2 * (log_drift / deviation) * (distance / deviation)
            touch = min(1.0, max(0.0, _cdf(z1) + _weighted_tail(z1, z2, exponent)))
    if not math.isfinite(otm) or not math.isfinite(touch):
        raise ValueError("Inputs exceed the model's numerical range")
    return {"probability_otm": otm, "probability_touch": touch}


def estimate_probabilities(request: dict[str, Any]) -> dict[str, Any]:
    """Reproducible inputs and optional web comparison, without DB writes."""
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    for name in ("option_symbol", "source_ref"):
        if not isinstance(request.get(name), str) or not request[name].strip():
            raise ValueError(f"{name} is required")
    now = _time(request.get("as_of"), "as_of")
    expiry = _time(request.get("expires_at"), "expires_at")
    years = (expiry - now).total_seconds() / YEAR_SECONDS
    params = {name: _number(request.get(name), name) for name in (
        "spot", "strike", "volatility", "risk_free_rate", "dividend_yield")}
    model = call_probabilities(**params, years=years)
    comparison = None
    provider = request.get("provider_comparison")
    warnings = ["Model estimate, not a broker probability or empirical trade forecast.",
                "Constant volatility and continuous yield; no discrete dividends or jumps.",
                "Upper-strike touch includes t=0; it is not assignment probability."]
    if provider is not None:
        if (not isinstance(provider, dict)
                or provider.get("option_symbol") != request["option_symbol"]):
            raise ValueError("provider comparison must match the exact option_symbol")
        if not isinstance(provider.get("source_ref"), str) or not provider["source_ref"].strip():
            raise ValueError("provider comparison source_ref is required")
        observed = _time(provider.get("observed_at"), "provider observed_at")
        differences: dict[str, dict[str, float] | None] = {}
        for name, estimate in model.items():
            value = provider.get(name)
            if value is None:
                differences[name] = None
                continue
            value = _number(value, name)
            if not 0 <= value <= 1:
                raise ValueError("provider probabilities must be fractions between 0 and 1")
            signed = 100 * (estimate - value)
            differences[name] = {"provider": value, "estimate": estimate,
                                 "signed_difference_pp": signed,
                                 "absolute_difference_pp": abs(signed)}
        comparison = {"provider": provider, "differences": differences,
                      "observation_gap_seconds": (now - observed).total_seconds(),
                      "interpretation": "Agreement comparison, not calibrated accuracy."}
        if observed != now:
            warnings.append("Provider and model observations have different timestamps.")
    return {"format": "call-probabilities-v1", "status": "ok", "model": MODEL,
            "measure": "risk_neutral", "inputs": request, "time_years": years,
            "day_count": "ACT/365F", "rate_convention": "continuous annual fractions",
            "model_probabilities": model, "comparison": comparison, "warnings": warnings}
