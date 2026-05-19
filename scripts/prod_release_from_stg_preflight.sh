#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SAWA_ROOT="${SAWA_ROOT:-$(cd "$WORKSPACE_DIR/.." && pwd)}"
PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
RELEASE_BRANCH="${RELEASE_BRANCH:-release/prod-$(date +%Y%m%d)-stg-sync}"
BASE_REF="${BASE_REF:-HEAD}"
OUT_DIR="${OUT_DIR:-$WORKSPACE_DIR/tmp/prod_release_from_stg}"
CREATE_BRANCH="${CREATE_BRANCH:-0}"

mkdir -p "$OUT_DIR"

run_capture() {
  local name="$1"
  shift
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  } >"$OUT_DIR/$name.txt" 2>&1 || {
    printf 'command failed: %s\nsee %s\n' "$*" "$OUT_DIR/$name.txt" >&2
    return 1
  }
}

if [[ "$CREATE_BRANCH" == "1" ]]; then
  if git -C "$WORKSPACE_DIR" rev-parse --verify --quiet "$RELEASE_BRANCH" >/dev/null; then
    git -C "$WORKSPACE_DIR" switch "$RELEASE_BRANCH"
  else
    git -C "$WORKSPACE_DIR" switch -c "$RELEASE_BRANCH" "$BASE_REF"
  fi
fi

current_branch="$(git -C "$WORKSPACE_DIR" branch --show-current)"
if [[ -z "$current_branch" ]]; then
  printf 'detached HEAD is not a valid prod release source\n' >&2
  exit 1
fi

run_capture git-status git -C "$WORKSPACE_DIR" status --short
run_capture git-head git -C "$WORKSPACE_DIR" rev-parse HEAD
run_capture git-branch git -C "$WORKSPACE_DIR" branch --show-current
run_capture git-worktree-list git -C "$WORKSPACE_DIR" worktree list
find "$SAWA_ROOT" -maxdepth 3 -name .jj -type d -print >"$OUT_DIR/jj-repositories.raw.txt"
{
  printf '$ find %q -maxdepth 3 -name .jj -type d -print\n' "$SAWA_ROOT"
  cat "$OUT_DIR/jj-repositories.raw.txt"
} >"$OUT_DIR/jj-repositories.txt"

while IFS= read -r jj_dir; do
  [[ -z "$jj_dir" ]] && continue
  repo_dir="$(dirname "$jj_dir")"
  safe_name="$(printf '%s' "$repo_dir" | tr '/ ' '__')"
  run_capture "jj-status-${safe_name}" jj --repository "$repo_dir" status || true
done <"$OUT_DIR/jj-repositories.raw.txt"

for service in web-stg worker-stg ocr-pipeline-stg web-prod worker-prod ocr-pipeline-prod; do
  run_capture "cloudrun-${service}" \
    gcloud run services describe "$service" \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --format="yaml(metadata.name,status.url,status.traffic,status.latestReadyRevisionName,spec.template.spec.containers[].image,spec.template.spec.containers[].env,spec.template.metadata.annotations)"
done

cat >"$OUT_DIR/policy.md" <<EOF
# prod release from stg preflight

- release_branch: \`$RELEASE_BRANCH\`
- current_branch: \`$current_branch\`
- base_ref: \`$BASE_REF\`
- project_id: \`$PROJECT_ID\`
- region: \`$REGION\`

## Fixed policy

- Code source: stg is authoritative for the prod release branch.
- Orders: prod-only; do not sync from stg.
- Menus: prod-only; do not sync from stg.
- Facility data: compare stg/prod and resolve each diff explicitly.
- Deploy source: named release branch only, with fresh deploy copies.
EOF

printf '%s\n' "$OUT_DIR"
