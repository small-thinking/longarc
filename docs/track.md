# LongArc Work Note

Record a short high-level entry after each meaningful increment: what capability changed, evidence/PR and merge status, remaining limits, and next step. Keep private account and market data out of this note. This is the single long-term progress log.

## Current work

Build the shared QQQ/IAU covered-call workflow around the migrated plan. The canonical work packages are in the local `private/qqq-covered-call-plan/PLAN.md`; private policy values are not duplicated here.

SQLite was selected and the local observation storage slice is implemented. The complete QQQ application remains pending. Existing OHLCV utilities are reuse candidates, not completed QQQ work packages. Earlier generic trading milestones are retired; their development history is preserved in Git.

## Change Log

### 2026-09-21 — Consistent covered-call candidate reports

- The skill routes every QQQ/IAU candidate report to a required field contract: spot/source timing, signed dollar/percent strike distance, bid/ask, Delta, premium, and provider Probability of Touch / Probability of OTM. Missing probabilities stay unknown and are not replaced by Delta or described as strategy win rates.
- Candidate preference, current action and missing entry requirements are reported separately. This is documentation guidance only; no collector, strategy threshold, schema, dependency or execution change.
- Validation: documentation diff, governance and skill validation pass. PR [#22](https://github.com/small-thinking/longarc/pull/22) open; not merged. Next: apply these fields to fresh observations while retaining source limitations.

### 2026-09-21 — Shared multi-symbol covered-call workflow

- Generalized bounded Schwab capture and selected-row import, advisory decisions, executions, costs and checkpoint replay to explicit stock/ETF CALL symbols, including QQQ and IAU. One skill/runbook selects the symbol and its own policy; source identity, roll legs and policy mismatches fail explicitly.
- History, replay revisions, estimates and paired comparisons stay separated by symbol. Execution reports provide both the account aggregate and symbol breakdowns. Legacy unscoped policy/replay/selected-row formats retain QQQ meaning; existing records are not rewritten.
- No database schema or dependency changes. No new trading strategy thresholds, automatic order execution or scheduler changes. Standard-looking CALL support does not establish adjusted-contract or other derivative support; sparse replay remains checkpoint simulation rather than empirical expected return.
- Validation: 259 Python tests, 9 Node bridge tests, Ruff, mypy, governance and skill validation pass. Synthetic QQQ/IAU/SPY cases cover symbol isolation, policy/roll conflicts, matching fills, CLI routing and legacy retries. Existing private IAU evidence was normalized and read back: 10 contract histories, idempotent retry, unchanged prior observation hashes and schema/holding tables. No fresh browser capture was performed for this increment. PR [#21](https://github.com/small-thinking/longarc/pull/21) open; not merged.

### 2026-09-20 — Shared-policy replay and adaptive estimates

- PR #19 merged; this increment reuses the existing advisory policy for observed-checkpoint shadow episodes, with bid/ask/slippage fills, fee-aware closes and separately accounted roll legs. No independent set of trading thresholds or actual executions.
- Added newest-revision reports, incomplete/open counts, entry-date cohort statistics, explicit monthly scenario scaling and matched two-policy comparisons. One complete cycle contributes; missing outcomes are not zero-filled. Code/policy/assumptions/source modes stay separate.
- No database schema changes: only append-only shadow calculation records and local reports. No scheduling or automatic policy tuning. Candidate choice/calendar/freshness attestations still require evidence; this is not a continuous-market or calendar-month portfolio backtest.
- Validation: 191 passing tests covering policy reuse, loss/profit/roll accounting, slippage, source gates, incomplete paths, immutable revisions, CLI/report and statistical edge cases; lint/types/governance pass. Local actual-data report correctly has no completed replay cycles. PR [#20](https://github.com/small-thinking/longarc/pull/20) open; not merged.


### 2026-09-20 — Unified selected-row observation ingestion

- Added explicit legacy observation/file adapters into the canonical options history stream, with evidence links, original clocks, preserved observe/shadow modes and idempotent retries. Original records are unchanged; selected coverage and unknown source fields remain explicit.
- Normalized percent-labelled provider OTM/touch estimates. Intervals with a verified closed-session endpoint are excluded from history change statistics while audit points remain visible.
- No database schema change or migration; historical conversion appends derived observations. Ad-hoc reports remain supported without scheduling. Trade lifecycle replay and calibrated monthly-income/loss estimates remain pending.
- Validation: 149 tests, lint/types/governance; synthetic fixtures plus local historical conversion/readback. PR pending; not merged.


### 2026-09-20 — Rule decisions and recorded execution results

- Added deterministic, versioned advisory screening with explicit evidence gates, HOLD/WATCH/exit/roll/entry outcomes and persisted reasons. Known risk exits outrank unknown unrelated inputs; no silent policy override or auto-trade.
- Added an idempotent actual-execution journal and matched partial-close/roll-episode realized P&L, fee allocation and remaining recorded quantities. Missing fees remain unknown; cumulative realized-only drawdown excludes open/stock risk.
- Reuses observations schema v2: no tables/columns/indexes or migration. Does not promote provisional holdings into fills, alter real account data, or activate a schedule.
- Validation: 144 tests including synthetic boundary, missing-data, fee, concurrent-close and CLI cases; lint/types/governance pass. PR pending. Remaining: broker reconciliation, corrections, open-position/portfolio valuation and validated empirical calibration.


### 2026-09-20 — Closed-session idempotency

- PR #16 merged and local main synchronized to `3d94447`.
- Added explicit verified-closed-session ingestion: identical content with new collection times returns the original snapshot; changed data/coverage and active-session samples remain separate. Original evidence/time are preserved. General journal conflict protection is unchanged.
- No schema changes or migration. No deletion of historical data and no automatic schedule.
- Validation: repeat-read, changed-price, session/mode isolation and invalid-date regression tests; full quality checks. Follow-up PR pending.


### 2026-09-20 — Candidate collection, sparse history and costs

- Added a bounded read-only Schwab browser bridge, partial capture ingestion, exact-contract histories and pooled DTE/Delta/interval descriptive statistics. Missing sessions/fields remain explicit; no interpolated prices or intraday crossing claims.
- Added dated fee assumptions and audited option-leg net scenarios, actual-fee overrides and spread/slippage handling. Unknown extra costs do not become zero.
- Schema remains v2 with no DDL or migration; new versioned payloads reuse observations. Existing records remain unchanged. Private captures stay excluded from Git.
- Validation: 114 Python tests, 4 Node bridge tests, lint/types and governance; browser smoke capture and database readback verified. PR pending; no merge or daily schedule activated.
- Remaining: unattended API/full-listed-chain access, exact exchange calendar, fill reconciliation and policy validation. Browser window coverage is not full-chain completeness.


### 2026-09-20 — Preference and read-only session handoff

- Consolidated confirmed preferences, conflicting historical parameter candidates and unresolved limits in the existing local private context entrypoint. Historical defaults were not promoted to approved policy.
- Clarified that single-contract comparisons do not require preset sizing; totals still require observed quantities. At the user’s request, initial offline entry/exit/roll rules now replace the earlier open-ended threshold discussion in the existing private context/policy. They are heuristic starting rules, not validated outcomes or an implemented policy evaluator. YAML readback and parameter ordering checks passed.
- Wired the existing project skill to the private context and runbook; recorded consolidation into existing files as the default. No new skill or duplicate context.
- Added threshold-comparison evidence conventions to the same runbook: observed crossings versus repeated samples, common episode windows, gaps and quote-only follow-up after closing. Existing storage is reused; no automatic comparison engine or recurring collector was added.
- Added a manual Schwab runbook for fresh account/chain reads, completeness and timestamp checks, exact-tool calculations, and source/analysis record readback. No new collector, decision engine, schema or runtime code.
- PR #14 is merged; the integrated calculation and skill workflow are validated together for PR #15. This turn completes preparation only: no new account read, recommendation, dry run, schedule or trade.
- Validation: 74 integrated tests, lint/types, documentation/link checks and governance passed. Next: a separate user-requested dry run using the context and runbook.

### 2026-09-20 — Calculation and audit dry run

- PR #12 and #13 are merged; new calculation work branches from main `6b446fd`. Added pure quote/time/moneyness/history metrics and explicit close/coverage/roll scenarios, with one JSON CLI operation to load, calculate and append an audit event.
- Every run preserves resolved inputs, IDs/hash, timestamps, source/quality, code fingerprint, metrics and missing/error reasons. Repeated requests are idempotent; current positions are never inferred from opening lots. Schema remains v2; optional underlying timestamp lives in snapshot JSON without changing existing retry hashes.
- Validation: 74 tests, lint and type checks pass. Isolated CLI dry run covered multiple opening lots, repeated snapshots, exact-ID history, synthetic arithmetic, idempotent retry, readback hashes and backup. The authorized existing Schwab tab supplied one quote-only dry run; absent quote/Greek times and multiplier remain unknown. No actual holdings imported; the main database's three business tables remain empty.
- Reviewed the old calculator's formulas; kept the explicit micro-unit input contract and unknown-value behavior. Strategy thresholds unchanged. Next: source freshness/units and policy contract, then manual fill reconciliation. No automatic observation schedule or trading readiness is claimed.
- This increment is [PR #14](https://github.com/small-thinking/longarc/pull/14), merged at `5e3e24c`.

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

Latest implementation evidence is recorded in the 2026-09-21 entry above. Earlier entries retain historical test counts. Browser data are unverified and incomplete; these checks validate the engineering workflow, not a trading strategy.

## 2026-09-22 — Explicit collection attempts and missing-data handling

- Added a skill-routed collection contract and offline checklist initializer/auditor.
- Reviews record attempted sources, missing reasons, canonical field review, held/watch
  coverage and dynamically selected two-/four-week expiries before reporting.
- Partial data remains usable; skipped checks stay visible and no policy checks are
  auto-passed. No database/schema changes or migration; historical records unchanged.
- Validation: targeted checklist regressions and existing repository checks (see PR).
- Limitation: caller evidence is not independently verified; this is not unattended
  browser recovery or a repaired browser collector. Published as a separate PR; no merge authorization.

- Pre-merge integration review: removed duplicate per-contract field-status inventory;
  reuse canonical importer coverage/warnings and the separate data-audit report.
  Removed duplicated recommendation-format requirements owned by the report-fields PR.
  The remaining script audits collection attempts only, without changing ingestion,
  analytics, policy, database or browser behavior.
- Integration validation after refinement: independent merge-tree checks with report-fields
  (#22) and analysis-reports (#23) passed without conflicts; the combined #23/#24
  tree passed 322 Python tests and 9 browser-bridge tests. No actual merge performed.
