#!/usr/bin/env bash
set -euo pipefail

# Preserve the entry point, not its independent and unrecorded deployment path.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/require_ci_cd_deploy.sh" web-prod
test "${CONFIRM:-}" = DEPLOY_PROD_MAINTENANCE
python3 "$SCRIPT_DIR/production_maintenance.py" show --image "${IMAGE:?immutable maintenance image required}"
