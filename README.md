# LongArc
Low-frequency, audit-first trading system evolving toward agentic workflows.

## Status (as of 2026-02-08)

Current stage: M0 scaffold complete, M1 in progress.

- Python package `longarc` with install/run via `uv`.
- Config schema + YAML loading (`src/longarc/core/config.py`).
- Structured logging bootstrap (`src/longarc/core/logging.py`).
- CLI surface (`src/longarc/cli.py`): `data download`, `data show-latest`, `backtest`, `paper-sim run`, `paper run`, `report`.
- Data provider abstraction with pluggable providers (`local_parquet`, `polygon`).
- Local parquet-backed data store with sort/dedup behavior.
- CI quality gate (governance + lint + type check + tests) in GitHub Actions.

Not implemented yet:
- Backtest engine.
- Paper simulation engine.
- Live paper broker adapters.
- Report generation logic.

Implemented today:
- `data download --provider local_parquet`: deterministic synthetic bars for development/testing.
- `data download --provider polygon`: real OHLCV ingestion from Polygon aggregates endpoint.
- `data show-latest`: latest stored bar from local parquet.

Commands still as placeholders:
- `backtest`, `paper-sim run`, `paper run`, `report`.

## Quick Start

- Python 3.11+
- `uv`

```bash
uv sync --extra dev
uv run python -m longarc.cli --help
uv run python -m longarc.cli data download --provider local_parquet --symbols AAPL --start 2024-01-01 --end 2024-01-03
uv run python -m longarc.cli data download --provider polygon --api-key "$POLYGON_API_KEY" --symbols AAPL --start 2024-01-01 --end 2024-01-03
uv run python -m longarc.cli data show-latest --symbol AAPL
uv run python -m longarc.cli backtest --config config/config.example.yaml
uv run python -m longarc.cli paper-sim run --config config/config.example.yaml
uv run python -m longarc.cli paper run --config config/config.example.yaml
uv run python -m longarc.cli report --run-id demo-001
bash scripts/run_backtest.sh
bash scripts/run_paper.sh
```

## Configuration

Config example: `config/config.example.yaml`

- `mode`: `backtest` / `paper_sim` / `paper` (future behavior)
- `universe`: symbols + timeframe
- `data`: provider + local path (`local_parquet` or `polygon` for `data download`)
- `broker`: adapter type
- `strategy`: strategy name + params
- `portfolio`, `risk`, `cost_model`, `runtime`

Current behavior: config is validated and loaded; data download/show-latest works, while strategy/backtest/paper/report engines are not implemented.

## Dev Checks

```bash
uv run ruff check .
uv run mypy src
uv run pytest
bash scripts/ci/validate_governance.sh
```

## Repo Map

```text
config/                  Example app configuration
docs/                    Plan and progress tracking
scripts/                 Run helpers and CI governance check
src/longarc/cli.py       CLI entrypoint
src/longarc/core/        Config and logging modules
tests/                   Smoke tests
```

Roadmap: `docs/plan.md`  
Progress log: `docs/track.md`
