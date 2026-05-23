#!/usr/bin/env bash
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" == "true" && -n "${GITHUB_RUN_ID:-}" ]]; then
  exit 0
fi

cat >&2 <<'EOF'
blocked: local service-account-key deploy authentication is forbidden.
Use the GitHub Actions deploy workflows with Workload Identity Federation.
EOF
exit 1
