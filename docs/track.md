# LongArc Progress Tracking

## Purpose

Track milestone progress, quality controls, and verification history.

## Milestone Status

| Milestone | Status | Notes |
| --- | --- | --- |
| M0 | Completed | Python package scaffold, config/logging modules, CLI skeleton, and baseline tests added. |
| M1 | In progress | `uv`-based quality workflow and governance checks added; provider abstraction + Polygon real-data ingestion path added. |

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
- Rewrote `/Users/Yexi/source/longarc/README.md` into a human-readable capabilities and usage guide, including current implemented scope vs planned features, setup steps, command examples, configuration notes, and developer workflow commands.
- Tightened `/Users/Yexi/source/longarc/README.md` to be more concise while keeping comprehensive coverage of status, capabilities, setup, configuration, and developer checks.
- Strengthened `/Users/Yexi/source/longarc/.codex/skills/longarc-development/SKILL.md` to require syncing latest remote `main` for every operation and to require ending any repository update with a PR that includes filled title, description, and test plan.
- Clarified `/Users/Yexi/source/longarc/.codex/skills/longarc-development/SKILL.md` so creating/updating a PR is explicitly the default last step for all feature development tasks.
- Added `/Users/Yexi/source/longarc/.github/workflows/pr-metadata-autofill.yml` to auto-populate missing PR metadata sections on PR open/edit/sync/reopen.
- Updated `/Users/Yexi/source/longarc/.github/workflows/pr-metadata-autofill.yml` to trigger on `pull_request` (same-repo PRs only) so autofill can run on active PRs before merge.
- Relaxed `/Users/Yexi/source/longarc/scripts/ci/validate_pr_metadata.py` to enforce non-empty PR title/Description/Test Plan presence (instead of placeholder-text rejection), so autofilled metadata can satisfy CI.
- Added PR metadata validator at `/Users/Yexi/source/longarc/scripts/ci/validate_pr_metadata.py` and wired it into `/Users/Yexi/source/longarc/.github/workflows/quality-gate.yml` as a pull-request check.
- Added tests for PR metadata validation behavior at `/Users/Yexi/source/longarc/tests/test_pr_metadata_validation.py`.
- Updated governance validation script at `/Users/Yexi/source/longarc/scripts/ci/validate_governance.sh` to require the PR metadata autofill workflow and validator script.
- Added provider contract and registry at `/Users/Yexi/source/longarc/src/longarc/data/providers/base.py` and `/Users/Yexi/source/longarc/src/longarc/data/providers/registry.py` to support pluggable data adapters.
- Added Polygon aggregates provider at `/Users/Yexi/source/longarc/src/longarc/data/providers/polygon.py` and wired `data download` provider selection into `/Users/Yexi/source/longarc/src/longarc/cli.py`.
- Updated `/Users/Yexi/source/longarc/src/longarc/data/providers/local_parquet.py` to provide a class-based adapter (`LocalParquetProvider`) alongside the existing function wrapper.
- Updated `/Users/Yexi/source/longarc/tests/test_cli_data.py` with a mocked Polygon CLI download test.
- Added `/Users/Yexi/source/longarc/tests/test_polygon_provider.py` to verify Polygon provider persistence behavior and registry validation.
- Updated `/Users/Yexi/source/longarc/README.md` to document implemented data capabilities and provider usage.
- Updated `/Users/Yexi/source/longarc/.env.example` to include `POLYGON_API_KEY`.

### Verification

- Not run (documentation/skill instruction update only).
- `bash scripts/ci/validate_governance.sh`
- `uv sync --extra dev`
- `uv run python -m longarc.cli --help`
- `uv run pytest`
- `uv run ruff check .`
- `uv run mypy`
- `uv run mypy src`
- `UV_CACHE_DIR=.uv-cache uv sync --extra dev`
- `UV_CACHE_DIR=.uv-cache uv run pytest`
- `UV_CACHE_DIR=.uv-cache uv run ruff check .`
- `UV_CACHE_DIR=.uv-cache uv run mypy src`
- `bash scripts/ci/validate_governance.sh`
- `UV_CACHE_DIR=.uv-cache uv run pytest`
- `UV_CACHE_DIR=.uv-cache uv run ruff check .`
- `UV_CACHE_DIR=.uv-cache uv run mypy src`
