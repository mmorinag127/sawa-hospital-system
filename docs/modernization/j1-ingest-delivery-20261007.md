# J1 ingest/upload durable delivery source unit

Base: `c93d54a18458c740bbaeca085d6047d66b7a8370`.
Branch: `codex/modernization-j1-ingest-delivery-20261007`.
This is implementation-first source for parent review, not technical acceptance,
runtime verification, staging cutover, or completion of J1/modernization.

## Original acceptance and actual callers

- `ingest_job_service.create_ingest_receipt` saves the accepted original payload,
  digest and business generation with the delivery intent in one transaction.
- `uploaded_pdf_service.create_uploaded_pdf_from_upload` saves UploadedPdf and
  the actual ingest reservation/intent in that same transaction. The API upload
  handler uses that reservation instead of opening a second acceptance Session.
- Direct mail/API ingest uses the same original reservation boundary.
- `restart_ingest_job` creates an intent for an explicit manual business restart.
  `requeue_uploaded_pdf` combines its original upload reset with that restart.
- Upload enqueue, manual retry, uploaded retry and recover-ready use durable
  dispatch. Reset-stale selects the existing intent; it does not reset generation
  or budget merely because transport delivery was interrupted.
- `enqueue_ingest`, `enqueue_ingest_async`, `enqueue_ingest_job_async`,
  `enqueue_uploaded_pdf_async`, `process_uploaded_pdf_job` and the compatibility
  `process_ingest` entry point no longer submit a local ingest runner.
- `/worker/ingest-deliveries/process` validates the exact recorded generation
  and enters the actual `process_ingest_job` with the original persisted lease.
- `/worker/ingest-deliveries/drain` independently dispatches eligible intents and
  runs existing pending-output reconciliation. No startup ingest recovery thread,
  ingest ThreadPool execution, inline automatic substitute or Celery retry remains.

Business capture timing is unchanged: order/document registration and
`prepare_ingest_ocr_job`/`capture_nonworkflow_execution` still occur at first owned
execution. Acceptance does not fabricate facility/menu/config capture. Interrupted
execution and reconciliation reuse the recorded capture, never current config.
Recognizer, quantities, operator inputs and original output adoption are unchanged.

## Ownership and receipts

Transport generations renew only the recorded task name/body generation. They do
not create a business generation, reset attempts or replace source metadata.
The original execution mutex is keyed by ingest job identity, across both kinds of
generation: PostgreSQL advisory exclusion or SQLite file exclusion. DB mutations
also use writer transactions/row locking and exact delivery claim-token fences.
Unknown/future/missing generations fail before duplicate handling. Known old or
acknowledged deliveries do not run OCR. Mutex contention is an error, not an ACK
without persisted active-owner proof.

ACK requires a saved original terminal receipt and settled ingest/upload ownership,
or an atomically saved original awaiting-output handoff. Handoff checks the existing
pipeline owner/result validator, selected PDF digest, configured-input digest,
selected/input/output references and original execution capture. Its ACK is a
transport handoff, not business success. Later reconciliation retains the documented
original handoff ownership without substituting the newest transport claim.

Real registration/prepare/capture validation failure before business capture saves
an original source-failure receipt, ingest error and (when applicable) uploaded
failure warning under the same digest/generation/claim transaction. That persisted
business failure can ACK without inventing OCR success. Budget exhaustion must have
the saved original claim refusal. Hard failures are not automatically queue-filled.
Empty returns, receipt refusal, foreign ownership and storage exceptions are not
success. Non-OCR skipped registration also closes the original UploadedPdf with
its owned lease and a real document receipt; this is not OCR proof.

## Durable automatic LLM successor

The actual `_complete_first_pass_job_in_session` producer accepts an eligible
automatic LLM successor in the SAME transaction as first-pass evidence and terminal.
Enabled/disabled eligibility, payload shape, selectable evidence, facility/document
presence and provider normalization retain the original automatic path semantics.
The accepted request records `llm_assist=True`, provider and all five reparse request
fields once, plus the existing recovery-grace setting at that accepted phase.
It records the exact predecessor evidence id/digest/payload and first-pass receipt.
No post-commit enqueue call is required for intent durability.

The successor has a dedicated OcrJob, execution id and transport identity. It does
not overwrite the first-pass job, receipt, request or ingest/upload result. The
actual autoLLM ThreadPool has been removed. Its existing enqueue helper only locates
the already accepted intent; independent drain dispatches it after original business
settlement and predecessor transport ACK. The transport contract is derived once
from the original recorded parent contract, with its dedicated authenticated
`/worker/ingest-deliveries/auto-llm` route, task identity and body. No live config,
queue, IAM or deadline is guessed.

