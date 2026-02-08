"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class UniverseConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["AAPL"])
    timeframe: str = "1d"


class DataConfig(BaseModel):
    provider: str = "local_parquet"
    path: str = "./data"


class BrokerConfig(BaseModel):
    adapter: str = "paper_sim"


class StrategyConfig(BaseModel):
    name: str = "sma_cross"
    params: dict[str, Any] = Field(default_factory=dict)


class PortfolioConfig(BaseModel):
    base_currency: str = "USD"
    initial_cash: float = 100000.0


class RiskConfig(BaseModel):
    max_position_notional: float = 20000.0
    max_order_notional: float = 5000.0
    max_daily_loss: float = 1000.0
    kill_switch: bool = True


class CostModelConfig(BaseModel):
    fee_bps: float = 1.0
    slippage_bps: float = 2.0


class RuntimeConfig(BaseModel):
    schedule: str | None = None
    dry_run: bool = False


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "backtest"
    timezone: str = "America/New_York"
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    cost_model: CostModelConfig = Field(default_factory=CostModelConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)


def load_config(path: str | Path) -> AppConfig:
    """Load and validate config from YAML."""
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError("Config file must parse to a YAML object.")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid config at {config_path}: {exc}") from exc
