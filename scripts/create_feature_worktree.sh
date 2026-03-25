#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
WORKTREE_ROOT="${WORKTREE_ROOT:-$(cd "$REPO_ROOT/.." && pwd)/worktrees}"
BRANCH_PREFIX="${BRANCH_PREFIX:-codex}"
BASE_REF="${BASE_REF:-$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)}"
FEATURE_NAME="${1:-${FEATURE:-}}"

if [ -z "$FEATURE_NAME" ]; then
  echo "usage: $0 <feature-name>"
  echo "example: $0 shipping-history-grid"
  exit 1
fi

slug="$(
  printf '%s' "$FEATURE_NAME" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's#[^a-z0-9._/-]+#-#g; s#-+#-#g; s#(^[-./]+|[-./]+$)##g'
)"

if [ -z "$slug" ]; then
  echo "feature name is empty after slug normalization"
  exit 1
fi

branch_name="${BRANCH_PREFIX}/${slug}"
worktree_path="${WORKTREE_ROOT}/${slug}"

mkdir -p "$WORKTREE_ROOT"

if [ -e "$worktree_path" ]; then
  echo "worktree path already exists: $worktree_path"
  exit 1
fi

if git -C "$REPO_ROOT" rev-parse --verify --quiet "$branch_name" >/dev/null; then
  git -C "$REPO_ROOT" worktree add "$worktree_path" "$branch_name"
else
  git -C "$REPO_ROOT" worktree add -b "$branch_name" "$worktree_path" "$BASE_REF"
fi

printf 'branch=%s\npath=%s\nbase=%s\n' "$branch_name" "$worktree_path" "$BASE_REF"
