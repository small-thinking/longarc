---
name: longarc-development
description: Use for LongArc repository changes, local Schwab API setup and reads, or user-requested read-only covered-call analysis, including QQQ and IAU. Routes to the maintained runbook and preserves reviewable changes.
---

# LongArc Development Skill

Choose the mode matching the user's request. Read-only analysis does not require code changes, Git publishing, or a new skill.

For every analysis, initialize and complete the runbook's [required collection
checklist](../../../docs/schwab-readonly.md#required-collection-checklist), then run
`scripts/collection_checklist.py` before reporting. Missing data is supported;
distinguish source absence, access failure, not applicable and not attempted.
Use the current context for explicitly deferred checks; record them as
not_attempted with the reason rather than treating them as passed.
Recompute candidate expiry coverage each run, retain held/research obligations,
and persist attempt evidence alongside canonical quotes. A checklist is not a
policy pass: keep partial calculations and unresolved trading checks separate.
For QQQ decisions, load the current private policy JSON at
`private/qqq-covered-call-plan/decision-policy.json`; dated review policies are
historical evidence and must not replace the current rule. The policy defines
the active profit-exit mode and threshold; calculate gross capture from the
current ask and actual opening premium, with positive fee-adjusted P&L required.
If the QQQ policy enables the pace-only profit trigger, use the actual
current-leg STO execution time and the expiry date (16:00 New York planning
cutoff). Report gross premium capture and elapsed lifetime separately; an
unknown opening time makes the pace trigger unknown, not satisfied. The fixed
profit threshold belongs only to policies that select it; risk exits remain
independent and take priority.

## Local Schwab API setup and reads

For App registration, OAuth setup or requested API snapshots, follow
[local Python API access](../../../docs/schwab-readonly.md#local-python-api-access).
One Developer Portal App can include both Accounts and Trading Production and
Market Data Production; local Python does not require a mobile app. Use the exact
registered local callback and order limit 0 for the current read-only workflow.
Save credentials/tokens only in ignored private files, never chat or Git. Prefer `schwab auth-listen` with local TLS and verify its health check before
opening a fresh OAuth URL; the listener exchanges callbacks immediately and
stops after one completed callback. The user handles browser certificate warnings;
do not bypass them or change broader certificate trust automatically. The
review client and ordinary snapshot commands remain GET-only. Separate
[order commands](../../../docs/schwab-orders.md) default to local plans with no
OAuth or broker request. Implementing or testing this capability does not enable
trading: ordinary and scheduled reviews never invoke live preview, submission,
replacement or cancellation. Use the optional order procedure only for an
explicitly requested order workflow; its default-disabled profile, exact request
digest, fresh preflight and reconciliation gates remain separate from analysis.
Prefer API account/quote/chain reads for ordinary reviews. Use `schwab ingest-chain`
to map saved API chain/underlying snapshots into the canonical quote journal;
retain source=schwab_api, exact source clocks and missing fields. Never send API
JSON through the browser importer or treat positions/quotes as reconciled fills.
Run the existing arithmetic, history, decision and reporting tools on saved
records. When `options probabilities` is available, compute separately labeled
model estimates from explicit inputs; preserve provider probability fields.
See the runbook for source mapping, command availability and API pacing.
A rate-limit response ends the affected read; do not burst-retry or infer a
provider quota from the App order-limit setting.
Account consent is a separate authorization step; Ready For Use is not consent.
Authentication-only setup does not require the trading collection checklist.
When using snapshots for a covered-call recommendation, resume the analysis
procedure below, including policy, source freshness and missing-data checks.

## Read-only covered-call analysis

For a user-requested account/chain review or dry run, first read the existing private context at `private/qqq-covered-call-plan/README.md`, then follow [the Schwab runbook](../../../docs/schwab-readonly.md). For QQQ, IAU or a combined review, resolve each exact symbol and its own policy from that context, then reuse the [same procedure](../../../docs/schwab-readonly.md#multiple-symbol-reviews). Pass the symbol explicitly to capture/history/research and match policy `scope.underlying` to decision/replay inputs. Old unscoped policies belong only to QQQ; do not infer IAU thresholds from a discussion of candidates. Coverage, episodes and results stay separate by asset; shared account cash is reconciled once. Paths are relative to the repository root unless linked otherwise. Use the authorized API for current account/market facts and existing calculation tools for arithmetic. Browser reads are a fallback for explicit web comparisons or missing API-only evidence, not a prerequisite for ordinary API reviews. Keep preferences and parameter decisions in the private context/policy; keep browser procedures in the runbook. This routing does not authorize trades or schedule future runs.

Every candidate recommendation must follow the runbook's [required recommendation fields](../../../docs/schwab-readonly.md#required-recommendation-fields), including strike distance and provider touch/OTM probabilities where supplied; show model probabilities separately with their version and assumptions. Missing provider probabilities stay unknown. Separate candidate selection from the current action recommendation and unresolved entry checks.

For each entry or holding review, follow the runbook's [IV context procedure](../../../docs/schwab-readonly.md#iv-context-in-entry-and-holding-reviews). After current facts and policy checks, use `options iv-history` with explicit symbol, source and as-of cutoff when available. Resolve tool availability from the private context; an unavailable command or insufficient comparable history must be reported, never replaced with invented statistics. Use a heuristic weighting only when explicitly adopted in the private context, with its version, normalized component definitions and uncalibrated weights recorded. Missing components leave the total unknown; a common benchmark percentile cannot rank candidates within the same review. Monitor comparable short/long-window context for potential distribution shift; missing diagnostics stay unknown and do not prompt ad hoc reweighting. The score is not a calibrated trade probability or an override of coverage, liquidity, calendar, candidate-selection or exit rules. Present-close cost uses the current ask and known fills/fees; forecasting is required only for claims about future expected results, not for this calculation. Monetary scenarios are USD only. This procedure does not enable orders or new schedules.

## Consolidation

Update the existing file that owns new information. Prefer the private context/policy for personal decisions, the runbook for browser procedures, and `docs/track.md` for high-level progress. Create a new file only when existing files cannot reasonably serve the purpose; do not create a new context, handoff, skill, or progress log for each session. Replace superseded active guidance and retain only useful provenance or concise change history.

The development rules below apply to repository changes, not ordinary read-only analysis.

## Non-Negotiables

1. Always add corresponding test for behavior changes.
2. Keep each PR to the smallest coherent, reviewable change. Prefer concise, readable code; remove redundancy and avoid speculative abstractions or unrelated refactors. Split independent work to reduce human review effort.
   For database changes, list added/changed/dropped tables and the exact schema delta (columns, types, nullability, keys, constraints and indexes) in the PR, plus migration/data-compatibility impact. State explicitly when there is no schema change.
3. Update both `README.md` and `docs/track.md` after every repo change. Use `docs/track.md` as the single high-level work note: record each meaningful increment, validation/PR and merge status, remaining limits, and next step. Avoid duplicate progress logs or private account data.
4. Write doc updates from product perspective: capability/status, user impact, and current limits.
5. Default git flow: sync `main`, branch from updated `main`, then implement.
6. Default final step for each feature/request: open or update the PR.
7. PR title/body must be complete and match `.github/pull_request_template.md`.
8. If PR update is blocked (tooling/auth), report blocker and provide ready-to-paste PR title/body.

## Workflow

1. `git checkout main` and `git pull` (unless user asks otherwise).
2. Create/continue the target branch.
3. Read `docs/plan.md` and map work to current milestone.
4. Implement the smallest valid change.
5. Run relevant tests/checks.
6. Update `README.md` and `docs/track.md` (product perspective).
7. Open/update PR with complete metadata and test plan.
8. Verify PR metadata is present on GitHub; if blocked, report and provide exact content.
