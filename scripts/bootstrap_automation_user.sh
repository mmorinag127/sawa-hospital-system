#!/usr/bin/env bash
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != true || "${GITHUB_REF:-}" != refs/heads/develop || "${1:-}" != stg ]]; then
  echo "Automation bootstrap requires the staging develop workflow" >&2
  exit 1
fi
case "${2:-shift}" in
  shift) bootstrap_script=portal_automation_db_bootstrap.py; bootstrap_email="$AUTOMATION_AUTH_EMAIL" ;;
  school-lunch) bootstrap_script=price_automation_db_bootstrap.py; bootstrap_email="$SCHOOL_LUNCH_AUTOMATION_AUTH_EMAIL" ;;
  *) echo "Unknown automation system" >&2; exit 1 ;;
esac
instance="$(gcloud run services describe "$WEB_SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --format=json | jq -r '.spec.template.metadata.annotations["run.googleapis.com/cloudsql-instances"] // empty')"
if [[ -z "$instance" ]]; then
  echo "Cloud SQL instance annotation is required" >&2
  exit 1
fi
proxy="$(mktemp)"
curl -fsSL -o "$proxy" https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.24.1/cloud-sql-proxy.linux.amd64
echo "fae2766aac9d614a2bdef2f2a7778f3d054f3acd5ff07a81a9e300bd471512eb  $proxy" | sha256sum -c -
chmod +x "$proxy"
"$proxy" --address 127.0.0.1 --port 5432 "$instance" &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null || true; wait "$proxy_pid" 2>/dev/null || true; rm -f "$proxy"' EXIT
ready=0
for _ in $(seq 1 30); do
  if (echo > /dev/tcp/127.0.0.1/5432) >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo "Cloud SQL proxy did not become ready" >&2; exit 1; }
cd backend
uv run --extra dev python "scripts/$bootstrap_script" \
  --environment stg --project-id "$PROJECT_ID" --region "$REGION" \
  --service "$WEB_SERVICE" --email "$bootstrap_email" \
  --db-host 127.0.0.1 --db-port 5432
