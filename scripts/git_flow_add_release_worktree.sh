#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
WORKTREE_ROOT="${WORKTREE_ROOT:-$(cd "$REPO_ROOT/.." && pwd)/worktrees}"
RELEASE_DATE="${RELEASE_DATE:-$(date +%Y%m%d)}"
RELEASE_BRANCH="${RELEASE_BRANCH:-release/prod-${RELEASE_DATE}}"
WORKTREE_NAME="${WORKTREE_NAME:-prod-release-${RELEASE_DATE}}"
WORKTREE_PATH="${WORKTREE_PATH:-${WORKTREE_ROOT}/${WORKTREE_NAME}}"

if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "$RELEASE_BRANCH" >/dev/null; then
  echo "blocked: release branch does not exist: $RELEASE_BRANCH" >&2
  exit 1
fi

mkdir -p "$WORKTREE_ROOT"

if [[ -e "$WORKTREE_PATH" ]]; then
  echo "blocked: worktree path already exists: $WORKTREE_PATH" >&2
  exit 1
fi

git -C "$REPO_ROOT" worktree add "$WORKTREE_PATH" "$RELEASE_BRANCH"

echo "release_branch=$RELEASE_BRANCH"
echo "worktree_path=$WORKTREE_PATH"
