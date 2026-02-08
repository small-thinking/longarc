#!/usr/bin/env bash
set -euo pipefail

required_files=(
  ".codex/skills/longarc-development/SKILL.md"
  ".github/pull_request_template.md"
  ".github/workflows/pr-metadata-autofill.yml"
  "docs/track.md"
  "scripts/ci/validate_pr_metadata.py"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "${file}" ]]; then
    echo "Missing required governance file: ${file}"
    exit 1
  fi
done

if ! grep -q "Always add corresponding test" ".codex/skills/longarc-development/SKILL.md"; then
  echo "Skill file must include the testing rule."
  exit 1
fi

if ! grep -q "Test Plan" ".github/pull_request_template.md"; then
  echo "PR template must include a Test Plan section."
  exit 1
fi

if ! grep -q "## Change Log" "docs/track.md"; then
  echo "Tracking document must include a Change Log section."
  exit 1
fi

echo "Governance validation passed."
