# Agent Staging Acceptance

Completion requires all 12 public tasks executed against authored known answers,
with operator approval, persisted readback and real output comparison. Old
completed histories, mock model transports and empty target lists are not live
positive proofs. Existing business data, production, IAM and budget ceilings are
not changed. Deployment uses the owner-dispatched staging Actions only.

## Implementation Before Verification

| Finding | Shared Fix | State |
|---|---|---|
| New-run reads drive work before preparation ends | Do not schedule preparing durable runs without a sealed working copy | Implemented, unverified |
| Metadata writes seal an unfinished before-image | Exclude locally preparing copies from automatic checkpoints | Implemented, unverified |
| Preparation saves originals but no working copy | Atomically persist originals and initial checkpoint before enabling execution | Implemented, unverified |
| Concurrent immutable file receipts raise raw unique errors | Compare digest after conflict-safe insert; reject every differing digest | Implemented, unverified |
| Shift inspection omits shortages and wishes | Read period-bounded demands/constraints and staff before approval | Implemented, unverified |
| Inspection/export UI promises a new confirmation | Task-specific read-only inspection and export descriptions in both UIs | Implemented, unverified |
| Review queue hides completed OCR awaiting operator confirmation | Include completed unconfirmed orders, retain failure states and exclude confirmed orders | Implemented, unverified |
| Hospital has only a pending-FAX baseline | Select an owner-bound review/completed request; freeze its checkpoint in the selection and materialize a separate private copy | Implemented, unverified |
| Default reservation prevents dependent review tasks within available budget | Optional per-request USD cap through the existing cap_cents API; monthly budget unchanged | Implemented, unverified |
| An approval-waiting source blocks its private review fork | Admit only the same-owner/same-generation source at approval with no unsettled commands; other active requests still block | Implemented, unverified |
| Copied OCR artifact references retain the source absolute directory | Newly initialized trial storage returns request-relative paths; production/legacy behavior unchanged; old nonportable sources rejected | Implemented, unverified |
| Live polling and controller commits conflict at SERIALIZABLE snapshots | Serialize this epoch-owning instance's ledger writers before opening transactions, including preparation persistence; reads remain concurrent, no business replay | Implemented, unverified |
| Unchanged FAX polling writes the same row and checkpoints every native file | Null-safe value-difference guard alongside the existing observation CAS; changed data still checkpoints, stale observations cannot overwrite operator decisions | Implemented, unverified |

Product changes are complete for the known findings. Regression additions cover
preparation visibility, immediate restart, immutable-file collision/digest
mismatch, scheduling before readiness, read-only shortage checks, UI text and
owner/generation/hash confinement and immutable copy selection. Execute them together and serially.

The first batch at source 05273c037083 was interrupted before receipt creation
when the review-queue omission was found during source inspection. Its results
are not a complete verification or deployment receipt.

The hospital review/confirmed copies must come from a real run of the
declared FAC00001 PDF (SHA256
ece4e4a98a394402eea9f8b9396fcaaddfcae6b28d8e5f1a5c2a6075a3e1df0e).
Capture the review checkpoint before approval and the confirmed checkpoint after
completion. Do not synthesize OCR receipts or change quantities to pass tests.
Selections freeze the owner, generation and content hashes server-side. The
existing private durable blob store supplies the copy; no private database or
transport credentials enter the public repository. Stopped/restored sources,
in-flight commands, missing blobs and cross-owner sources are rejected. The
source remains untouched. Deploy all fixes once after the complete local batch;
capturing live checkpoints does not require a second fixture deployment.

The second batch at 936f63d5d124 was interrupted without a receipt while this
private copy design replaced packaged snapshots. Its initial backend command
also found an unmapped ingest.py dependency; the scope mapping is now explicit.
Neither interrupted attempt is a completed verification.

Batch 7c55cea4557b finished in 852 seconds without a receipt: E2E found an
external node_modules symlink; seven discovery checks found selected-day scope
applied before selection; four new unit fixtures left their initial run active.
Dependencies are now an independent APFS copy. Lookup metadata is restricted to
actual hospital forks and fixtures explicitly finish their setup request. The
same review also added the quiescent-parent admission and relative-storage
boundaries above. No failing result is claimed as a pass or deployed.

Live case oracle/results are maintained in the project-level
`docs/reviews/agent-stg-validation-20261030/` directory. Every pending check must
remain explicitly incomplete until its new live evidence is captured.

Staging source cf43d742 passed the full local batch and deploy 40998488150.
Live pending-FAX run 3f0a4b5c69104334b91a72b3831bbb99 completed OCR and prepared
its review, then a concurrent ledger commit raised SerializationFailure. Its
saved OCR/review must be preserved and resumed through the normal recovery UI,
not rerun or repaired by editing the ledger. The new writer-boundary regression
holds one real PostgreSQL transaction open, verifies another writer waits before
its snapshot, and verifies a reader remains available. Injected external
conflicts still roll back and propagate without executing a body twice.

