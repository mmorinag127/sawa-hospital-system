#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SAWA_ROOT="${SAWA_ROOT:-$(cd "$REPO_ROOT/../.." && pwd)}"
PROD_BRANCH="${PROD_BRANCH:-master}"
DEVELOP_BRANCH="${DEVELOP_BRANCH:-develop}"
RELEASE_BRANCH="${RELEASE_BRANCH:-}"
TARGET_ENV="${TARGET_ENV:-prod}"
PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
WORKER_SERVICE="${WORKER_SERVICE:-worker-${TARGET_ENV}}"
WEB_SERVICE="${WEB_SERVICE:-web-${TARGET_ENV}}"
STRICT="${STRICT:-1}"

critical_paths=(
  "backend/src/services/hakodate_fixed_quad_registration_service.py"
  "backend/src/services/order_service.py"
  "backend/src/services/order_workflow_v2_service.py"
  "backend/src/services/uploaded_pdf_service.py"
  "backend/src/services/apply_gate_service.py"
  "backend/src/api/orders.py"
  "frontend/src/pages/orders/[id].tsx"
  "frontend/src/pages/orders/[id]/workflow-v2.tsx"
  "frontend/src/pages/orders/[id]/inspection-v2.tsx"
  "frontend/src/pages/orders/index.tsx"
  "scripts"
  "Taskfile.yml"
  "AGENTS.md"
)

failures=0

fail() {
  echo "blocked: $*" >&2
  failures=$((failures + 1))
}

section() {
  printf '\n== %s ==\n' "$1"
}

section "repository"
echo "repo=$REPO_ROOT"
current_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
echo "current_branch=$current_branch"
if [[ "$current_branch" == "HEAD" ]]; then
  fail "detached HEAD is not deployable"
fi
if [[ "$TARGET_ENV" == "prod" ]]; then
  if [[ -n "$RELEASE_BRANCH" && "$current_branch" != "$RELEASE_BRANCH" ]]; then
    fail "prod preflight must run from $RELEASE_BRANCH, not $current_branch"
  elif [[ -z "$RELEASE_BRANCH" && "$current_branch" != release/prod-* ]]; then
    fail "prod preflight must run from release/prod-*"
  fi
fi

status_output="$(git -C "$REPO_ROOT" status --short)"
if [[ -n "$status_output" ]]; then
  echo "$status_output"
  fail "worktree has uncommitted changes"
else
  echo "worktree=clean"
fi

for branch in "$PROD_BRANCH" "$DEVELOP_BRANCH"; do
  if git -C "$REPO_ROOT" rev-parse --verify --quiet "$branch" >/dev/null; then
    echo "$branch=$(git -C "$REPO_ROOT" rev-parse "$branch")"
  else
    fail "required branch missing: $branch"
  fi
done

if [[ -n "$RELEASE_BRANCH" ]]; then
  if git -C "$REPO_ROOT" rev-parse --verify --quiet "$RELEASE_BRANCH" >/dev/null; then
    echo "$RELEASE_BRANCH=$(git -C "$REPO_ROOT" rev-parse "$RELEASE_BRANCH")"
  else
    fail "release branch missing: $RELEASE_BRANCH"
  fi
fi

section "worktrees"
git -C "$REPO_ROOT" worktree list

section "jj"
jj_dirs="$(find "$SAWA_ROOT" -maxdepth 3 -name .jj -type d -print || true)"
if [[ -z "$jj_dirs" ]]; then
  echo "jj_repos=none"
else
  while IFS= read -r jj_dir; do
    repo_dir="$(dirname "$jj_dir")"
    echo "-- $repo_dir"
    if command -v jj >/dev/null 2>&1; then
      jj_status="$(jj --repository "$repo_dir" status 2>&1 || true)"
      echo "$jj_status"
      if [[ "$jj_status" != *"The working copy is clean"* && "$jj_status" != *"Nothing changed."* ]]; then
        fail "jj working copy has changes: $repo_dir"
      fi
      if [[ "$jj_status" == *"no description set"* || "$jj_status" == *"JJ_EMPTY_STRING"* ]]; then
        fail "jj working copy has empty description: $repo_dir"
      fi
    else
      echo "jj command not found; skipping status"
      fail "jj command is required for release preflight"
    fi
  done <<< "$jj_dirs"
fi

section "sibling commits touching critical paths"
sibling_commits="$(git -C "$REPO_ROOT" log --all --not HEAD --format='%H %s' -- "${critical_paths[@]}" || true)"
if [[ -z "$sibling_commits" ]]; then
  echo "sibling_commits=none"
else
  echo "$sibling_commits"
  if [[ "$STRICT" == "1" ]]; then
    fail "critical-path sibling commits exist outside HEAD; inspect/merge or document as unrelated"
  fi
fi

section "cloud run current revisions"
if command -v gcloud >/dev/null 2>&1; then
  for service in "$WORKER_SERVICE" "$WEB_SERVICE"; do
    revision="$(gcloud run services describe "$service" --project="$PROJECT_ID" --region="$REGION" --format='value(status.latestReadyRevisionName)' 2>/dev/null || true)"
    if [[ -z "$revision" ]]; then
      echo "$service=unavailable"
      continue
    fi
    image="$(gcloud run revisions describe "$revision" --project="$PROJECT_ID" --region="$REGION" --format='value(spec.containers[0].image)' 2>/dev/null || true)"
    echo "$service revision=$revision image=$image"
  done
else
  echo "gcloud not found; skipping Cloud Run revision check"
fi

section "result"
if [[ "$failures" -gt 0 ]]; then
  echo "preflight=blocked failures=$failures"
  exit 1
fi

echo "preflight=pass"
