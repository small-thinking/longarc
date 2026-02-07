# LongArc Progress Tracking

## Purpose

Track milestone progress, quality controls, and verification history.

## Milestone Status

| Milestone | Status | Notes |
| --- | --- | --- |
| M0 | In progress | Project scaffolding not started yet. |
| M1 | In progress | Baseline quality workflow and governance checks added. |

## Change Log

### 2026-02-07

- Added `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml`.
- Added governance validation script at `/Users/Yexi/source/longarc/scripts/ci/validate_governance.sh`.
- Added PR template at `/Users/Yexi/source/longarc/.github/pull_request_template.md`.
- Added project-level skill at `/Users/Yexi/source/longarc/.codex/skills/longarc-development/SKILL.md`.
- Updated PR template to use the GitHub PR title field (removed template title section).
- Renamed workflow to `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml` and generalized it for overall repo quality checks.

### Verification

- `bash scripts/ci/validate_governance.sh`
