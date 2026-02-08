#!/usr/bin/env bash
set -euo pipefail

uv run python -m longarc.cli paper-sim run --config config/config.example.yaml "$@"
