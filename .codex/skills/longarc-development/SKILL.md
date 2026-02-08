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
4. Unless the user explicitly specifies a different workflow, always sync from remote `main` first and create a new branch from that updated `main` before continuing development.

## Workflow

1. Sync local state from remote `main` (for example: `git checkout main`, `git pull origin main`).
2. Create a fresh working branch from the updated `main` (unless the user asked to stay on an existing branch).
3. Read `docs/plan.md` and map the task to the current milestone.
4. Implement the smallest change set that satisfies the milestone scope.
5. Add or update tests, then run local checks.
6. Update `docs/track.md` with what changed and how it was verified.
7. Ensure PR notes follow `.github/pull_request_template.md`.
