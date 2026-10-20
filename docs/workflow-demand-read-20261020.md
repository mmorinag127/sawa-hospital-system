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
- PR113 merged into develop at `4c0bc6dbcefa3c587ec8e03de345dec3d3857ee4`.
- Formal PR CI `39421362085` passed backend, frontend and infrastructure.
  Backend completed in 25m24s; frontend in 4m35s.
- Post-merge parent component checks: 68 passed, zero failures/skips.
- Staging Actions `39424788076` ran in normal mode with existing,
  provenance-verified OCR artifact run `39109725288` and review order
  `ORD581f55d6`. This is GET-only UI verification, not OCR reprocessing.
- Automatic push run `39424702757` was cancelled before deployment to avoid
  duplicate deployment. It is not counted as successful verification.
- Post-merge parent backend checks: 11 passed.
- Deployment succeeded, but the overall run failed the optional browser checks.
  Web is `web-stg-00426-ck9`, 100% traffic, image
  `sha256:1eece76bbae9cc3c6ab8c235781408dfcfc0498384166ec6e6d0f1b34c678419`.
  Worker is `worker-stg-00867-29r`, 100% traffic, image
  `sha256:b78b5eab6e944b9c6beb2b45e9d127d0f33ca55ccfce1668ea474d5ddd56abe8`.
- Browser artifacts: `/private/tmp/sawa-pr113-live-ui`.
  Target inspection body completion minus request start was 9.017 seconds.
  This is one sample, not full-page acceptance or a controlled speed ratio.
- The verifier incorrectly required every target to be an unloaded detail.
  Read-only staging SQL confirmed target `ORD581f55d6` is revision 56 with
  selected evidence `OEV9fef6b9e2672`; the older artifact had revision 53.
  Sibling `ORDc69f352e` is revision 10 with no selected evidence.
  The verifier also omitted `open-ocr-detail` from its diagnostic phase list.
  Correction is in progress; operator state will not be reset for a test.

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

## PR114 verifier resource-gate correction

- Base: `580bb00680c14c19f66682cb1fb97838292e1828`; work branch:
  `codex/hospital-live-resource-gate-20261020`.
- Staging artifacts from Actions `39433164601` are under
  `/private/tmp/sawa-pr114-live-ui`. OCR cases timed out in
  `open-ocr-detail` after 30003/30005 ms; review timed out in `open-step2`.
  No inspection-detail response was recorded.
- Read-only local WebKit reproduction used the product's exact Google Fonts
  CSS `@import` and `readLiveCandidate`. Holding that stylesheet produced a
  1503 ms click timeout at `visible, enabled and stable`, with one matching
  navigation element. Screenshot also timed out waiting for fonts.
  Immediately aborting the unverified stylesheet completed the same helper
  in 51 ms. A separate Blob PDF iframe reproduction completed clicks in
  40-85 ms. PDF navigation blocking was not reproduced.
- Both verifiers now execute the same resource gate: reject writes immediately;
  continue same-origin GET/HEAD; abort external non-image resources immediately;
  hold external images until receipt validation, then permit only exact
  verified URLs. No external CSS/font allowlist, force click, or production
  timeout change was introduced.
- Timeout diagnostics retain only a recognized verifier filename and line,
  never exception messages, values, full paths, or tokens. The unused review
  predicate was removed; its method/origin/exact-URL tests execute the common
  runtime gate instead.
- The OCR live verifier also checks raw JSON: zero initial raw requests,
  one request on opening, exact order/workflow revision/selection, displayed
  JSON equal to the response, and no `pre` after closing. It submits no
  quantities and does not adopt evidence.

Reproduce the old CSS deadlock and verify the corrected gate with real WebKit
(all responses are synthetic; no remote or business traffic):

```sh
cd /Users/mmorinag/Sawa/2025.12/worktrees/hospital-workflow-demand-read-20261020/frontend
node --test --test-concurrency=1 tests/config/ocr-resource-gate.test.mjs
```

Related verification command:

```sh
node --test --test-concurrency=1 \
  tests/config/ocr-resource-gate.test.mjs \
  tests/config/ocr-live-state.test.mjs \
  tests/config/ocr-live-diagnostics.test.mjs \
  tests/config/ocr-live.test.mjs \
  tests/config/ocr-review-live.test.mjs \
  tests/config/ocr-first-request.test.mjs \
  tests/config/ocr-live-endpoints.test.mjs \
  tests/recognition-review-live-contract.test.cjs
```

Result: 93 passed, 0 failed, 0 skipped. Log:
`/tmp/live-resource-gate-tests.log`. WebKit ran outside the local sandbox,
as required by its macOS launch environment. These are local regression and
fixture results, not a successful staging rerun. Product code, UI parameters,
business values, authentication, DB, and Actions were not changed. No commit,
push, or deployment was performed by this implementation task.

## PR115 verifier UI-readiness correction

- Base: `dd13c080`; branch: `codex/hospital-live-ui-readiness-20261021`.
- Actions `39443672180` deployed successfully. Artifacts under
  `/private/tmp/sawa-pr115-live-ui` show the review dialog passed; OCR case 1
  reached image/page capture but crashed during the post-reload detail read.
  Case 2 was not executed. This is not whole-flow acceptance.
- Parent server-log evidence: reload at 15:13:56.012, inspection started at
  15:13:57.079 and completed in 0.917529 seconds; order started at
  15:13:57.082 and completed in 1.053654 seconds. The verifier awaited only
  inspection, while the production page awaited both responses. No subsequent
  inspection-detail request was recorded.
