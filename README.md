# LongArc
Low-frequency, audit-first trading system evolving toward agentic workflows.

## Status (as of 2026-02-09)

Current stage: M0 scaffold complete, M1 not implemented yet.

- Python package `longarc` with install/run via `uv`.
- Config schema + YAML loading (`src/longarc/core/config.py`).
- Structured logging bootstrap (`src/longarc/core/logging.py`).
- CLI surface (`src/longarc/cli.py`): `data download`, `data show-latest`, `backtest`, `paper-sim run`, `paper run`, `report`.
- CI quality gate (governance + lint + type check + tests) in GitHub Actions.
- Contributor workflow now enforces product-facing status updates in both README and tracking after every change.

Not implemented yet:
- Real market data download/storage logic.
- Backtest engine.
- Paper simulation engine.
- Live paper broker adapters.
- Report generation logic.

All CLI business commands currently log "not implemented yet" and exit successfully.

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
