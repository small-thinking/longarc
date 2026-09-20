# LongArc Progress Tracking

## Current work

Rebuild around the migrated QQQ plan. The canonical work packages are in the local `private/qqq-covered-call-plan/PLAN.md`; private policy values are not duplicated here.

Database selection/setup and the QQQ application remain pending. Existing OHLCV utilities are reuse candidates, not completed QQQ work packages. Earlier generic trading milestones are retired; their development history is preserved in Git.

## Change Log

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
