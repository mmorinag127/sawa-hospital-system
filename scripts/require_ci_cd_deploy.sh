#!/usr/bin/env bash
set -euo pipefail

target="${1:-deploy}"

if [[ "${GITHUB_ACTIONS:-}" == "true" && -n "${GITHUB_RUN_ID:-}" ]]; then
  exit 0
fi

cat >&2 <<EOF
blocked: ${target} must be run by CI/CD.
Local Cloud Run deploys are forbidden. Push the release branch and run the GitHub Actions deploy workflow instead.
EOF
exit 1
