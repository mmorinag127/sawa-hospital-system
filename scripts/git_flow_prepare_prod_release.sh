#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PROD_BRANCH="${PROD_BRANCH:-master}"
DEVELOP_BRANCH="${DEVELOP_BRANCH:-develop}"
RELEASE_DATE="${RELEASE_DATE:-$(date +%Y%m%d)}"
RELEASE_BRANCH="${RELEASE_BRANCH:-release/prod-${RELEASE_DATE}}"

current_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" == "HEAD" ]]; then
  echo "blocked: current worktree is detached; checkout a named branch first" >&2
  exit 1
fi

if [[ -n "$(git -C "$REPO_ROOT" status --short)" ]]; then
  echo "blocked: worktree has uncommitted changes" >&2
  git -C "$REPO_ROOT" status --short >&2
  exit 1
fi

for branch in "$PROD_BRANCH" "$DEVELOP_BRANCH"; do
  if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "$branch" >/dev/null; then
    echo "blocked: required branch does not exist: $branch" >&2
    exit 1
  fi
done

if git -C "$REPO_ROOT" rev-parse --verify --quiet "$RELEASE_BRANCH" >/dev/null; then
  echo "exists: $RELEASE_BRANCH"
else
  git -C "$REPO_ROOT" branch "$RELEASE_BRANCH" "$DEVELOP_BRANCH"
  echo "created: $RELEASE_BRANCH from $DEVELOP_BRANCH"
fi

merge_base="$(git -C "$REPO_ROOT" merge-base "$PROD_BRANCH" "$RELEASE_BRANCH")"
prod_head="$(git -C "$REPO_ROOT" rev-parse "$PROD_BRANCH")"
if [[ "$merge_base" != "$prod_head" ]]; then
  echo "warning: $PROD_BRANCH is not an ancestor of $RELEASE_BRANCH" >&2
  echo "warning: inspect this before prod deploy; prod history may contain changes absent from release branch" >&2
fi

echo "prod_branch=$PROD_BRANCH"
echo "develop_branch=$DEVELOP_BRANCH"
echo "release_branch=$RELEASE_BRANCH"
echo "release_head=$(git -C "$REPO_ROOT" rev-parse "$RELEASE_BRANCH")"
