# H2 Review And Command Ownership Source Unit

- WT: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h2-quad-owner`
- Branch: `codex/modernization-h2-quad-owner-20261007`
- Base: `07489c1da1c775503d08f7789bdd2a81d8ad53f8`
- Source only; no runtime/tests/build/install/network/DB/OCR/deploy/commit/merge/push.

## Root Boundary

Failure class: old reads/command chains restore another PDF/order/session's review, replace manual points, recapture a later revision, discard newer input or finish another operation's busy/loading.

Capture original rendered order/document/route, real browser session, context/operator-input/editor generations and original CAS base before dispatch. Compare against current refs rather than recapture a newer display. Operator edits advance workingInputGenerationRef; command preparation is not a new operator edit. Preserve existing editor CAS bases.

Read adoption checks full input ownership; loading completion separately checks original request identity. Sheet edit without successor ends only original loading. Successor loading is preserved. Auth/route events, target changes and unmount retire old results. Review/PDF rendering is source-owner gated even before passive target reset. Target reset cannot erase manual edits made since that render.

Shared command adoption requires actual response order_id and positive revision newer than original base. Versioned mapper/intermediate flushes plus _finalize_workflow_mutation can advance revisions more than once: no blanket plus-one assumption. Quad PUT additionally requires exact base+1 before adoption. Missing/wrong order, invalid/equal/decreasing revision and explicit workflow:null do not supply a new base.

## Actual Caller Inventory

All UI callers are in `frontend/src/pages/orders/[id]/workflow-v2.tsx`.

| Entry | Actual boundary |
| --- | --- |
| loadQuadReview; Step 1.5 effect/re-fetch | Exact document_id, response/error full owner, original-request loading cleanup. |
| saveQuadAndRerun; manual/estimate buttons | Original source_receipt/points/If-Match, own PUT revision only for original PDF OCR POST. |
| loadHeaderAxisReview / loadRowAxisReview; effects/re-fetch | Shared loadAxisReview; exact document, original timeout, PDF/order/receipt checks, independent request loading. |
| saveHeaderAxisAndRerun / saveRowAxisAndRerun | Shared saveAxisAndRerun; original coordinate_space/points/header count confirmation, receipt/base/owner, own PUT revision for OCR. |
| Quad click/manual/clear; header/row move/add/delete/reset | Existing geometry/beginWorkflowEditorEdit; operator edits retire old result adoption. |
| Three setSelectedDocumentId callers | Two recorded-current synchronizations and explicit PDF picker; synchronous context/request retirement. |
| saveFacilityTemplateColumns | executeWorkflowMutation(templateColumns), original If-Match, guarded own result/draft discard/refresh/status/catch; own saving completion. |
| updateExpandedCellCopyMode | executeWorkflowMutation(expandedCellCopy), original payload/If-Match, guarded own result/discard/refresh/message/error/cleanup. |
| runOcr | runAction + executeWorkflowMutation(ocrRun), explicit original PDF/options; missing PDF stops. |
| runAction | Original input/context/op/busy before action, after action, refresh and error recovery; own busy completion only. |
| refreshAll / loadFacilityTemplateStatus | Captured read owner plus passed command guard; independent request identity. |
| Initial refresh / OCR polling effects | Actual refreshAll; initial read lease and effect lifetime guard errors. Poll subscription pins order/PDF/route/auth and exact serialized ocr_job_id and workflow_attempt_id, not operator-input generation. Each tick captures its own current read lease. Old in-flight input replies/errors are rejected; next same-job/same-attempt tick resumes after editing. Timer cleanup retires its callback, not a newer effect. |
| Original PDF effect | Exact document; delayed response/error guard; auth/route/unmount retirement and own URL revocation. |

Four OCR entry paths remain: quad, header, row and ordinary runOcr. Receipts retain unknown/null/zero fields. Quantities, geometry, recognizer choice and existing valid success actions remain unchanged. Other workflow commands retain APIs, with shared workflowMutationConfig/adoption ownership and response-identity checks.

## Backend Source Proof And Prerequisites

Read-only inspection of writer-active `hospital-h2-revisions`, not frozen backend acceptance:

- `backend/src/api/orders.py`: quad/header/row GET document_id and PUT source_receipt + required If-Match. Ordinary columns/copy/OCR retain If-Match/operator authorization. Header/row contracts appeared during this read-only turn; the final source was re-read and connected.
- `backend/src/services/order_workflow_v2_service.py`: get_quad_review and _axis_source_receipt capture actual selected PDF/version, input/config digests, order identity and revision; _validate_axis_receipt and correction saves check/persist actual receipts. UI never rebuilds them.
- `backend/src/services/workflow_attempt_guard_service.py`: capture_selected_pdf_receipt supplies actual PDF/document/order/message/storage/SHA/generation.
- `backend/src/services/order_service.py`: build_hakodate_header_axis_review/build_hakodate_row_axis_review use receipt/captured input and return runtime_item saved_override. Final read confirms _normalize_operator_header_axis_override/_normalize_operator_row_axis_override now echo deepcopy(value["source_receipt"]). The earlier absent-receipt producer residual is resolved in observed source, not runtime-proven. Integration must carry this actual echo and stable digest contract. No backend edit here.
- Final read also found actual geometry_source_digest producer and quad/axis receipt emission. It hashes selected PDF, configured input, facility/week identity, page, authored layout and authoritative physical menu entries/row IDs; it does not hash workflow revision/status/meta. Integration requires this field in both current GET receipt and actual persisted override receipt. Missing/null/empty stable proof explicitly prevents saved-point adoption.

Quad/axis saved points require stored receipt selected_pdf_receipt, selected_version_input, geometry_source_digest, configured_input_digest and order_input_identity to match the selected read. Full input_digest is deliberately NOT the geometry equality key: own correction changes workflow revision/meta and thus this mutation digest. The original complete input_digest/source_receipt and If-Match still go unchanged to correction PUT. Absent keys differ from explicit null. Stored revision/provenance is never rewritten from GET. Missing/mismatched proof is not inferred from the GET PDF. Old/missing backend receipt blocks correction/rerun rather than defaulting to current PDF.

## Source Cases And Remaining Proof

Captured-schema alignment: the backend fixture now calls actual `workflow_attempt_guard_service.capture_reparse_read_input` with real Order/OrderWorkflowState model objects and explicit session/config/menu adapters. It checks the entire producer key catalogue (order/config/physical entries/lines/workflow/evidence/draft/cache/document/decisions) and both model-column catalogues; no hand-authored partial capture or empty-menu substitution remains in normal cases. The authored physical entry includes name/menu_name/date/daypart/slot 0/category null/order/quantity 0 and is copied into actual GET receipts. Empty/null physical menu and absent/null slot are explicit stop definitions for quad/header/row, preserving revision/meta. The normal definitions exercise GET -> PUT -> actual OCR override normalizer -> GET for all three axes. Quad normalizer's second return value is its actual source label, not an error code.

Limits remain explicit: the axis heavy render/manifest builder is doubled; actual workflow GET/PUT receipt producers, captured schema, geometry hash, save/finalizer/serializer and OCR normalizers are not. Session query adapters author no lines/decisions/evidence/draft/cache/document in this fixture; there is no SQL, real PDF, full OCR rerun, heavy renderer or DB ownership proof. Tests require the final sibling capture schema and strict physical-row producer integration. Missing the capture key itself is not silently repaired; the real capture producer always emits it, and the catalogue assertion detects schema drift. No runtime execution has occurred.

Latest source contract: `_serialize_ocr_job` emits recorded `metrics.workflow_attempt_id`; reused `OCR-{order_id}` is not a unique attempt. Polling requires both nonempty recorded IDs. Dispatch, in-flight response/error and subsequent tick are retired when either identity changes. Tests isolate same-job attempt retirement, changed returned attempt and missing attempt, using actual effect/timer/refreshAll. Fresh input leases apply only to reads for the exact pinned attempt, never to mutation CAS bases.

The geometry fixture now authors `fax_template.page: 1`. Actual quad/header/row GET producers reject absent, zero, negative, boolean, string and fractional page values with `geometry_template_page_required`. Header/row roundtrip definitions call actual GET receipt producer, decorated save, finalizer/serializer and actual override normalizer; only the heavy render/manifest builder is an explicit adapter double. They retain zero coordinates and exact stored provenance while full mutation digest changes. They do not prove the heavy builder, SQL concurrency or integration execution. Parent integration must include authored-page validation, stable geometry digest, axis receipt echo and recorded attempt serialization from the final backend source.

The earlier numeric case-count claim was not a fully enumerated count and is withdrawn. `frontend/tests/h2-quad-owner.test.cjs` contains source definitions only, not execution evidence:

- Normal quad/header/row source/CAS chains; ordinary columns/copy/OCR; null/zero/unknown preservation.
- Independent PDF/order/route/auth/manual/sheet/unmount retirement on read/PUT/POST success/error; busy changes only in its dedicated dimension.
- Competing reads/commands, successor busy/loading, original loading completion without successor.
- Missing auth/PDF/receipt and foreign saved PDF/input/config/version/order proof; dirty manual points retained.
- Missing/wrong response order; invalid/equal/decreasing/jumped quad revision; generic multiple-flush revision accepted.
- Actual header/row wrappers/shared callbacks, missing receipt blocker, original PDF/receipt after manual correction.
- Actual columns/copy/runAction/runOcr late paths; no stale draft discard.
- Actual PDF effect delayed success/error and auth/route/unmount URL revocation.
- Stable geometry equality across changed full mutation digest; missing stable proof blocks. These frontend receipts are protocol fixtures, not claimed real backend output.
- Actual initial-refresh/polling callbacks using actual refreshAll (not an always-success refresh stub), retired/current rejection, queued own job with zero attempts, and superseded request failure.
- Actual timer/effect/refreshAll: manual or sheet edit retires old response/error, next tick advances the same job and attempt without draft reset or dirty CAS-base change; auth/PDF/order/route/job/attempt/cleanup/unmount stop old reads. Returned different queued job is not adopted under old-job ownership; polling explicitly stops. Missing actual job or attempt ID is not guessed; transient same-job read failure allows the next tick.

`backend/tests/unit/test_h2_quad_geometry_roundtrip.py` defines real get_quad_review -> save_quad_review_decision -> get_quad_review calls and real hash/provenance/meta/finalize/serialize helpers. It asserts changed full input_digest, stable geometry_source_digest, exact stored receipt, original zero points and own revision. Source-change neighbors cover PDF/config/week/physical-menu changes. Storage/raster/session/source-read adapters are explicit unit-test doubles; no SQL CAS, transaction or real PDF execution proof is claimed. This test requires the sibling backend producer to be integrated; it contains no skip or legacy field supplementation.

Frozen paths: `frontend/src/pages/orders/[id]/workflow-v2.tsx`, `frontend/tests/h2-quad-owner.test.cjs`, `backend/tests/unit/test_h2_quad_geometry_roundtrip.py`, this document. Hygiene: git diff --check and scoped staging only.

Polling body checks expected ocr_job_id and workflow_attempt_id before any response adoption. workflowJobRef is updated from actual rendered/own read workflow identities, so old subscription cannot dispatch after a new job or same-job new attempt appears. A fresh read may update displayed workflow revision; the existing dirty editor baseRevision is never changed by that read. Only validated own command responses advance that command base.

Unverified: source-case execution, TypeScript/build, React/StrictMode and timer timing, real auth/route/PDF/input/job races, final backend SHA integration (including emitted/persisted geometry_source_digest and echoed axis receipt), actual SQL CAS/revision transitions, OCR/quantity parity and staging. Backend physical-row null-slot fallback is assigned separately; this UI does not guess its structure. No overall H2/runtime/staging completion.
