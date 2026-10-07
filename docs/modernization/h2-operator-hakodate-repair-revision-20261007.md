# Operator Hakodate repair revision source contract

Worktree: hospital-h2-revisions; named branch
codex/modernization-h2-workflow-revisions-20261006.
Base: d3d39bf3198f3ba631c82c7c8bd396323dcd2484.

Failure class: request-time capture allowed repair writes from an operator whose
original edit revision was stale. The shared assignment entry now requires the
operator's explicit expected revision for persistent repair; it checks before
provider computation and captures authority in that same read transaction.
It never replaces the caller revision with a current revision.

API commands and common paths:

- POST hakodate-assignment -> build_order_hakodate_assignment.
- POST quantity-assignment-compare -> build_order_quantity_assignment_comparison
  -> get_ocr_sheet and build_order_hakodate_assignment, sharing the original input.
- POST hakodate-projected-sheet -> build_order_hakodate_projected_sheet
  -> build_order_hakodate_assignment.

All three require the existing If-Match dependency. Missing or malformed headers
are 428; a parsed invalid revision is 422; stale is 409. The service independently enforces the explicit revision.
Provider uses captured PDF/config/metadata/order lines. Actual cache persistence
retains the original synchronous write receipt and existing same-transaction
ownership fence. Provider failure or missing repair artifacts cannot save old
payload as successful repair. A failed actual write gate returns conflict.

Full repository Python production search identified these internal callers:

- Assignment preview in get_latest_sheet_draft: allow_artifact_repair=False.
- Hakodate overlay diagnostic (original 21119): allow_artifact_repair=False.
- ensure_hakodate_evidence_draft_current -> projected sheet: explicit selected
  evidence; assignment persist_artifact_repair=False; draft adoption retains its
  existing synchronous receipt.
- _persist_ocr_rerun_completion -> projected sheet: own returned evidence under
  caller-owned fenced completion Session; assignment repair is nonpersistent.
- Current evidence projection helper (original 14508) -> projected sheet:
  explicit evidence; nonpersistent assignment, existing synchronous draft fence.
- Projected sheet -> assignment and comparison -> assignment are the shared
  service transitive callers above.

No script callers were found. Truly read-only assignment calls disable artifact
repair, therefore neither provider repair nor cache mutation is entered. Existing
own job/evidence projection paths keep their accepted execution receipt rather
than inventing an operator token. Their recognition/adoption policies are not
changed by the new operator repair requirement.

Focused definitions invoke the three actual endpoints and services with formal
ORM fixtures: missing 428, stale 409 before provider, current repair cache write,
failed provider no cache write, revision switch during provider denied by the
real write fence. Only provider/calculation outputs are injected; persistence
and ownership gates are not replaced with always-success mocks. Zero preserved.

Source only: no test/runtime/DB/network execution. Diff hygiene only. Existing
staged output/terminal corrections and frontend remain unchanged. Frontend
If-Match propagation is a separate frozen UI integration residual, not implemented
here. Output acceptance timing remains pending; 193 legacy caller migrations
remain deferred. This is not full H2 or runtime verification completion.
