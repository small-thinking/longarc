# LongArc Progress Tracking

## Current work

Rebuild around the migrated QQQ plan. The canonical work packages are in the local `private/qqq-covered-call-plan/PLAN.md`; private policy values are not duplicated here.

SQLite was selected and the local observation storage slice is implemented. The complete QQQ application remains pending. Existing OHLCV utilities are reuse candidates, not completed QQQ work packages. Earlier generic trading milestones are retired; their development history is preserved in Git.

## Change Log

### 2026-09-20 — SQLite foundation

- Merged cleanup PR #10, then implemented W02's first storage slice on a new branch.
- Added transactional schema initialization, append-only observations, explicit provenance and idempotency, bounded reads, health checks and consistent backup/restore to new paths.
- Added structured `db` CLI tools for Codex; no SQL write escape hatch or trading execution.
- Full policy, money/contract domain validation, financial calculation, ingestion, fills, replay, scheduling and alerts remain pending; W00/W02 are not wholly complete.
- Verification: 34 tests passed, plus lint, type checks and governance. Local SQLite 3.47.1 database initialized empty; separate synthetic database verified write/read, retry, backup and restore with identical readback. Evidence stays in private deployment files.


### 2026-09-20 — Clear the old trading framework

- Replaced the generic SMA/momentum/multi-asset roadmap with the QQQ scope and iteration-plan entry point.
- Reduced personal investment material to five local active files. Historical originals were verified and moved to an archive outside this repository.
- Removed obsolete application configuration, preset portfolio/risk amounts, backtest/paper/report placeholders, runner scripts, broker credential templates and unused configuration dependencies.
- Required explicit provider selection so a market-data command cannot silently default to synthetic data.
- Preserved tested OHLCV storage/providers, logging, package tooling and CI. Database and policy contracts will be implemented from the new plan.
- Verified the imported AGENTS.md is outside the project in the archive; no imported instruction file is active in the current workspace. Global and existing repository Codex configuration remain unchanged.

## Verification

- Regression tests reject retired commands and downloads without an explicit provider.
- Existing synthetic-data, mocked Polygon, Parquet persistence and PR metadata tests retained.
- `uv run --no-sync pytest`: 17 passed. Ruff, mypy, governance checks and `git diff --check` passed. CLI help exposes only the retained data commands.