Batch 3a876df0 finished in 1169.711 seconds. All four new PostgreSQL boundaries
passed; the agent suite had 492 passes and one legacy conversation-fixture
failure. Its future-only fake stream repeated answered call q2, so the real
unknown-stream fence correctly rejected it depending on scheduling. The fixture
now emits fresh q3 and also asserts q3 remains answerable, waiting_input is
retained and no error is recorded. Stop/old-answer rejection assertions remain.
No application answer fence was relaxed. This failed batch has no success receipt.

Two f6201291 batches finished without a success receipt: a Docker inventory
connection refusal, then a PyPI read timeout. All application suites passed in
both. The read-only dependency audits now retry the exact allowlisted command
once only for named transport exceptions. Both attempts stay in the receipt's
hashed log. Findings, ordinary tests, changed commands and a second failure are
not retried or converted to success. The source and manifest stability checks
remain unchanged. Added coverage checks those boundaries before a fresh batch.

## Live continuation findings after d2308406

The signed 30-command batch passed in 1061.663 seconds; staging deployment
41010093996 succeeded. Four completed shift workbooks were reopened and downloaded
again from the normal UI, with exact SHA256 matches to the checked originals.
The pending hospital run retained its 56 rows, three twenties and 333 blanks.
Review copy 6d126ced5cd64d558d0810c5685fca64 retained the exact original PDF hash;
the explicit copy-only cabbage correction was accepted, without another OCR.

Live approval of the pre-deployment parent returned HTTP 409: native URLs are
process-local and were absent after restart, while metadata recovery had already
restored the approval phase. Native request dispatch now starts the owned saved
copy when its API is missing or its owned process exited. Scope, stop/generation,
working-copy and source checks stay in Sandbox.start; no HTTP request is retried.
This covers both FAX and ordinary task approval reads without duplicating route
specific startup logic or resuming a model under a changed execution contract.

The UI also keyed correction drafts only by FAX ID, so a private copy sharing
that identifier displayed its text in the parent. The rejected parent request
did not change quantities. FAX drafts now include request and review identity;
ordinary review and reply drafts include request and current form identity.
Approvals never carry an unsent correction. Unknown responses keep the submitted
text, pending inputs are disabled, and late source-upload cleanup cannot clear a
different request's file selection. Tests cover copy/new-review separation,
transport uncertainty, stopped/missing-copy rejection and no business replay.

All these known implementation changes are complete before the next serial
batch. Their tests and staging acceptance remain pending; d2308406 is still the
live source. The correction request's observed 106.814-second HTTP duration is
recorded separately, not attributed to an individually measured native endpoint.

### Third Live Failure: Correction Narrative Without Corrected Cells

Run `6d126ced5cd64d558d0810c5685fca64` claimed cabbage quantity18,
but its actual approval sheet retained20. Durable model events show12 rejected
full-sheet submissions: protected canonical metadata did not match. The final
summary-only submission then used the unchanged native sheet. It was not approved.
This is a failed acceptance attempt, not a positive result.

The tool now accepts sparse `cell_edits` resolved against exact native
date/daypart/menu identity, preserving all unrelated cells and metadata. Missing
or ambiguous identities are rejected; blank/symbol/text quantities are retained.
Legacy full-sheet proposals retain support but must preserve row count, column
shape and protected business identities. Subsequent proposals preserve prior
operator edits and reject stale native revisions. Approved proposals cannot be
replaced. After correction, the visible summary is derived from actual cell
differences; model narrative is retained separately for audit, never as evidence
that a quantity changed. Tests cover actual18, blanks, symbols, ambiguity,
malformed matrices, source preservation and renewed human approval.

Implementation and test updates are complete for the three known live defects.
No tests/build/deploy have yet run against this dirty revision. The full12-task
acceptance remains incomplete (4 previously verified positive cases).

### MCP Boundary Found During the New Staging Attempt

Run `48b227225f9646e387d6894fa6122d9b` received the same authored cabbage18
correction. Its model submitted only a narrative again. The server correctly
displayed "no quantity/remarks changes", and the unchanged20 was not approved.
The independent FastMCP function had not exposed/forwarded `cell_edits`, despite
the direct Tools schema and tests supporting it. Its signature, forwarding and
task instructions are now updated. The test now executes the correction through
the actual FastMCP boundary, and all advertised tools' top-level properties and
required fields must agree with their registered MCP schemas.

The original request's restored approval succeeded (HTTP200,115.866seconds) and
its normal server-owned save/output sequence is still running. It is not retried.
Before the next minimal deployment this sequence must reach a known result.
New changes require a new signed local receipt; the prior79354753 success is
preserved, never relabelled as coverage for this follow-up. The signed79354753
receipt is used only as the verified ancestor for existing impact selection.
