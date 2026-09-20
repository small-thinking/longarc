# LongArc

Local investment tooling being rebuilt around the QQQ covered-call plan.

## Current scope

The only active product plan is the migrated QQQ strategy: infrequent manual trades, frequent observation and deterministic calculations, and complete records. Start with `private/qqq-covered-call-plan/README.md` in the local investment workspace. The five personal planning files are excluded from Git and are not included in a fresh clone.

SQLite storage foundation is implemented: initialization, observation writes/reads, health checks and backup/restore through a JSON CLI. See [storage operations](docs/storage.md). Pure quote and explicit-quantity scenario calculations with audit logging are available. Automated option-chain collection, policy/trade domains, alerts and the complete workflow remain **unimplemented**.

The former generic trading framework has been removed: no backtest/paper/report placeholder commands, application trading configuration, preset capital/risk budgets, or broker execution credentials. Those old commands now fail argument parsing instead of returning success.

## Holding tracking

Multiple short-call opening lots can each retain repeated price/Greek snapshots without trading. See [holding tracking and schema](docs/holding-tracking.md). Supplied openings remain provisional; current quantities and actual exits/rolls are not reconciled.

## Calculations

Use `uv run python -m longarc.cli calc --db PATH --file REQUEST.json` to calculate quote metrics, history changes, and explicit close/roll scenarios, then log inputs/results for readback. See [formulas, units, logging and dry run](docs/calculations.md). Strategy decisions and current portfolio P&L are not inferred.

## Storage tools

Use `uv run python -m longarc.cli db --help`. Each operation requires `--db`; the local database is `private/longarc.sqlite3`. This is an append-only observation journal, not a trading ledger or approval system. No database server needs starting.

## Retained utilities

- Python package, logging, CLI and quality checks.
- OHLCV provider interface, a Polygon adapter, and a Parquet store with deduplication tests.
- A deterministic synthetic bar generator for development only.

These utilities are candidates for reuse, not a validated options data pipeline. Every download must specify its provider. `local_parquet` generates synthetic bars; it does not retrieve market prices. Data-source entitlements, provenance and production storage remain part of the new implementation work.

## Development

Keep PRs small and focused, with concise code that is easy for a human to review. Database changes must include their schema and migration impact in the PR.

Python 3.11+ and `uv`:

```bash
uv sync --locked --extra dev
uv run python -m longarc.cli --help
uv run python -m longarc.cli data download --provider local_parquet --symbols QQQ --start 2024-01-01 --end 2024-01-03 --data-path ./private/synthetic-bars
uv run python -m longarc.cli data show-latest --symbol QQQ --data-path ./private/synthetic-bars
```

For the existing read-only Polygon adapter, select `--provider polygon`, set `POLYGON_API_KEY` in the process environment, and use a separate data directory. `.env.example` is a reference; the CLI does not automatically load `.env` files.

```bash
uv run ruff check .
uv run mypy src
uv run pytest
bash scripts/ci/validate_governance.sh
```

[Current plan](docs/plan.md) · [High-level work note](docs/track.md)
