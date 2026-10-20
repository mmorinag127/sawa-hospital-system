# Workflow demand-read verification

## Scope

- Base develop: `5e30eefee4f5daddc0f97b6c29cbd1ddc5172496`.
- Branch: `codex/hospital-workflow-demand-read-20261020`.
- Reduce initial history detail reads, overlapping OCR progress polls, duplicate
  workflow projection, and closed raw JSON serialization.
- Preserve recognized values, quantities, warnings, candidate filtering,
  confirmation gates, operator edits, authorization, and response ownership.
- No production changes, recognition shortcuts, cross-request result cache,
  skipped validation, or business-data writes.
- Completion requires integration into develop, Actions staging deployment,
  same-input comparison, authenticated UI checks, and actual timing.

## Verification status

- Implementation is fixed and locally reviewed; not approved for completion.
- Parent backend check: 11 passed using isolated in-memory SQLite and
  `FACILITY_MASTER_PATH=backend/src/data/facility_master.template.json`
  (absolute path supplied at execution).
- The first parent DOM run exposed obsolete delayed-response fixtures and
  incomplete adaptations. It is not a successful acceptance run.
- Independent review identified stale common job status, incomplete
  current-revision fences, and retired detail-error publication. All were
  corrected and covered by component tests.
- Worker related frontend suite: 813 passed, zero failures/skips
  (`/tmp/demand-final-owner.log`). TypeScript check passed.
- Parent rerun of demand-read and native-owner component tests: 61 passed,
  zero failures/skips. The monitor approved the code review only.
- No commit, merge, or staging deployment has occurred for this branch yet.

## Live verification constraint

- Exact target: `ORD581f55d6`, staging workflow-v2.
- A new owned hidden IAB tab reached the normal Google login page. Semantic
  sign-in with the already-authorized account did not navigate or open a popup.
- Staging Web logs after 2026-10-20 11:37 UTC showed only `/api/auth/config`
  (HTTP 200, 0.017793978 seconds at 11:40:23 UTC), not the application login
  callback request. The browser reported no JavaScript dialog or warning/error.
- The underlying Google/browser cause is not established. No authentication
  bypass, alternate identity, user-tab operation, or permission change was used.
- This does not prove authenticated page behavior or end-to-end latency.

## Release source checks

- Origin develop was fetched before creating the named worktree.
- Production Web and worker still use source `7ae61ee10337`, an ancestor of
  this branch's base. Production was read only.
- Hospital jj working copy is empty. Shift jj has unrelated uncommitted
  changes; it is not part of this hospital deployment and remains untouched.
- Historical sibling commits must not be treated as new fixes solely because
  their original SHAs differ from integrated/cherry-picked SHAs.