The accepted source/start input remains immutable. Existing parent-owned
`advance_nonworkflow_order_input` writes can advance an unstarted successor's CAS
snapshot in the SAME writer transaction, using that exact recorded parent advance;
they cannot recapture configured inputs, change the accepted source or advance an
already started successor. First execution seals this phase and verifies the saved
predecessor version. Transport retries preserve that started phase and resume the
successor's recorded progress instead of reusing a stale parent snapshot.

Actual writes enter through `lock_ingest_lease`, then the dedicated successor fence,
and all existing nonworkflow selected-PDF/configuration/order CAS guards remain.
The fence checks exact successor id/generation/token, immutable source keys, original
ingest reservation and predecessor terminal/evidence. It does not reuse the parent
claim or allow arbitrary writes after parent ACK. The successor request binder and
first-pass resume helper explicitly validate the recorded successor contract. The
parent payload keeps its original lineage; it is not rewritten to pretend it was
produced by the new job. Later output is accepted only against this successor's own
recorded pipeline result contract.

Owned terminal and pending receipts are atomically bound to the successor token.
Pending requires the actual pending receipt, exact request, selected/input/output
references, pipeline generation and digests. If an interrupted launch already saved
its pipeline contract, transport retry restores the actual waiting receipt without
launching another pipeline. ACK distinguishes terminal failure from downstream
durable handoff. `run_ocr_job_recovery_once` retains the original successor handoff
token under physical exclusion; it does not supplement it with the parent's latest
claim. Downstream failure uses the actual rerun terminal persister, not ingest
failure/completion setters. No receipt, storage refusal and stale ownership remain
errors. Hard-failed/superseded originals are not automatically queue-filled.

Model-level original/transport immutability compares the actual persisted row in
the same writer connection using strict canonical JSON, including in-place changes.

## Explicit deployment contract

All following `HOSPITAL_INGEST_DELIVERY_` values are required before acceptance:
`PROJECT_ID`, `LOCATION`, `QUEUE`, `TARGET_URL`, `SERVICE_ACCOUNT_EMAIL`, `AUDIENCE`,
`DISPATCH_DEADLINE_SECONDS`; additionally each of `WORKER_` and `DRAIN_` requires
`ISSUER`, `AUDIENCE`, `EMAIL`, `SUBJECT`. Worker trust must match task OIDC email and
audience. Worker/drain trust is independently verified. No live values, queue, IAM,
scheduler, deadline, production permission or deployment were selected here.

The persisted Cloud Tasks contract includes name, URL, POST body, headers, OIDC
identity/audience and deadline. FULL lookup on AlreadyExists distinguishes missing
task/tombstone from unknown provider failure and refuses conflicting request content
or OAuth. Conditional tombstone renewal uses the original recorded contract only.
Persistent visited counts are advanced before provider calls for fair draining.
The adjacent bodyless workflow helper now rejects unexpected body and OAuth without
changing workflow business/migration logic or harmless provider headers.

## Authored cases and deferred technical verification

`backend/tests/unit/test_ingest_delivery_contract.py` contains executable unit-case
definitions, not execution evidence: acceptance rollback, missing config, upload
atomicity, original lease reuse/stale token, generation validation, busy exclusion,
empty returns, non-OCR failure/skip settlement, early source failure and duplicates,
storage refusal, failed drain exclusion, auth rejection, exact FULL task mismatch,
tombstone renewal, persistent fairness, physical SQLite mutex and budget refusal.
Handoff cases use real receipt transactions and the existing owner matcher with an
isolated result-validation adapter; they cover saved handoff ACK, retained original
reconciliation fencing, foreign owner and selected/configured source mismatches.
They are not end-to-end pipeline reconciliation proof.
The existing workflow runner case file adds body/OAuth rejection siblings.
Successor unit definitions additionally invoke the actual first-pass terminal
producer in real Session transactions (rollback, distinct job, disabled, recorded
provider/request), owned terminal/failure/no-receipt/storage cases, immutable nested
JSON, pending source/request/owner rejection and the actual recovery dispatcher plus
rerun failure persister. Recognizer, full order snapshot CAS and result-validation
adapters are isolated where explicitly stated; these cases are NOT end-to-end proof.
Fixtures are metadata-only, not facility/week/PDF recognition parity proof.
No test, solver/OCR, DB, build, lint, install, network or live operation was executed.

The following is a planned technical matrix, NOT implemented/executed proof:
PostgreSQL and SQLite concurrent worker/drain exclusion; death before/after provider
send and ACK; tombstone renewal/unknown lookup; fair draining under provider errors;
real first-pass terminal refusal; awaiting-output handoff then original reconciliation;
old-claim and nonpersisted handoff rejection; manual retry/reset/budget transitions;
all actual upload/mail/non-OCR paths and unchanged recognizer/operator quantities.
Execute these before technical acceptance/staging cutover, not as a prerequisite for
implementation-first source integration. Long-run measurement remains deferred.

