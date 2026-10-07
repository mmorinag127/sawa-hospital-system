# H2 Captured Recognition And Proposal Inputs

Source-only batch on `codex/modernization-h2-workflow-revisions-20261006`, base
`d3d39bf3198f3ba631c82c7c8bd396323dcd2484`. Existing staged changes retained.
No runtime, tests, build, DB, network, commit, merge, push or deployment performed.

## Actual Caller Inventory

| Entry / producer | Calculation input | Result boundary |
| --- | --- | --- |
| Pipeline input event -> correction -> Yomitoku output | Generation-bound transport plus canonical selected-PDF receipt | `page_correction_artifacts.recognition_input` declares original identity only when actual recognition bytes equal original bytes; changed bytes require actual uploaded corrected artifact SHA and parent SHA |
| API manual reparse / auto-LLM / pending reparse recovery -> `reparse_order` -> `_resolve_reparse_llm_pdf_bytes` | Own returned pipeline declaration, exact original processing-start receipt | Original identity is verified, not a missing-correction fallback; corrected declaration must equal actual corrected artifact receipt; explicit operator corrected request still requires corrected artifact |
| POST sheet auto-edit, `wait=true` -> `propose_sheet_auto_edit` | Provided If-Match checked before provider; captured evidence/draft/cache/order lines/PDF/config | Same input CAS before returning proposal; missing PDF/render failure stops before provider |
| POST sheet auto-edit -> `start_sheet_auto_edit_job` -> background `run_sheet_auto_edit_job` -> shared proposal function | Queue-time snapshot retained in real SAE job metadata; only own reservation advances its revision; supplied sheet hash/model/use-LLM must match own request | Own revision/job/input fence before terminal metadata write; stale job/input cannot publish done; source-body failure may write failed-only metadata through exact original input CAS, never proposal result |
| GET quad-review -> selected document estimate -> PUT quad-review | Explicit selected OrderVersion or unique current selection, exact linked UploadedPdf SHA/generation, authored config and full order input digest | PUT requires returned receipt, original revision, unchanged selected Version contents and selected PDF; approved estimate coordinates equal declared estimate; manual coordinates remain operator input with same source fence |
| H2 workflow page quad handler | Selected document ID sent on GET; returned receipt and revision sent on PUT | OCR-runs receives revision from its own successful PUT result, not a latest read |
| Workflow queue -> `capture_reparse_read_input`; nonworkflow registration -> `capture_nonworkflow_execution` | Raw full authored facility JSON and raw ingest-policy JSON captured with start input | Workflow actual write guard compares current raw source with original snapshot; nonworkflow full-input CAS compares it too; own business writes cannot advance to a changed config |
| Firstpass `ingest_worker._process_ingest_captured`, reparse, auto-LLM, recovery, pipeline dispatch and nested config readers | Owned config/policy access returns deep copies of captured start values, including zero/null/unknown keys | Reparse removes cache reclear and late policy reads; parser receives captured policy; pipeline validates explicit template IDs against captured config, not mutable facility cache |
| Synchronous OCR table LLM review and submitted-sheet application | Their existing pre-calculation synchronous snapshot now includes full authored config/policy | Template uses that snapshot; shared synchronous actual write CAS compares raw authored source before adoption |

The frontend routing edit is confined to this WT's `workflow-v2.tsx` quad handler.
No H2UI074 or H1 worktree edits or integration were made. Integrators must retain
the selected-document query, returned receipt and own-result If-Match propagation.

## Source Cases Added, Not Run

`backend/tests/unit/test_h2_captured_proposal_inputs.py` invokes actual consumer,
proposal runner, strict render loader and shared write fence functions. Cases cover
declared no-op original, actual corrected child, explicit corrected missing stop,
missing declaration, sync/async stale tokens without provider/write, replaced PDF
without render/adoption, changed policy rejection, captured policy deep-copy,
async own completion and quantity zero. Pipeline tests invoke the actual producer
receipt helper for no-op, actual corrected and changed bytes without artifact.
An additional pipeline source case passes the actual no-op producer declaration
into the actual backend reparse PDF consumer and asserts exact bytes/receipt.
These are source definitions, not runtime or concurrency proof. Existing business
assertions were not weakened.

## Residuals

- 193 legacy normal-unit caller/revision migrations and obsolete fallback
  expectations remain explicitly deferred to the technical-test phase.
- This batch has not been executed, typechecked, tested or deployed. DB
  concurrency, serialization and frontend/API integration remain unverified.
- No claim of full H2 completion or normal Hakodate quantity-flow success;
  structure-only firstpass remains diagnostic under the existing contract.
