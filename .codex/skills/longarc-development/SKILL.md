---
name: longarc-development
description: Use this skill for LongArc repository changes to enforce testing, clear change descriptions, and tracking document updates.
---

# LongArc Development Skill

Use this skill whenever making code, CI, or documentation updates in this repository.

## Required Rules

1. Always add corresponding test.
2. When updating the code, prepare a clear title, a human-readable description, and a concrete test plan.
3. Add or update the tracking document.

## Workflow

1. Read `docs/plan.md` and map the task to the current milestone.
2. Implement the smallest change set that satisfies the milestone scope.
3. Add or update tests, then run local checks.
4. Update `docs/track.md` with what changed and how it was verified.
5. Ensure PR notes follow `.github/pull_request_template.md`.
