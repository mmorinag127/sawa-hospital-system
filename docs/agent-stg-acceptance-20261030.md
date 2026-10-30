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
| Hospital has only a pending-FAX baseline | Task-bound, provenance-declared native workflow snapshots, fixed by release hashes | Selection/migration support implemented; real snapshots still to capture |

Product changes are complete for the known findings. Regression additions cover
preparation visibility, immediate restart, immutable-file collision/digest
mismatch, scheduling before readiness, read-only shortage checks, UI text and
task-specific fixture confinement. Execute them together and serially.

The hospital review/confirmed snapshots must come from a real run of the
declared FAC00001 PDF (SHA256
ece4e4a98a394402eea9f8b9396fcaaddfcae6b28d8e5f1a5c2a6075a3e1df0e).
Capture the review checkpoint before approval and the confirmed checkpoint after
completion. Do not synthesize OCR receipts or change quantities to pass tests.
This dependency requires the controller fix to be deployed before snapshots can
be captured; package the captured snapshots together in the next minimal
deployment. No repeated deployment of unchanged sources.

Live case oracle/results are maintained in the project-level
`docs/reviews/agent-stg-validation-20261030/` directory. Every pending check must
remain explicitly incomplete until its new live evidence is captured.
