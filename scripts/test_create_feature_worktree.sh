#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ROOT="$(mktemp -d)"
REPO_DIR="${TEST_ROOT}/repo"
WORKTREE_ROOT="${TEST_ROOT}/worktrees"

cleanup() {
  chmod -R u+w "$TEST_ROOT" 2>/dev/null || true
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

mkdir -p "$REPO_DIR"
git -C "$REPO_DIR" init -q
git -C "$REPO_DIR" config user.email "codex@example.invalid"
git -C "$REPO_DIR" config user.name "Codex Test"
printf 'hello\n' > "${REPO_DIR}/README.md"
git -C "$REPO_DIR" add README.md
git -C "$REPO_DIR" commit -q -m "init"

base_ref="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"

output="$(
  REPO_ROOT="$REPO_DIR" \
  WORKTREE_ROOT="$WORKTREE_ROOT" \
  BRANCH_PREFIX="codex" \
  BASE_REF="$base_ref" \
  "$SCRIPT_DIR/create_feature_worktree.sh" "Shipping History Grid"
)"

branch_name="$(printf '%s\n' "$output" | awk -F= '/^branch=/{print $2}')"
worktree_path="$(printf '%s\n' "$output" | awk -F= '/^path=/{print $2}')"

test "$branch_name" = "codex/shipping-history-grid"
test -d "$worktree_path"
test -f "${worktree_path}/README.md"
git -C "$REPO_DIR" rev-parse --verify --quiet "$branch_name" >/dev/null

echo "ok: create_feature_worktree"
