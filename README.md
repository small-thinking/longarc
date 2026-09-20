# LongArc
Local investment tooling focused on the QQQ covered-call plan.

## Current focus

Only the migrated QQQ covered-call strategy and its iteration plan are active. Start with `private/qqq-covered-call-plan/README.md` in this local investment workspace; those personal materials are excluded from Git. See [current scope](docs/plan.md) and [progress](docs/track.md).

The previous generic trading roadmap has been retired. The next requested step is database selection, controlled read/write access, and recovery. That work has not been implemented.

Reusable foundations: Python package and CLI, configuration loading, logging, local Parquet storage, synthetic development bars, and a Polygon OHLCV adapter. These are candidates for reuse, not a working QQQ options system. Backtest, paper, and report commands remain legacy placeholders. The example configuration is a scaffold fixture, not an approved investment policy; no strategy is selected by default.

## Quick Start

- Python 3.11+
- `uv`

```bash
uv sync --extra dev
uv run python -m longarc.cli --help
uv run python -m longarc.cli data download
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
- `data`: provider + local path
- `broker`: adapter type
- `strategy`: strategy name + params
- `portfolio`, `risk`, `cost_model`, `runtime`

Current behavior: config is validated and loaded, but not yet executed by a strategy/backtest/paper engine.

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
