---
name: longarc-development
description: Use for LongArc repository changes or user-requested Schwab read-only covered-call analysis, including QQQ and IAU. Routes analysis to the local context and runbook; enforces reviewable repository changes.
---

# LongArc Development Skill

Choose the mode matching the user's request. Read-only analysis does not require code changes, Git publishing, or a new skill.

## Read-only covered-call analysis

For a user-requested account/chain review or dry run, first read the existing private context at `private/qqq-covered-call-plan/README.md`, then follow [the Schwab runbook](../../../docs/schwab-readonly.md). For QQQ, IAU or a combined review, resolve each exact symbol and its own policy from that context, then reuse the [same procedure](../../../docs/schwab-readonly.md#multiple-symbol-reviews). Pass the symbol explicitly to capture/history/research and match policy `scope.underlying` to decision/replay inputs. Old unscoped policies belong only to QQQ; do not infer IAU thresholds from a discussion of candidates. Coverage, episodes and results stay separate by asset; shared account cash is reconciled once. Paths are relative to the repository root unless linked otherwise. Use available browser tools for current page state and existing calculation tools for arithmetic. Keep preferences and parameter decisions in the private context/policy; keep browser procedures in the runbook. This routing does not authorize trades or schedule future runs.

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
