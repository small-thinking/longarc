# LongArc Progress Tracking

## Purpose

Track milestone progress, quality controls, and verification history.

## Milestone Status

| Milestone | Status | Notes |
| --- | --- | --- |
| M0 | In progress | Python scaffold established with `src/` and `tests/`. |
| M1 | In progress | `uv`-based quality workflow and governance checks added. |

## Change Log

### 2026-02-07

- Added `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml`.
- Added governance validation script at `/Users/Yexi/source/longarc/scripts/ci/validate_governance.sh`.
- Added PR template at `/Users/Yexi/source/longarc/.github/pull_request_template.md`.
- Added project-level skill at `/Users/Yexi/source/longarc/.codex/skills/longarc-development/SKILL.md`.
- Updated PR template to use the GitHub PR title field (removed template title section).
- Renamed workflow to `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml` and generalized it for overall repo quality checks.
- Added Python version pin at `/Users/Yexi/source/longarc/.python-version`.
- Added `/Users/Yexi/source/longarc/pyproject.toml` with `uv`-based dev tooling configuration.
- Added lockfile `/Users/Yexi/source/longarc/uv.lock` for reproducible installs.
- Added starter Python package at `/Users/Yexi/source/longarc/src/longarc/__init__.py`.
- Added corresponding test at `/Users/Yexi/source/longarc/tests/test_package_smoke.py`.
- Updated `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml` to run Python checks via `uv`.

### 2026-02-08

- Updated `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml` to install dependencies with `uv sync --extra dev` instead of `uv pip install`, to avoid environment resolution failures on GitHub runners.

### Verification

- `bash scripts/ci/validate_governance.sh`
- `uv run ruff check .`
- `uv run mypy`
- `uv run pytest`
- `uv sync --extra dev`
