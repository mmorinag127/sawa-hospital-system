# Git Flow Operations

## Purpose
- Keep local, stg, and prod release sources explicit.
- Prevent deploying a branch that is missing fixes already verified locally or on stg.
- Make prod promotion repeatable without relying on memory, old worktrees, or stale deploy copies.

## Branch Model
- `master`: production history. Prod deploys must be represented here after release completion. This repo already uses `master`; do not introduce `main` unless the repo is renamed deliberately.
- `develop`: integration branch for completed work that is allowed to reach stg.
- `feature/<name>` or `codex/<name>`: isolated work branch. Use a separate worktree for non-trivial work.
- `release/prod-YYYYMMDD`: prod release candidate cut from `develop` after stg validation.
- `hotfix/prod-YYYYMMDD-<name>`: urgent prod fix cut from `master`, then merged back into `develop`.

## Environment Mapping
- Local: any feature/codex branch or worktree.
- Stg: `develop` or an explicit `release/*` branch only.
- Prod: `release/prod-*` only. After prod verification, merge the release branch into `master` and back into `develop`.

## Hard Gates
- Do not deploy from detached HEAD.
- Do not deploy directly from a feature/codex branch to prod.
- Do not deploy from `master` to stg unless intentionally validating current prod.
- Do not use `git status --short` alone as a release signal.
- Do not reuse an old deploy copy.
- Do not run stg cleanup/reset procedures against prod.
- Do not continue if facility template resolution differs between stg and prod and the canonical source has not been chosen.

## Standard Flow
1. Create or use a feature/codex worktree.
2. Implement and commit.
3. Merge the completed branch into `develop`.
4. Deploy stg from `develop`.
5. Validate exact live stg surfaces.
6. Cut `release/prod-YYYYMMDD` from `develop`.
7. Run prod release preflight on the release branch.
8. Build once from the release branch.
9. Deploy/promote prod from that release artifact.
10. Verify exact live prod surfaces.
11. Merge `release/prod-YYYYMMDD` into `master`.
12. Merge `master` back into `develop`.

## Deploy Instruction Contract
- When the operator says `deploy`, do not deploy directly from a feature/codex branch and leave it unresolved.
- First commit the requested changes, then merge the completed feature/codex branch into the target release source branch.
- For stg, the target release source branch is `develop` unless the operator explicitly names a `release/*` branch.
- For prod, the target release source branch is `release/prod-YYYYMMDD`.
- Deploy from the target release source branch after the merge, using a fresh deploy copy.
- After deploy, record the deployed Cloud Run revision/image and confirm the deployed source branch contains the merged commit.
- A feature/codex branch is not considered resolved until its commits are ancestors of the target release source branch.
- Do not delete a feature/codex branch until the deployed release source branch has passed postdeploy checks.

## Required Commands

Check current release-source state:

```bash
task git_flow_status
```

Create missing operational branches:

```bash
task git_flow_bootstrap
```

Create a prod release branch from `develop`:

```bash
task prod_release_prepare RELEASE_DATE=20260504
```

Create a dedicated worktree for the prod release branch:

```bash
task prod_release_worktree RELEASE_DATE=20260504
```

Run prod preflight from the release branch:

```bash
task prod_release_preflight
```

## Prod Release Data Handling
- Prod existing orders, PDFs, OCR evidence, sheets, confirmed snapshots, archives, facility configs, and menu data must be preserved.
- Before prod deploy:
  - record current Cloud Run worker/web revisions and images.
  - record current prod order/PDF/DB counts.
  - confirm latest Cloud SQL backup, or create an on-demand backup.
  - record raw bucket object count and footprint.
  - export or snapshot any order PDFs that will be used for smoke tests.
- New additive tables are allowed only when startup/schema creation is known to be safe and existing read paths remain compatible.
- Destructive scripts must require explicit operator confirmation and must not be part of normal prod deploy.

## Verification Matrix
- Branch:
  - named branch, not detached.
  - `develop`, `master`, and release branch exist.
  - relevant sibling commits are either ancestors or documented unrelated.
- JJ:
  - no production-code changes remain only in a jj working copy.
  - no empty-description jj working copy contains production-code changes.
- Code:
  - backend tests for touched services pass.
  - frontend build or targeted UI validation passes when frontend changed.
- Stg:
  - exact order flow verified through the same UI/API surface the operator uses.
  - facility template resolution is correct for the target facility and at least one neighboring facility.
- Prod:
  - predeploy checks pass.
  - deploy uses fresh source/artifact.
  - existing prod order still opens.
  - new upload/OCR workflow can run without corrupting existing data.

## Failure Handling
- If prod release preflight fails, do not deploy.
- If stg/prod facility config drift is detected, stop and choose the canonical source per facility.
- If a fix is found in another worktree or jj working copy, integrate it into `develop` before cutting a release.
- If a live surface differs from the API evidence, the release is not complete.
