# Hospital Recorded Job Status: Source Packet

Base: `cfedb6ea72735bd6a2b5307699e5a9c35eb90503`.
Branch: `codex/modernization-j1-hospital-status-20261008`.
Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-j1-status`.

## Scope and Invariant

Failure class: independent hospital job display omits recorded input/execution identity.
Shared boundary: existing `get_hakodate_pipeline_job_status`, using exactly the job
already selected by `get_latest_order_job` and the existing request-mode gate.
Additive `job_status` copies recorded fields; it does not read current order state,
workflow state, configured sources, providers, or a later receipt.

Recorded mappings:
- `jobId`: job `id`; absent/empty ID produces null projection, never a fabricated ID.
- `inputVersion`: job `input_artifact_digest`, as serialized by `_job_to_dict`.
- `inputReference` / `artifactReference`: job `input_reference` / `output_reference`.
- `attemptIdentity`: recorded `metrics.workflow_attempt_id` (including zero), or
  recorded nonworkflow `execution_id` only when its job/order identities match this job.
  Competing ownership markers are not resolved by choosing one; identity stays null.
- `sourceState`: unchanged saved job status. `sourceLabel`: existing Japanese label
  for the existing described status; unknown described status retains its spelling.
- `state`: pending/queued/running/failed from the existing described status.
  Success requires both described `done` and saved `done`. Awaiting output,
  recovering, stalled, unknown, success/completed aliases are not new success claims.
- `failure.message`: recorded metrics error when present, otherwise recorded job
  error_message. No error-code source is introduced; code remains null.

The original status response keys, request-mode rejection, normalization and original
error/stage/result/output expressions remain. Only the additive projection and helper
are added. Routes, auth, scheduling, adoption, OCR and quantities are untouched.
The original page parser retains the added field without reconstruction. Display uses
the explicitly received status, removing the workflow-state alternate selection.
Existing Japanese labels/detail markup remain alongside common JobStatus. The original
detail's `order.ocr_processing_stage` fallback is retired: stage formatting receives
only this received job's `processing_stage`; absence stays empty. No geometry/business
metrics, native refs, CSS, provider or session changes. Display JavaScript intentionally
changes; no whole-page emitted-JS parity claim is made.

## Frozen Sources

Exactly five files; counts are total/nonblank, not executed AST measurements.

| File | Before | After | SHA256 |
| --- | --- | --- | --- |
| backend/src/services/order_service.py | 36422/34169 | 36459/34204 | d4cb506a7aed3b04430a00dfd8f616567731773822e57c736a34d99c37c68c42 |
| frontend/src/pages/orders/[id].tsx | 16246/15447 | 16246/15447 | ce4650656022200f39ef207452c4e4156b55a4e201a68b3ed9b5d97e66ed927f |
| backend/tests/unit/test_hospital_job_status_contract.py | absent | 128/102 | de427dc1ab227919fe9eef0f1cf50259e1001fbe8dcf502eb2f1988e5c68b3c5 |
| frontend/tests/hospital-job-status.test.cjs | absent | 68/64 | 5935c27d4587a31f961768ca648fd5769d7fa307ca551856fad82b801a459da1 |
| docs/modernization/j1-hospital-recorded-status-source-20261008.md | absent | this document | hash reported outside this document |

## Definitions and Evidence Boundary

UNEXECUTED backend definitions cover unchanged other function ASTs, original target
function AST after removing only additive projection, original response keys/gate,
typed JSON-compatible null/zero/raw spelling, exact saved-done gate,
unique/foreign/competing ownership, missing job, recorded errors and unresolved
states. Fault/component doubles replace latest-job reads and status description only;
these are not formal DB, recognizer or owned operator-flow proof.
UNEXECUTED frontend definitions restore exactly the five approved page edits and compare
all remaining source bytes, retain parser/labels/details, and inspect actual JSX props,
conditional authority and missing-record branch. A separate AST definition requires
exactly one received stage argument, rejecting the current-order stage fallback.
No mock status ID or success fallback.

The first frozen patch `edbd11854bd45ea299e39aa6f2489f0651fbc6042a0b145a1b3185a044d24912`
is superseded after source review found the remaining stage fallback. Its file hashes
are historical, not current proof. No test was executed before or after this correction.

Commands executed only for source work: `git rev-parse HEAD`, `git branch --show-current`,
`git status --short`, `rg`, `find`, `cat`, `sed`, `ls`, `git diff`, `git show`, `wc -l`,
`awk`, `shasum -a 256`, manual `apply_patch`, and final scoped `git add`/cached diff hash.
One exploratory read referenced absent test files; no tests were run. Installed Next
AGENTS and use-client guidance were read from hospital-main dependencies read-only;
no dependency copy/install or runtime import occurred.

All technical acceptance remains pending: Python/TS parsing, focused test execution,
actual DB/API projection serialization and ownership, real React/MUI rendering,
unknown/missing-record display, original Japanese detail parity and integrated regression.
Live/user flow, deployment and whole-modernization acceptance are not claimed.
No tests/build/generation/tsc/lint/runtime imports/server/browser/DB/API/network/commit
were executed. Parent source monitors and integrated-SHA verification remain required.
