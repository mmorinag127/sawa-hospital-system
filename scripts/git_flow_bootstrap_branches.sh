#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PROD_BRANCH="${PROD_BRANCH:-master}"
DEVELOP_BRANCH="${DEVELOP_BRANCH:-develop}"
RELEASE_DATE="${RELEASE_DATE:-$(date +%Y%m%d)}"
RELEASE_BRANCH="${RELEASE_BRANCH:-release/prod-${RELEASE_DATE}}"
BASE_REF="${BASE_REF:-HEAD}"
CREATE_RELEASE_BRANCH="${CREATE_RELEASE_BRANCH:-0}"

current_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" == "HEAD" ]]; then
  echo "blocked: current worktree is detached; checkout a named branch first" >&2
  exit 1
fi

if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "$PROD_BRANCH" >/dev/null; then
  echo "blocked: prod branch does not exist: $PROD_BRANCH" >&2
  exit 1
fi

if git -C "$REPO_ROOT" rev-parse --verify --quiet "$DEVELOP_BRANCH" >/dev/null; then
  echo "exists: $DEVELOP_BRANCH"
else
  git -C "$REPO_ROOT" branch "$DEVELOP_BRANCH" "$BASE_REF"
  echo "created: $DEVELOP_BRANCH from $BASE_REF"
fi

if [[ "$CREATE_RELEASE_BRANCH" == "1" ]]; then
  if git -C "$REPO_ROOT" rev-parse --verify --quiet "$RELEASE_BRANCH" >/dev/null; then
    echo "exists: $RELEASE_BRANCH"
  else
    git -C "$REPO_ROOT" branch "$RELEASE_BRANCH" "$DEVELOP_BRANCH"
    echo "created: $RELEASE_BRANCH from $DEVELOP_BRANCH"
  fi
fi

echo "prod_branch=$PROD_BRANCH"
echo "develop_branch=$DEVELOP_BRANCH"
echo "release_branch=$RELEASE_BRANCH"
