# LongArc Progress Tracking

## Purpose

Track milestone progress, quality controls, and verification history.

## Milestone Status

| Milestone | Status | Notes |
| --- | --- | --- |
| M0 | Completed | Python package scaffold, config/logging modules, CLI skeleton, and baseline tests added. |
| M1 | In progress | `uv`-based quality workflow and governance checks added; data layer implementation pending. |

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

- Updated `/Users/Yexi/source/longarc/.codex/skills/longarc-development/SKILL.md` to require pulling latest remote `main` and creating a fresh branch from updated `main` before development, unless the user explicitly asks for a different workflow.
- Updated `/Users/Yexi/source/longarc/.codex/skills/longarc-development/SKILL.md` to require creating/updating a PR for each new request or feature with mandatory PR title, description, and test plan content.
- Updated `/Users/Yexi/source/longarc/.codex/skills/longarc-development/SKILL.md` to enforce a PR metadata completion gate (must verify title/body are actually updated), plus explicit blocker handling with ready-to-paste PR content when tooling/auth is unavailable.
- Updated `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml` to install dependencies with `uv sync --extra dev` instead of `uv pip install`, to avoid environment resolution failures on GitHub runners.
- Added project metadata and tooling config at `/Users/Yexi/source/longarc/pyproject.toml`.
- Added package scaffold files under `/Users/Yexi/source/longarc/src/longarc/`.
- Implemented config loading/validation in `/Users/Yexi/source/longarc/src/longarc/core/config.py`.
- Implemented logging initialization in `/Users/Yexi/source/longarc/src/longarc/core/logging.py`.
- Implemented CLI skeleton and placeholders in `/Users/Yexi/source/longarc/src/longarc/cli.py`.
- Added config and env templates at `/Users/Yexi/source/longarc/config/config.example.yaml` and `/Users/Yexi/source/longarc/.env.example`.
- Added runner scripts `/Users/Yexi/source/longarc/scripts/run_backtest.sh` and `/Users/Yexi/source/longarc/scripts/run_paper.sh`.
- Added baseline smoke tests at `/Users/Yexi/source/longarc/tests/test_package_smoke.py`.
- Updated `/Users/Yexi/source/longarc/README.md` with a quick-start CLI command.
- Added parquet-backed data storage at `/Users/Yexi/source/longarc/src/longarc/data/store.py` with sorting, deduplication, and idempotent writes by timestamp.
- Added local parquet provider at `/Users/Yexi/source/longarc/src/longarc/data/providers/local_parquet.py` with deterministic synthetic bar generation for `1m`/`1h`/`1d`.
- Implemented `data download` and `data show-latest` CLI behaviors in `/Users/Yexi/source/longarc/src/longarc/cli.py`.
- Added data-layer tests at `/Users/Yexi/source/longarc/tests/test_data_store.py` and `/Users/Yexi/source/longarc/tests/test_cli_data.py`.
- Added `pyarrow` runtime dependency in `/Users/Yexi/source/longarc/pyproject.toml` and refreshed `/Users/Yexi/source/longarc/uv.lock`.

### Verification

- Not run (documentation/skill instruction update only).
- `bash scripts/ci/validate_governance.sh`
- `uv sync --extra dev`
- `uv run python -m longarc.cli --help`
- `uv run pytest`
- `uv run ruff check .`
- `uv run mypy`
- `uv run mypy src`
- `uv run ruff check .` (pass)
- `uv run mypy src` (pass)
- `uv run pytest` (pass, 8 tests)
