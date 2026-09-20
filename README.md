# LongArc

Local investment tooling being rebuilt around the QQQ covered-call plan.

## Current scope

The only active product plan is the migrated QQQ strategy: infrequent manual trades, frequent observation and deterministic calculations, and complete records. Start with `private/qqq-covered-call-plan/README.md` in the local investment workspace. The five personal planning files are excluded from Git and are not included in a fresh clone.

SQLite storage foundation is implemented: initialization, observation writes/reads, health checks and backup/restore through a JSON CLI. See [storage operations](docs/storage.md). Pure quote and explicit-quantity scenario calculations with audit logging are available. Browser-assisted candidate-chain capture, sparse history reports and dated fee estimates are available through `options`; see [the runbook](docs/schwab-readonly.md#repeatable-candidate-capture-and-history). Unattended API collection, scheduling, policy/trade domains and alerts remain **unimplemented**.

The former generic trading framework has been removed: no backtest/paper/report placeholder commands, application trading configuration, preset capital/risk budgets, or broker execution credentials. Those old commands now fail argument parsing instead of returning success.

## Holding tracking

Multiple short-call opening lots can each retain repeated price/Greek snapshots without trading. See [holding tracking and schema](docs/holding-tracking.md). Supplied openings remain provisional; current quantities and actual exits/rolls are not reconciled.

Repeated imports are idempotent. After verifying a closed market, `options ingest --closed-session YYYY-MM-DD` reuses an unchanged capture from that last market session, preserving its original collection time. Changed values or coverage still create a new record; active-market reads retain their separate times.

## Calculations

Use `uv run python -m longarc.cli calc --db PATH --file REQUEST.json` to calculate quote metrics, history changes, and explicit close/roll scenarios, then log inputs/results for readback. See [formulas, units, logging and dry run](docs/calculations.md). Strategy decisions and current portfolio P&L are not inferred.

## Read-only session handoff

The existing `longarc-development` skill routes manual analysis to the maintained context and runbook. Consolidate new information into the existing owning file; create new files only when necessary. Start each manual account/chain analysis with the local `private/qqq-covered-call-plan/README.md` and [Schwab read-only runbook](docs/schwab-readonly.md). Offline strategy rules are maintained in the existing private context/policy; the runbook does not implement an automatic policy evaluator. Contract selection can begin with per-unit comparisons, while total exposure needs observed quantities. The runbook records source checks, logging conventions and paired exit-threshold research evidence, including sampling gaps and post-close quote-only tracking; the browser bridge automates bounded visible quote windows, but is not an unattended service or numerical policy. Calculation tooling was merged in [PR #14](https://github.com/small-thinking/longarc/pull/14); check the current checkout before invoking it.

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
