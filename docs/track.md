# LongArc Work Note

Record a short high-level entry after each meaningful increment: what capability changed, evidence/PR and merge status, remaining limits, and next step. Keep private account and market data out of this note. This is the single long-term progress log.

## Current work

Rebuild around the migrated QQQ plan. The canonical work packages are in the local `private/qqq-covered-call-plan/PLAN.md`; private policy values are not duplicated here.

SQLite was selected and the local observation storage slice is implemented. The complete QQQ application remains pending. Existing OHLCV utilities are reuse candidates, not completed QQQ work packages. Earlier generic trading milestones are retired; their development history is preserved in Git.

## Change Log

### 2026-09-20 — Calculation and audit dry run

- PR #12 and #13 are merged; new calculation work branches from main `6b446fd`. Added pure quote/time/moneyness/history metrics and explicit close/coverage/roll scenarios, with one JSON CLI operation to load, calculate and append an audit event.
- Every run preserves resolved inputs, IDs/hash, timestamps, source/quality, code fingerprint, metrics and missing/error reasons. Repeated requests are idempotent; current positions are never inferred from opening lots. Schema remains v2; optional underlying timestamp lives in snapshot JSON without changing existing retry hashes.
- Validation: 74 tests, lint and type checks pass. Isolated CLI dry run covered multiple opening lots, repeated snapshots, exact-ID history, synthetic arithmetic, idempotent retry, readback hashes and backup. The authorized existing Schwab tab supplied one quote-only dry run; absent quote/Greek times and multiplier remain unknown. No actual holdings imported; the main database's three business tables remain empty.
- Reviewed the old calculator's formulas; kept the explicit micro-unit input contract and unknown-value behavior. Strategy thresholds unchanged. Next: source freshness/units and policy contract, then manual fill reconciliation. No automatic observation schedule or trading readiness is claimed.
- This increment is [PR #14](https://github.com/small-thinking/longarc/pull/14), open for review and not merged.

### 2026-09-20 — Multiple opening lots and quote history

- Added migration 2: holding_lots and holding_snapshots, linking repeated checks to distinct short-call opening lots, including same-contract openings. Added narrow JSON CLI read/write tools.
- Mapped the supplied option-chain fields and missing evidence; no screenshot data or actual account holdings imported. Opening records remain provisional and do not establish current remaining positions.
- Calculations and strategy thresholds unchanged. Next: one pure midpoint/spread function and a thin read/calculate/record adapter; execution lifecycle and complete coverage remain later work.
- Validation: 49 tests passed; lint/types/governance passed. Local database backed up and migrated from v1 to v2; all business tables remain empty. Tests cover migration/data preservation, multi-lot isolation, repeated snapshots/idempotency, input validation, CLI and backup/restore. The separate work-note PR #12 is merged; this implementation is awaiting merge as PR #13.


### 2026-09-20 — Storage merged; clarify transaction tracking

- PR #10 (legacy cleanup) and PR #11 (SQLite foundation) are merged. Local main synchronized to `0d5daec` after #11.
- Available: initialize, append/read observations, idempotent retries, corrections, health checks and backup/restore. Evidence: 34 tests and local isolated synthetic backup/restore readback; these are the #11 results, not a new test run.
- Current schema has two tables: migration bookkeeping and generic observations. It can retain repeated measurements, but has no enforced trade/episode relationship, typed quote/Greek contract, or fills ledger yet. A scope string is not a substitute for that model.
- Next small implementation: define a stable episode identity and link repeated observations to it, with explicit parameter units, timestamps and unknown-value handling. Exact DDL will be proposed in that PR; no schema changes in this documentation update.
- Then add one independently tested calculation at a time behind a thin read/calculate/record operation. Calculation functions should not perform database I/O. Live collection, scheduling and verified transaction accounting remain later work.


### 2026-09-20 — Review conventions

- Recorded minimal, readable PR scope in the project development skill and README. Added schema disclosure to the PR template and exact migration-1 SQL to PR #11. Documentation only; no application or database changes.


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

Latest implementation evidence: 74 passing tests, lint/types/governance, and isolated synthetic plus browser-quote calculation/readback/retry/backup checks. Browser data are unverified and incomplete; this validates the engineering loop, not a trading strategy. Earlier entries retain historical test counts.
