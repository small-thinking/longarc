#!/usr/bin/env bash
set -euo pipefail

uv run python -m longarc.cli backtest --config config/config.example.yaml "$@"
