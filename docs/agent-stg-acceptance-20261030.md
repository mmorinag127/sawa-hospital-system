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

Live case oracle/results are maintained in the project-level
`docs/reviews/agent-stg-validation-20261030/` directory. Every pending check must
remain explicitly incomplete until its new live evidence is captured.