- A mounted production-page regression reproduces an enabled STEP2 click
  while workflow is still loading, followed by the delayed workflow-state
  effect resetting the page to Step1. With DOM readiness synchronization,
  no early click occurs and the Step2 candidate remains after effects settle.
- Both verifiers now await inspection and order on initial load and reload,
  validate order/document/revision ownership, and wait for the visible state
  headline to leave its loading state before interaction. These waits use
  existing assertion deadlines; no fixed sleep, force click, retry loop,
  timeout extension, product change, or authentication change was introduced.
- The detail promise attaches success/error handlers immediately, before the
  detail-button click. Rejections are rethrown when consumed, never treated as
  successful evidence. Early detail failures and click failures on both
  initial load and reload execute through the full verifiers in fixtures,
  save failed result JSON, close their browsers, and produce no unhandled
  rejection. Existing successful selected/unselected paths remain covered.

Regression command (production DOM with synthetic transport, not live OCR):

```sh
cd /Users/mmorinag/Sawa/2025.12/worktrees/hospital-workflow-demand-read-20261020/frontend
node --test --test-concurrency=1 \
  tests/config/ocr-ui-readiness.test.mjs \
  tests/config/ocr-live-state.test.mjs \
  tests/config/ocr-live-diagnostics.test.mjs \
  tests/config/ocr-first-request.test.mjs \
  tests/recognition-review-live-contract.test.cjs
```

Result: 86 passed, 0 failed, 0 skipped. Log:
`/tmp/live-ui-readiness-tests.log`. The product DOM harness emits its existing
jsdom warning about the JSX style attribute; no test failed. No business
data, quantities, authentication, product code, or Actions were changed.
No commit/push/deploy or staging rerun was performed for this correction.

## PR116 terminal/current-state separation and original-only diagnosis

- Base: `81b1aa2c2c357eddb1bd3710571da6f3250e6893`; branch:
  `codex/hospital-live-current-state-20261021`.
- Failure classes: immutable native job outcome was conflated with the later
  operator workflow state; the original diagnostic results were only saved
  after all fault cases, exceeding the existing offline child deadline.
- `assertNativeIngestOwner` retains execution/job/evidence/order/PDF/hash,
  attempt-null, producer, terminal status and error assertions. Historical
  state remains checked in `verificationOutcome`; the live native check
  separately checks the current selected/unselected state. Reload snapshots
  now also preserve workflow state.
- Selected-invalid observations require the explicit `evidence_unusable`
  workflow blocker, selected evidence identity, `selectable=false`, public
  acceptance `null`, and the existing full canonical receipt assertions
  against the manifest receipt (including original payload hash, PDF,
  digest, prior revision, cells and values). They may collect image/page
  screenshots, reload and raw-open/close evidence, but finish with
  `status=blocked`, `code=selected_invalid_requires_original_diagnosis`.
  This generic public blocker is NOT treated as root-cause parity proof.
  Invalid receipts or unrelated blockers still fail before observation.
- `diagnose_reviewed_ocr.py --original-only` runs the four original-input
  phases for both source revisions and both existing OpenCV profiles.
  The explicit Actions diagnostic invocation uses this mode. The default
  all-fault mode remains unchanged in case coverage. Child timeout remains
  300 seconds; workflow timeout remains 15 minutes.
- Original-only completion means diagnostic completion, not selectable
  evidence: summaries explicitly record `mode` and
  `original_validation_passed`. Full private values, exception identities
  and error order are still compared before cleanup, including when both
  versions reject the original input. A mismatch remains a failed run.
- No product, business values, authentication, DB writes, dependencies,
  commit, push or deployment changes. Existing untracked dependencies and
  lock files were preserved.

Focused verification:

```sh
cd /Users/mmorinag/Sawa/2025.12/worktrees/hospital-workflow-demand-read-20261020/backend
env DB_URI=sqlite:///:memory: \
  FACILITY_MASTER_PATH=/Users/mmorinag/Sawa/2025.12/worktrees/hospital-workflow-demand-read-20261020/backend/src/data/facility_master.test.json \
  /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/backend/.venv/bin/python \
  -m pytest tests/unit/test_reviewed_ocr_diagnostic.py -q

cd ../frontend
node --test --test-concurrency=1 \
  tests/config/native-ingest-live.test.mjs \
  tests/config/ocr-live-state.test.mjs \
  tests/config/ocr-live.test.mjs \
  tests/config/ocr-review-live.test.mjs \
  tests/config/ocr-live-diagnostics.test.mjs \
  tests/recognition-review-live-contract.test.cjs
```

Results: backend **120 passed**, frontend **101 passed**, no skipped tests.
Backend Unix-socket fixtures required execution outside the local sandbox.
An initial `DB_URI=:memory:` invocation failed configuration parsing; the
explicit SQLite URI above was used without changing runtime code. The first
sandboxed run had 111 passed/9 failed (8 socket permission failures and one
SIGTERM fixture signature); that fixture was updated for the new keyword
while retaining its cleanup assertions. Frontend initially had 99 passed/
2 failed; the old source assertion mapping was updated and screenshot
observation is checked via actual fixture screenshot calls, not a timing
entry that was never recorded.

These are focused local regressions, not a successful real-input diagnostic.
Earlier read-only run `39456171409` produced only
`unexpected_TimeoutExpired`. The new original-only Actions result and
same-input baseline/current comparison remain pending with the parent;
overall parity/completion is explicitly NOT established.
