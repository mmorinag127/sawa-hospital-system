# H2 Selected PDF Processing-Start Receipt

Source-only unit. Branch `codex/modernization-h2-workflow-revisions-20261006`,
base `d3d39bf3198f3ba631c82c7c8bd396323dcd2484`. Existing staged changes retained.
No runtime, tests, build, DB, network, commit, merge, push, or deploy performed.

## Invariant

The selected OrderVersion or current OrderDocument identifies exactly one
UploadedPdf by message and storage URI. The UploadedPdf's full original SHA256
and any already-authoritative generation form the processing-start receipt.
Missing, ambiguous, or malformed digest proof stops; a later download cannot
create this proof. Historical GCS inputs with a complete authoritative digest
and no generation remain valid only when the downloaded bytes match that digest.
New manual uploads pin the generation returned by their actual upload response.
There is no inferred/backfilled generation for older unpinned inputs.

Every body download uses an authoritative generation when available and is
digest checked. Before recognition
and actual persistence, the original source is checked again for same-name
replacement. Current GCS metadata is compared to the captured generation only;
it never supplies a replacement generation. Local-source comparisons hash the
actual original file. Verified bytes are carried with their receipt. PDF
correction receipts contain their own digest/generation and original-parent
digest; raw/overlay substitution on missing correction proof is removed.

## Actual Caller Coverage

| Entry/caller | Processing-start capture and actual boundary |
| --- | --- |
| uploaded_pdf_service -> ingest_worker normal firstpass | Existing early registration creates the exact document; capture_nonworkflow_execution binds UploadedPdf proof before computation; load_selected_pdf_bytes feeds pipeline and configured recognizer; common actual-write guard verifies document/upload/source inside the owned transaction. |
| ingest_worker recovery/resubmit | Original nonworkflow boundary retained; original selected source read by its captured receipt, not transport URI/latest source; transport input retains its own generation and hash. |
| order_service reparse / ROI pipeline | Queue or explicit nonworkflow start captures selected version and receipt; main PDF load uses that receipt; FAX and pipeline entrypoints validate the same verified bytes. |
| order_service direct Hakodate rerun | One strict original download; native manifest receives verified bytes and materializes them once, without URI reread. Prebuilt unbound manifests stop. |
| order_service reconcile firstpass / rerun recovery / auto-LLM | Original execution receipt remains in immutable own-job metrics/boundary; recovery mapping loads exact original bytes; callback/evidence/terminal writes use existing atomic owned guards. |
| synchronous operator review / cache / draft / revision writes | Original synchronous input snapshot contains selected PDF receipt in input_versions; shared synchronous CAS verifies exact document, upload row, and original source before adoption. |
| native header/row-axis review and grid detection | Receipt captured at their explicit source-read entry, before template/grid computations; original bytes verified before native registration. |
| artifact repair | Explicit synchronous receipt or original async execution receipt propagated into native quantity construction; no calculation-end source recapture. |
| fax_extractor -> OpenAI/Gemini/pipeline, including audits/retries | Shared FAX entry plus actual provider/pipeline entry verifies original or explicitly parent-bound corrected bytes; recognizer algorithms and raw quantities unchanged. |
| ocr_pipeline upload -> app event handler | Selected receipt goes into transport metadata; pinned event body digest and original GCS generation checked before recognition and output persistence; corrected artifact receipt uses upload response generation. |
| evidence persistence | Original receipt retained in _ocr_execution from the already fenced own row/boundary; firstpass/reparse recognizer sidecars retain the actual original/derived receipt; no current source selection. |
| selected document GET and raw PDF link | Exact selected version/document-to-upload proof is read before the body. Raw PDF links use the selected document download route instead of unverified direct latest-object URLs. Missing selected source stops with 409, not a current OCR/archive replacement. |
| source failure callbacks/recovery | Failure-only guard retains revision/attempt/start-input CAS and permits no evidence or quantity adoption. Nonworkflow document/job failure receipt is atomic; workflow failure uses existing atomic workflow/job completion. Superseded owners cannot terminalize. |

Repository search also finds offline `run_vlm_ocr_local.py`,
`run_hakodate_visual_verification.py`, and `rerun_compare_stg_hakodate_targets.py`.
They are not production adoption callers. Independent VLM diagnostics remain
available without fabricating a job or canonical evidence. Owned FAX/provider
calls require a captured receipt; attached derived receipts must match the
execution's original parent receipt, even after copies.
Offline native manifest tools do not write canonical order evidence.

## Source Cases And Residuals

Focused behavior source covers valid original bytes, same-URI replacement,
missing/invalid digest before read, missing/changed/mismatched generation,
pipeline dispatch rejection, raw operator receipt, correction-parent absence,
actual DB-write document/upload/source comparison, no native URI reread, and
unbound manifest rejection. Existing configured-provider zero/null/raw-result
assertions remain, with a real source fixture. Pipeline source tests cover
original receipt/digest/generation and corrected upload response receipt.

Generation query consumer audit: storage loader parses URI query separately;
signed URL signing receives generation separately from object_path; operational
archive export and native path materialization use the shared loader; pipeline
URI parsing uses urlparse.path; frontend OCR-queue HTTP conversion keeps the
query in the resulting HTTP URL rather than encoding it into the object name.
Original-document GET no longer replaces a missing selected file with current
OCR transport/archive input. The unused legacy overlay fallback selector has
no repository callers and was removed. Explicit unsupported variants stop,
never silently become raw. PNG/grid/rectification preprocessing retains its
existing algorithms; corrected PDF bytes carry a parent-bound artifact receipt.

Source verification performs external reads while ownership locks are held,
because the actual persistence decision must reject an intervening source
replacement. Selected-source SDK verification uses a 30-second per-request I/O
bound and disables SDK retries, not a new business retry policy. It makes no
reverse DB-lock acquisition in the storage service. Transport/recognition reads
remain outside actual persistence transactions. Source I/O failure raises
selected_pdf_verification_failed: the failed transaction exits/rolls back before
a failure-only owned completion transaction starts. No recognized value can be
committed by that completion. Runtime latency/deadlock behavior is unexecuted.

Additional focused source cases cover historical digest-only compatibility,
signed URL generation, real corrected bytes plus changed original parent,
independent diagnostics, and current/superseded atomic source-failure receipts.

All cases are unexecuted under the source-only prohibition. The 193 legacy unit
caller/fixture migrations and fallback-expectation migration remain deferred.
Historical sources missing an authoritative full digest need explicit operator
resolution; no guessed migration is supplied. Document-download signed/archive
fallback expectations were changed into explicit negative cases; the normal
selected-version case retains PDF bytes, document ID, and version assertions
with its explicit captured receipt.
Hakodate structure-only
firstpass remains diagnostic, not recognized quantity success. This unit is not
overall H2 completion or proof of a normal full operator flow.

This unit changes fourteen production files, five focused test files, and this
audit note. They are storage_service, manual_upload_service, shared attempt
guard, workflow queue/completion service, order_service, ocr_job_service,
ocr_evidence_service, FAX/OpenAI/Gemini/pipeline entrypoints, ingest_worker,
orders API, and ocr_pipeline/app/main.py. No integration test files or sibling
worktrees were edited by this unit. Existing staged integration source is kept.
