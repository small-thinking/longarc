# LongArc

Local tooling for covered-call analysis and manual execution records across QQQ and IAU.

## Current scope

QQQ and IAU share one covered-call workflow with separate symbol-scoped policies: infrequent manual trades, frequent observation and deterministic calculations, and complete records. Start with `private/qqq-covered-call-plan/README.md` in the local investment workspace. Personal planning and policy files are excluded from Git and are not included in a fresh clone.

SQLite storage foundation is implemented: initialization, observation writes/reads, health checks and backup/restore through a JSON CLI. See [storage operations](docs/storage.md). Pure quote and explicit-quantity scenario calculations with audit logging are available. Browser-assisted candidate-chain capture, sparse history reports and dated fee estimates are available through `options`; see [the runbook](docs/schwab-readonly.md#repeatable-candidate-capture-and-history). Deterministic advisory rules and an evidence-backed manual execution journal are available; see [decision and result tracking](docs/calculations.md#deterministic-decisions-and-actual-results). Unattended API collection, scheduling, automatic broker reconciliation and alerts remain **unimplemented**.

The former generic trading framework has been removed: no backtest/paper/report placeholder commands, application trading configuration, preset capital/risk budgets, or broker execution credentials. Those old commands now fail argument parsing instead of returning success.

## Multiple underlyings, one workflow

Capture, rule evaluation, confirmed execution tracking, fee scenarios and replay
accept explicit uppercase underlying symbols. QQQ and IAU are supported by the
same standard short-CALL interface; another stock/ETF ticker does not require a
code branch, but still requires verified contract terms, coverage, source support
and its own policy. This does not add puts, index/cash-settled options or other
strategies.

Use `scope.underlying` in each policy and match it to the request's symbol. Old
unscoped policies belong only to QQQ; IAU cannot silently inherit them. Technical
support does not adopt new IAU thresholds. The skill selects the asset policy and
reuses the same [read-only procedure](docs/schwab-readonly.md#multiple-symbol-reviews).

`options history --symbol IAU` and `options estimate --symbol IAU` isolate evidence
and replay statistics (their legacy default is QQQ). `options performance` shows
account totals plus `by_symbol`; `--symbol IAU` limits results to IAU. Reports show
realized option P&L and remaining recorded contracts, not stock P&L or invented
open-option marks. See [symbol contracts and compatibility](docs/calculations.md#symbol-scope-and-compatibility).

## Read-only analysis reports

`options data-audit` inventories records, quote coverage, missing fields and
replay-data blockers. `options compare-candidates` compares one saved quote batch
against explicit policy thresholds and fee assumptions. `options position-report`
joins recorded open lots to quote histories, separates realized P&L from conditional
ask-close scenarios, and exposes stale or missing evidence. Realized totals are
recorded all-time subtotals, not reconciled account income. All three export JSON
and Markdown without changing the database. See [analysis reports](docs/analysis-reports.md)
for commands, selection rules, units and limits. These are on-demand reports, not
automatic candidate selection or validated return forecasts.

## Holding tracking

Multiple short-call opening lots can each retain repeated price/Greek snapshots without trading. See [holding tracking and schema](docs/holding-tracking.md). The original holding tables remain provisional. The separate execution journal matches recorded closes to openings and reports remaining recorded quantities; it still requires fresh broker reconciliation.

Repeated imports are idempotent. After verifying a closed market, `options ingest --closed-session YYYY-MM-DD` reuses an unchanged capture from that last market session, preserving its original collection time. Changed values or coverage still create a new record; active-market reads retain their separate times.

## Calculations

Use `uv run python -m longarc.cli calc --db PATH --file REQUEST.json` to calculate quote metrics, history changes, and explicit close/roll scenarios, then log inputs/results for readback. See [formulas, units, logging and dry run](docs/calculations.md). Strategy decisions and current portfolio P&L are not inferred.

## Read-only session handoff

The existing `longarc-development` skill routes manual analysis to the maintained context and runbook. Consolidate new information into the existing owning file; create new files only when necessary. Start each manual account/chain analysis with the local `private/qqq-covered-call-plan/README.md` and [Schwab read-only runbook](docs/schwab-readonly.md). Offline strategy rules are maintained in the existing private context/policy; the runbook does not implement an automatic policy evaluator. Contract selection can begin with per-unit comparisons, while total exposure needs observed quantities. The runbook records source checks, logging conventions and paired exit-threshold research evidence, including sampling gaps and post-close quote-only tracking; the browser bridge automates bounded visible quote windows, but is not an unattended service or numerical policy. Calculation tooling was merged in [PR #14](https://github.com/small-thinking/longarc/pull/14); check the current checkout before invoking it.

## Storage tools

Use `uv run python -m longarc.cli db --help`. Each operation requires `--db`; the local database is `private/longarc.sqlite3`. This is an append-only observation journal supporting separate evidence, calculation and execution records; it does not reconcile broker positions or authorize trades. No database server needs starting.

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

### Unified selected-quote history

`options ingest` accepts canonical browser captures with explicit `symbol` and
`manual-schwab-selected-rows-v2` files. Legacy `manual-schwab-selected-rows-v1` files
retain their QQQ identity. Use `--observation-id ID` instead of `--file` to normalize a saved
`manual-evidence-v1` or `manual-tenor-comparison-v1` QQQ observation. Imports append
standard history records linked to the original evidence, preserve collection time
and mode, and are safe to retry. Selected rows remain incomplete; missing source
timestamps, underlying data and contract multipliers remain unknown. Explicitly
percent-labelled OTM/touch probabilities are available as fractions in snapshots;
they are provider estimates, not calibrated realized probabilities.

History retains closed-session observations for audit but excludes intervals with
an explicitly verified closed-session endpoint from change statistics. Reports can be
requested ad hoc with `options history`; this does not enable scheduling, trade
replay, loss-probability estimation or expected monthly-income modeling.

### Shared-policy replay and updating estimates

`options replay` reads saved canonical quote evidence and runs the same policy as
`options decide`. It models explicit bid/ask fills, costs, risk/profit closes and
roll obligations without writing actual trades. `options estimate` reads the latest
episode revisions, reports partial/open samples separately, updates descriptive
net-P&L/loss statistics and supports paired policy comparisons. One completed cycle
is usable; uncertainty is reported as unknown when it cannot be estimated.

[Replay inputs and commands](docs/calculations.md#policy-replay-and-adaptive-descriptive-estimates)
explain required source checks and execution assumptions. Monthly scaling is an
explicit scenario, not an empirical income forecast; missing paths and assignment
cannot be reconstructed. All reports remain ad hoc, with no automatic trading or
policy changes.
