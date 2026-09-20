---
name: longarc-development
description: Use this skill for LongArc repository changes to enforce testing, clear change descriptions, and tracking document updates.
---

# LongArc Development Skill

Use this skill for any code, CI, or documentation change in this repository.

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