Migration `0034` declares `0033` as predecessor. Operational schema installation,
the actual formal schema-head registry/catalog verification and observed fingerprint
remain parent/technical-verification work. No prior migration, catalog fingerprint,
stamp, automatic production table creation or `require_formal_head` bypass was added.
Cloud Tasks package is already declared; no installation was performed. Existing
lifecycle tests will require explicit config and original delivery-owner fixtures.
Parent must review the frozen source and perform integration/technical gates.

### Owned permanent successor refusal

`process`, `dispatch` (including independent `drain`) and handoff
`recovery_context` share `refuse_if_known` under the original ingest physical mutex.
It verifies the immutable parent lease/payload digest, first-pass receipt, child
execution identity, exact saved task contract and current transport generation.
Only observed reservation, predecessor, evidence, prepared-version or complete
order snapshot differences authorize a `REFUSED` receipt. Missing records, unknown
states, future/missing generations and storage errors do not authorize terminal ACK.
An original claim context with a retired token cannot stop the current owner.

The same transaction conditionally records the owned refusal and dedicated child
OCR job warning/failed status. It does not change order, evidence, result adoption
or first-pass receipt. CAS/persistence failures propagate and roll back, not ACK.
Refusal is not a successful OCR result or proof that an interrupted LLM never ran.
Saved refusal is verified before duplicate ACK and excluded from drain/recovery.
The existing pure OCR GET serializer exposes the saved job metrics
`delivery_warning` and `auto_llm_refusal_receipt` plus `error_message`; `diagnostic`
also projects the persisted warning without claim/provider/repair writes.

A conflict arising after the pre-execution check still fails the original write
guard; a subsequent delivery/drain/recovery check can persist the proven refusal.
No exception-string classification turns a transient failure into business refusal.
Authored producer/runner/drain cases cover supersession, evidence, prepared and
snapshot differences, stale token, saved duplicate ACK and storage failure. The
snapshot fixture adapter isolates configured/PDF capture and is not live config
or DB parity evidence. All cases remain UNEXECUTED; PostgreSQL/SQLite concurrency,
provider retry/ACK and runtime/cutover acceptance remain parent technical work.

## Source file set

### FULL lookup header correction (source only)

The shared ingest `inspect_task`, also called by successor dispatch, compares
every persisted header name case-insensitively and requires its exact value and
presence. Additional provider transport headers no longer make the entire map
unequal. Case-alias duplicates are rejected rather than collapsed last-wins.
Unrecorded Authorization, Proxy-Authorization, Cookie and
X-Serverless-Authorization remain rejected alongside the saved OIDC contract.
Task name, method, URL, body, OAuth exclusion, OIDC email/audience and deadline
(including nanos) retain their existing exact checks.

Read scope: existing workflow `inspect_task` (no whole-header equality), shared
ingest lookup and successor call site. Local SDK proto-source searches in the
worktree/repository and available user/Homebrew/system Python locations did not
find `google/cloud/tasks_v2/types/target.py`; no SDK import or network lookup was
executed. Authored FULL protobuf cases cover provider extras and lowercase own
header for both ingest/successor contracts, own-header change/removal, credential
extras, case aliases and request-identity mismatches. Existing assertions remain.
All cases are UNEXECUTED; live provider-added headers remain runtime proof pending.

The original 16-file unit plus successor/shared-entry corrections comprises:

```text
backend/migrations/0034_ingest_deliveries.py
backend/src/api/ingest.py
backend/src/api/worker.py
backend/src/main.py
backend/src/models/__init__.py
backend/src/models/ingest_delivery.py
backend/src/models/ingest_delivery_successor.py
backend/src/services/ingest_delivery_auth_service.py
backend/src/services/ingest_delivery_dispatch_service.py
backend/src/services/ingest_delivery_service.py
backend/src/services/ingest_delivery_successor_service.py
backend/src/services/ingest_job_service.py
backend/src/services/ocr_job_service.py
backend/src/services/order_service.py
backend/src/services/uploaded_pdf_service.py
backend/src/services/workflow_attempt_guard_service.py
backend/src/services/workflow_ocr_delivery_dispatch_service.py
backend/src/workers/ingest_worker.py
backend/tests/unit/test_ingest_delivery_contract.py
backend/tests/unit/test_workflow_ocr_delivery_runner_contract.py
docs/modernization/j1-ingest-delivery-20261007.md
```

Work commands were source reads (`cat`, `sed`, `rg`, `ls`), `apply_patch`,
`git status`, `git branch --show-current`, `git rev-parse HEAD`, `git diff`,
`git diff --check`, `git add`, and `git diff --cached --check` only.
Runtime, tests, build, typecheck, lint, installation, DB, network, solver/OCR,
commit, merge, push and deployment remain UNEXECUTED.
