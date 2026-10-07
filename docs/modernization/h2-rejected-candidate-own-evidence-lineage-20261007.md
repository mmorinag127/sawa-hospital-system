# H2 Rejected Candidate Own-Evidence Lineage

## Source And Verification Boundary

- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h2-revisions`
- Branch: `codex/modernization-h2-workflow-revisions-20261006`
- Base: `d3d39bf3198f3ba631c82c7c8bd396323dcd2484`
- Existing staged implementation and test source are preserved.
- This record is a source audit, not runtime evidence or overall H2 completion.
- No runtime, tests, build, DB, OCR, server, commit, merge, push, or deploy was performed.

## Production Caller Audit

| Entry / caller | Actual persistence and selection boundary |
| --- | --- |
| API manual / LLM / non-LLM reparse callbacks | Pass captured real job, attempt and queue revision into reparse; completion receives the actual returned evidence ID on success, error and crash when available. No latest evidence lookup. |
| `_process_ingest_inline` -> `_process_ingest_captured` | Registers the real first-pass job, then captures its execution generation and order/workflow read inputs before PDF loading, recognition and quantity parsing. The original returned pipeline dict is retained even when unusable. No order/job identity is guessed at persistence time. |
| `create_order_from_ingest` -> `_persist_ingest_pipeline_evidence` | Actual Order/document/version/line writes and own evidence insertion share one fenced Session. An existing target must match the processing-start input; an absent target may enroll only the Order this execution actually inserts. Evidence failure rolls back the business writes. The worker receives `first_pass_evidence`, not a latest evidence query. |
| Pending first-pass / `run_ocr_job_recovery_once` | Registration is not an OCR result: no computed lines are adopted without the current pipeline result. Recovery passes the start contract saved on that real job, including generation and input versions. Missing legacy contracts stop visibly as `ocr_execution_input_required`; no current workflow token is reconstructed. |
| `_enqueue_auto_llm_reparse` -> `_run_auto_llm_reparse` | Passes the actual first-pass job, its captured/own-advanced input contract and the returned own evidence payload into the thread. No latest-order-job choice or synthetic reparse job. Recognition/provider behavior is retained. |
| `reparse_order` accepted result | Same guarded Session persists current pipeline evidence before existing OrderLine writes. Own evidence ID is retained on subsequent cache/archive failures. Recognition and quantity acceptance logic are unchanged. |
| Reparse empty lines / validation rejection / missing first pass / provider rejection / OCR exception | `_rejected_reparse_result` saves only the current pipeline payload as diagnostic evidence. Missing payload or failed persistence returns explicit failure, not old cache recovery. |
| `_save_reparse_candidate_as_draft` | Same Session fences own evidence, `_append_edited_ocr_revision`, and review draft. Own job/attempt/revision/evidence lineage is stored; `adopted=False`. Rejected review remains available without becoming a selected sheet. |
| `_publish_applied_reparse_revision` | Receives the actual own evidence ID and captured real job. Appends a pipeline-result archive with `lines_applied=True`, `adopted=False`, and awaiting-selection state. Does not write `_edited_ocr.latest` or adopt a revision-list candidate. |
| `_append_edited_ocr_revision` -> `ocr_revision_store.append_revision` | Sole production append caller. Caller-owned Session holds the actual ownership fence through insertion. Async archives require exact own evidence provenance; synchronous operator saves require their pre-calculation input snapshot. |
| `save_ocr_sheet_exact` | Keeps the previous pre-calculation snapshot contract; append/cache/draft writes share the fenced Session. Writes an explicit selected revision pointer and base evidence. |
| `_select_order_sheet_revision` and payload revision selector | Use explicit workflow evidence and operator-written selected revision ID/base evidence. Exact lookup only; async/rejected archives cannot be silently selected. Revision lists remain history-only. |
| `review_ocr_table_with_llm` -> patch candidate persistence | Captures authoritative workflow/evidence/draft/cache/order/lines before computation. Same write Session validates that snapshot and exact base evidence/draft before candidate insertion. |
| Explicit patch application | Requires explicit candidate ID and pre-calculation snapshot; same Session fences candidate/draft insertion and applied marker. No latest-candidate default. |
| Draft persistence / selected draft read | Validate actual evidence/template/order and async execution provenance. Diagnostic/rejected outputs may form review drafts, not canonical adopted sheets. Selected draft follows exact workflow draft/evidence IDs. |
| `mark_ocr_run_completed` / `select_ocr_result` | Completion requires captured revision plus attempt and actual own evidence provenance. Superseded completion mutates neither job nor workflow. Selection uses explicit evidence; diagnostics, cache mirrors and malformed lineage stop visibly. |
| Evidence-only rerun API / service | Receives real captured job identity; returns own persisted evidence on success and downstream error. Unusable own output is retained as diagnostic evidence, not adopted. |
| Legacy nonworkflow rerun / LLM / first-pass reconcile | Real job is passed to own evidence persistence. Workflow-owned recovery is not reconstructed from current job tokens. Invalid current outputs retain own diagnostics and failed state; missing output/persist failure does not adopt latest evidence. |
| Workflow/read/inspection projections | Resolve selected workflow evidence and matching selected draft. Rejected candidate history remains separate from current adopted materialization. |
| Cache mirror | Persists only unmixed own input in the caller's fenced Session, marked nonselectable. Preserved operator cache artifacts are not evidence for the new attempt. |
| `_build_current_sheet_context_uncached` -> current-state upsert/delete | Captured execution ContextVars reach the actual current-state Session. Guard is held through upsert/delete; post-evidence invalidation also passes the explicit job/attempt/revision. Superseded projections cannot replace or remove the new current state. |
| Workflow projection / critical-choice synchronization | Nonworkflow projection writes validate the same start input before their actual Session writes. Critical decision input versions are included; operator critical-choice writes participate in the parent Order lock. Workflow-owned projection is read-only so it cannot advance its own immutable queue revision before completion. |

`ocr_revision_store.get_latest_revision` and cache-backfill helper have no production adoption caller in the audited source. History listing is not an adoption gate.

## Processing-Start Contract And Write Fence

The worker scope was explicitly authorized in the follow-up payload. Both previously reported production source gaps are connected:

- `capture_nonworkflow_execution` claims a generation on an existing real job. It does not fabricate a job, workflow attempt, or evidence ID. Missing jobs and workflow-owned jobs cannot be claimed as nonworkflow.
- A linked order is captured explicitly. Without a linked order, the worker resolves the existing registration keys (message ID, or provided facility/week) before recognition. A new exact source uses normal placeholder registration to obtain its real Order ID before calculation. Capture refuses an absent target; it never reads all Orders. Multiple matching registration targets stop instead of choosing the first row. The explicit registered target remains fixed after recognition.
- `workflow_exists=False` at processing start is an explicit contract, distinct from missing input. Persisted workflows carry their actual positive revision and exact input snapshot. A workflow-owned target cannot be written with missing captured workflow tokens.
- The actual guard locks the parent Order, workflow when present, then the real job in the same transaction. PostgreSQL uses actual row locks; SQLite uses conditional no-op updates. The parent Order lock also serializes absent-workflow writers with workflow bootstrap. Evidence/draft/cache/line/decision input versions are compared under the actual write fence.
- Replacing the workflow, changing operator input, replacing a nonworkflow execution generation on the same job, switching to another workflow job, or removing the required job rejects the old execution's actual writes. A missing job cannot be confused with an existing null-attempt job.
- `start_orders` contains immutable target reference, explicit workflow boundary and SHA256 of the start input, not a duplicate full snapshot. Only one target's comparison values are retained in `orders`. Expected inputs advance only through this execution's own already-fenced writes; rollback/transaction-close restores the in-memory contract. There is no calculation-after-the-fact latest input capture.
- Actual persisted evidence records the real job, captured workflow tokens where applicable, nonworkflow generation, explicit processing-start input and its own inserted OEV ID. Revision insertion and draft persistence compare this provenance to the captured execution, including generation, not just null attempt/revision values.
- Heartbeat stage threads copy the captured ContextVars. Workflow progress uses captured real order/job/attempt/revision; terminal completion retains its existing atomic refusal rules.
- Explicit operator draft application no longer fabricates `OCR-{order_id}` or marks an unrelated OCR job done. It keeps the pre-calculation CAS and references only the explicit selected evidence lineage.

No known unconnected actual-result caller remains in this bounded source audit. This is source implementation completion only; it is not runtime proof or overall H2 completion.

## Edited Source Files

This follow-up changes `backend/src/workers/ingest_worker.py`, `backend/src/api/orders.py`, and these ownership-related services:

- `workflow_attempt_guard_service.py`
- `order_service.py`
- `ocr_evidence_service.py`
- `ocr_job_service.py`
- `ocr_revision_store.py`
- `draft_sheet_service.py`
- `order_workflow_v2_service.py`
- `workflow_state_service.py`
- `order_current_state_service.py`
- `critical_decision_service.py`

Previously staged candidate/selection source and synchronous pre-calculation captures are preserved. No test source, recognizer engine, numerical recognition algorithm, integration branch, or deployed data is changed by this follow-up.

## Bounded Target Correction Source Audit

- `uploaded_pdf_service.build_ingest_payload` passes its actual current Order/document and upload identity; uploaded recovery registration uses that real upload row. No capture change is required at this producer.
- `_process_ingest_inline` resolves the existing message/provided facility-week registration keys before calculation, or registers the exact new source with `lines=None`. The resulting real Order ID is passed to capture and remains the actual persistence target after recognition.
- Registration serializes matching keys with PostgreSQL transaction advisory locks; SQLite obtains a write lock on the actual upload or actual job. Existing target writes take the parent Order lock. New insertion uses normal registration, not a fabricated workflow/job/evidence.
- Reparse and evidence-only rerun already supply explicit real Order IDs. Recovery carries the saved bounded start contract; old full-read-set contracts fail the common write guard and are exposed as missing execution input, not refreshed after recognition.
- Source cases audited: linked upload, new upload, existing message duplicate, provided facility-week duplicate, legacy first pass with a real job, explicit rerun/reparse, recovery, changed workflow/operator input, missing target and old unbounded contract. These are source checks, not executed tests.
- This correction changes worker, order service, common guard, job blocker projection and this record only. Previous staged source/test changes remain intact; no runtime or 193-caller migration was performed.

## Deferred Technical Phase (Unchanged)

## Registered Document Continuation Correction

- The actual input snapshot now includes the current document's columns. The common DB-write fence rejects status/source/document edits as well as Order/workflow/job-generation changes.
- `create_order_from_ingest` distinguishes its captured current processing document from upload duplicate history. Current source ID, storage URI, captured document ID and processing status must match. Completed duplicates and switched inputs stop; document IDs and duplicate checks are retained.
- A valid continuation reuses that same document and reaches the existing quantity/OrderLine application block (including an explicit empty list and zero quantities), document status update, current version update and own evidence insertion in one fenced Session. It does not create an extra document or Order.
- `first_pass_result_applied` is returned by the actual application and retained in own evidence metadata. The worker cannot mark selectable evidence successful without this result. Missing lines form diagnostic evidence, not an adopted successful result.
- Pending output retains only this execution's computed application payload/lines in its bounded contract. Reconcile applies that own computation through the same service before evidence/cache completion. Missing computation persists diagnostics and fails; old evidence without application provenance cannot prove success.
- Auto-LLM receives only the own evidence after the application's success gate; rejected evidence remains review-only. Existing-target new-document application uses the same existing line-write block and start fence.
- Audited adjacent source cases: early registration followed by normal first pass; completed duplicate; switched current document; old execution generation; rejected partial output with unchanged original lines; absent own output; explicit empty lines; zero-valued quantities; pending/recovery; auto-LLM; existing target. No cases were executed, and the deferred 193 tests were not migrated.

## Explicit Non-OCR Terminal Correction

- Explicit operator skip and the existing backlog-skip branch carry `document_status=skipped`, no pipeline/evidence and zero OCR attempts. They reuse the captured processing document through the same continuation boundary, not the duplicate early return.
- That fenced Session updates document status/version and the actual job together. Job status uses the existing terminal `done` vocabulary, but its receipt and metrics explicitly say `result_state=skipped`, `adopted=False`, with the real job/execution/document IDs and skip reason. No OCR evidence or quantity application is fabricated.
- The worker requires the actual skip receipt, finalizes registration side effects, then returns without OCR context suggestion or auto-LLM. Non-OCR registration is not normal OCR verification.
- Pending output remains processing/awaiting output with its own captured application input, not a terminal skip. Valid empty/zero results still take the normal line/evidence application path. Rejected current payloads remain diagnostics.
- Missing output and worker crashes attempt an atomic own document/job failure receipt through the same actual write guard. Switched input, completed duplicates and superseded generations cannot write that receipt; the original exception remains visible. A downstream failure after completed application does not rewrite the completed document via this receipt.
- All worker terminal returns were source-audited: skip, pending, invalid payload, valid empty/zero result, completed duplicate, missing pipeline and BaseException. No runtime/DB/test execution or deferred unit migration was performed.

## Atomic First-Pass Terminal Correction

- `create_order_from_ingest` now commits lines/document/version/current-state invalidation, actual returned evidence, real job terminal status/metrics and completion receipt in the same guarded Session. `_complete_first_pass_job_in_session` revalidates the same actual owner before changing the real job, without opening or committing another Session.
- Normal and rejected worker branches consume the returned job/execution/document/evidence receipt. Their old separate `update_job` terminal call is removed. Pre-persistence OCR output/error progress stays nonterminal; it cannot declare successful completion before application.
- Reconcile uses the same registration/application transaction for accepted or diagnostic current output. Recovery cache writes accept that caller-owned Session; failed cache save aborts the whole application/terminal transaction. Missing own computation forms diagnostic failure, never success.
- Already-persisted completion is checked using that job's explicit own receipt under the actual guard; reconcile does not re-adopt evidence or issue a second terminal update. Recovery exhaustion for first pass uses the same fenced document/job failure receipt.
- The worker crash handler no longer issues a separate failed update after its receipt attempt. A postcommit side-effect failure cannot overwrite completed job status through that handler. Skip remains atomic, pending remains nonterminal, and rejected own evidence retains failed/review-only terminal metadata.
- Added `backend/tests/unit/test_first_pass_atomic_receipt.py`: focused behavior source calling the actual terminal helper for own success/zero quantity, superseded refusal, mid-transaction failure rollback and rejected own identity. Its transaction double is not ORM/DB atomicity proof; execution and DB concurrency verification remain deferred. No 193-caller migration was performed.
- This follow-up changes order service, worker, the focused new test source and this record. All prior staged changes are retained. Runtime, DB, tests, build and commit were not run.

## Mandatory Own Resume Source

- Audited both production explicit resume callers: auto-LLM supplies its actual returned own persisted evidence payload; completed LLM recovery supplies the output loaded from its explicit real job output reference under the preserved execution boundary.
- `_resolve_own_resume_first_pass` checks the common actual owner and exact real evidence ID/job/attempt/revision/generation plus saved payload, or the explicit actual job output reference. Schema annotation uses the existing structured adapter and retains values including numeric/string zero.
- Explicit resume always uses this input-only decision, even outside LLM mode. Missing content, conflicting selected document, missing/mismatched lineage or incompatible schema return current-input diagnostic failure. It cannot reach `_load_existing_first_pass_payload_for_reparse`, active evidence, cache or latest/list selection as a replacement.
- The separate non-resume operator-selected first-pass path remains unchanged; no menu/quantity acceptance policy or recognizer was changed. Downstream reused-first-pass parsing receives only the validated explicit payload.
- Added focused behavior test source for incompatible own schema with old active present/absent, missing own content and compatible zero values. Tests were not executed. All previous stage is retained; 193 legacy migrations and runtime verification remain deferred.

## Omitted Resume Start-Input Correction

- Sole production caller of `_load_existing_first_pass_payload_for_reparse` is the requires-first-pass branch of reparse. It now receives explicit stop errors; the helper never calls current-active/cache output readers.
- Nonworkflow capture records the workflow's exact selected evidence reference and metadata digest before calculation. Workflow queue records the same reference under its workflow revision lock before reservation/clearing selection. Neither selects latest evidence or supplements captured attempt/revision.
- Loader checks the real job ownership fence, reads only that captured evidence ID, validates all saved row metadata/payload against its digest and existing selectability, then applies the existing schema adapter. Missing selection, changed metadata/wrong lineage, missing content and schema mismatch stop explicitly. Ordinary recognizer without requires-first-pass context remains unchanged.
- Added behavior test source invoking the actual loader with a correct explicit start source/zero values and changed source metadata, while forbidding active recapture. Supplied own-resume tests remain intact. No tests were run; 193 migration remains deferred.
- Changes: order service, shared guard, workflow queue service, focused test source and this record. Existing staged implementation, operator edits and recognition/quantity algorithms remain unchanged.

## Explicit Selected OrderVersion Input

- Initial document selection returns the exact selected OrderVersion columns. Nonworkflow capture checks that same row/content under the parent lock, before claiming its job. API version DTO and queue metrics propagate the exact row; queue rejects changed/missing explicit selected input before reservation.
- Workflow and nonworkflow common actual-write guards compare the captured selected version ID, URI, line snapshot and all metadata under row lock. Missing rows and any edits reject the write; current-document capture does not stand in for a selected historical version.
- Reparse and evidence-only rerun are the only postcapture document resolver callers. Both use `_captured_order_document_selection` and the actual guard, then the captured URI/metadata, never a new OrderVersion selection. The original resolver is used only for initial nonworkflow capture.
- Added focused source cases invoking the actual shared selected-version checker for normal/URI edit/line edit/missing, including unchanged zero/null; and the captured input reader for original URI/no re-selection and changed-input stop. No tests were executed.
- Changes are confined to order service, shared guard, necessary API/queue callers, focused source tests and this record. Existing stage, recognizer, quantities and 193 deferred cases are preserved. Runtime/DB/network/build/commit remain unperformed.

## Shared Reparse Week And Baseline Sources

- Audited full `_reparse_order_with_attempt` and `_rerun_ocr_evidence_only_with_input`, plus their input/baseline/week/projection helpers. Reparse no longer fetches current-active/cache OCR for week resolution, current sheet for LLM baseline/previous-applied comparison, latest draft for quantity preservation, or facility/global latest Order week hints.
- `capture_reparse_read_input` records workflow queue's actual Order/lines/selected draft inputs before calculation and downstream invalidation. Nonworkflow uses its existing bounded captured inputs. `_captured_reparse_read_input` validates the owner and returns that saved input, using the caller Session when already locked. Reparse uses captured received-at, week and lines; operator draft quantities are provided explicitly to the existing baseline adapter.
- `_resolve_reparse_own_week` accepts the explicit captured week or dates in own recognized lines/payload only. Missing/ambiguous dates stop as `week_unresolved`; it does not search other orders, received-month defaults, adjacent-month menus, old cache or active evidence. Shared week resolution in an async scope routes through the same captured input rule; projection does not read latest-order week hints in that scope.
- `_resolve_reparse_llm_baseline` now passes only own pipeline payload plus captured selected draft to the existing structured review adapter. The unused old structural helper, which read active OCR and other orders' latest week, is removed after checking it had no other production caller.
- Reparse archive publication receives own rows and captured operator row IDs, not a postapply current-sheet read. Rerun uses captured selected draft metadata and its actual returned own evidence override; mismatched persisted draft/evidence lineage stops instead of calling a latest-evidence resync helper.
- Retained source reads: authoritative facility/template/menu configuration, captured exact selected document/evidence, actual guarded writes, and output loaded from the current pipeline's own returned reference. Those references are not latest/cache substitutions. Non-async explicit operator read surfaces remain separate.
- Added focused behavior test source: missing week with active/cache readers forbidden, explicit start week with zero/null preserved, own recognized dates without received-month default, and captured draft/own payload baseline without late sheet. No tests were run; quantity parity and full functional/concurrency verification remain deferred, along with the 193 migrations.
- This correction changes order service, queue service, shared guard, the focused week source test and this record. Prior stage is preserved; no runtime/tests/build/install/network/DB/commit/merge/deploy was performed.

## Authored Reparse/Rerun Fax Template

- Actual acquisition inventory: reparse direct facility/template lookup; evidence-only rerun direct facility lookup; `_resolve_order_fax_template` used for payload annotation/cache/evidence; nested `get_facility_config` and `get_facility_config_for_template` calls within the reparse/rerun authored-template scope. All use the single raw JSON authored facility/template reader in that scope.
- `get_authored_facility_config` requires one exact facility and a nonempty authored `fax_template` with declared columns. It runs the existing facility config and template-column validators. Missing/null/empty templates, incomplete/invalid columns and ambiguous facilities stop visibly. It never calls the base/override merger, DB resolver, area/column normalization, row-field harmonizer or template-ID supplementation.
- The validator's field/role normalization is used only for comparison/error detection, not returned value construction. Shared facility validation now also checks authored fax columns using its existing column validator. Returned config is a deep copy of the raw JSON; zero indexes, zero geometry values, false flags and authored rows are unchanged.
- Main reparse base/override fallback and duplicate-facility first-match are removed. Rerun returns the authored reader's concrete error instead of continuing after config lookup failure. The annotation helper returns no template on failure, never a base template.
- Explicit request provider/model and existing LLM parsing controls remain request behavior, not persisted master rewriting. No recognizer algorithm, quantity/date/week policy, H1 JSON integration tree or deferred 193 cases is changed.
- Focused source cases invoke the real shared authored reader for missing/null/empty/incomplete inputs despite a usable base/override, ambiguous facilities, and unchanged present zero/false values. These tests were not run; operational completeness/parity still requires the deferred technical phase.
- Changed files: config service, existing config validator, order service, focused authored-template test source and this record. Prior stage is preserved. No runtime/tests/build/lint/install/DB/network/commit/merge/deploy was performed.

- 193 normal unit mutator callers and obsolete context-fallback test expectations remain deferred by explicit instruction.
- Existing test source changes remain staged; this unit does not bulk migrate or run them.
- Runtime behavior, quantity parity and adjacent superseded/error cases remain unverified. Whitespace `git diff --check` is not functional proof.
- Technical behavior cases must cover normal first-pass with a real non-alias job, absent-workflow bootstrap, changed workflow revision, a different workflow job intervening, replaced generation on the same job, missing required job, current own diagnostic result, missing own pipeline result, evidence/cache failure, rollback/crash, auto-LLM propagation and explicit operator selection of reviewable evidence. Their business quantity assertions must not be weakened.

## Global Canonical Template Acquisition Correction

This supersedes the context-local template design above. Failure class: canonical
template acquisition enriched an incomplete authored source with base, override,
DB columns, defaults or synthetic IDs outside the reparse ContextVar.

- Shared decision: `get_authored_facility_config`, `get_facility_config`,
  `get_facility_config_for_template` and direct `_build_facility_config` now
  validate raw JSON before returning a deep copy of the unchanged fax template.
  Missing/null/empty/incomplete, duplicate facilities, unresolved multi-ID
  selection and wrong explicit IDs stop. Validation does not construct values.
- ContextVar and its two order-service scopes are removed. Legacy area/schema/
  row-field normalizers have explicit noncanonical names and no production calls.
  Base/override merging remains only for non-FAX packaging and label settings;
  DB settings remain usable for those existing functions, never for fax values.
- Actual firstpass/ingest: worker template-ID lookup, initial extraction and
  facility extraction use the global getters. The shared `run_ocr_pipeline`
  validates canonical facility/template inputs before any pipeline side effect,
  covering worker firstpass/resubmit, order-service ROI/reparse and fax-extractor
  pipeline calls. Missing facility is an explicit error, not unknown classification.
- Reparse/rerun: direct authored acquisition, shared template materializer and
  canonical live manifest creation use the same reader. Automatic LLM
  large-cell/date-fill template enrichment is removed; explicit operator
  provider/model/prompt commands and the recognizer implementation are unchanged.
- Annotation/cache: submitted-sheet parsing and pages read require authored
  template; unresolved template stops cache publication and active-payload
  annotation. Independent own diagnostic evidence is not converted to a template
  or adopted sheet. Grid detection no longer borrows base/cache templates or
  injects grid bands; explicit operator grid inputs remain explicit.
- Cell estimation: manifest-embedded templates no longer bypass the getter.
  Hakodate assignment returns template errors as blockers and cannot assign
  evidence when canonical template resolution failed. Step-review acquisition
  and batch-cell acquisition use the shared selected-template getter.
- DB template-version resolution no longer catches canonical errors with DB
  config fallback, injects version columns/row fields/IDs into the fax template,
  or uses caller-provided fax config for bootstrap. Version identity remains
  separate metadata. Registration validates before any config DB mutation.
- Output-template consumers: order-form merged-cell detection, master workbook
  column acquisition and output diet-column mapping no longer use overrides as
  canonical columns. No workbook, date/week policy or historic selection policy
  was changed.

Production getter inventory (source search, no execution): order service,
ingest worker, candidate resolution, Hakodate step-review/cell-batch/assignment,
facility-template-version, workflow-v2, order-form, master-order-form-template,
output builder, total service, and orders/facilities APIs. The direct builder
calls are the two public config getters and template-version resolution/
registration. Config signature checks also use the selected-template getter.
No production call to the legacy fax normalizers remains. Config storage/editor
override validation remains noncanonical; its output cannot supply a fax template
through the shared getter.

Focused behavior source cases call real getters/builder, manifest reader,
pipeline entry and version resolver: missing key/null/empty/incomplete despite
usable base/DB/embedded templates; ambiguous facility/multiple IDs; wrong ID;
unchanged zero/false/null/unknown values; canonical version columns without DB
replacement. These declarations were not executed.

Changed in this bounded correction: config_service, order_service, ingest_worker,
facility_template_version_service, ocr_pipeline_service,
hakodate_cell_ocr_batch_service, order_form_service,
master_order_form_template_service, output_builder, focused
test_authored_reparse_template and this document. Prior staged config-validator
and ownership/receipt/callback changes remain preserved.

Residuals: runtime/quantity-parity/concurrency verification is unperformed by
instruction; the 193 old unit caller/expectation migrations remain deferred.
External OCR service implementation and H1 JSON/date integration are not
executed or modified in this source unit. This is not full H2 completion.

## Atomic rerun receipts and full raw facility snapshot correction

This section supersedes the earlier descriptions of builder calls, retained
legacy normalizers, and DB/base packaging or label enrichment. The public
getters now return a deepcopy of one complete authored JSON facility record.
They do not read Facility/config DB rows, merge base/override defaults, normalize
the raw template, or add missing input keys. JSON name/alias resolution requires
one exact facility match. Selected template IDs must match the actual raw
fax_template.template_id, not merely a declared list member. Missing, incomplete,
ambiguous and mismatched inputs stop explicitly. Zero/null/unknown raw values
are retained. No date/history selection policy was introduced.

Removed unreachable builder and legacy fax normalizers, plus their unused merge,
area-suffix and row-field helpers. Version resolution/registration consume the
same raw snapshot already read by their caller; version metadata stays outside
fax_template. Editor/storage override validation is not an adoption source.

Actual shared getter production inventory: ingest_worker firstpass and recovery;
order_service firstpass/reparse/rerun/annotation/cell estimation; OCR pipeline;
Hakodate batch, assignment and step-review; candidate resolution; workflow-v2;
facility-template-version resolution/registration/signatures; order-form,
master-order-form-template, output builder, total service; orders/facilities API.
All use the shared raw getters, including calls outside OCR contexts. No caller
of the removed builder or renamed legacy normalizers remains in backend/src.

Atomic result/terminal caller map:

- _reconcile_finished_ocr_rerun -> _persist_ocr_rerun_completion: own returned
  evidence, cache and real-job terminal receipt in one guarded Session.
- rerun_ocr_evidence_only -> live pipeline -> the same completion helper:
  optional own-evidence draft, workflow completion and job receipt share Session.
  Pipeline/document/missing-result errors use an explicit failed receipt.
- _reconcile_finished_llm_reparse -> reparse_order: accepted lines/cache/archive,
  rejected diagnostics, and rejected candidate review each complete their own job
  in the actual result Session. Recovery consumes that receipt, not a separate
  terminal update or a latest-evidence lookup.
- _run_reparse_background and _run_ocr_rerun_background consume returned
  job/order/attempt/revision/evidence receipt. Pre-result exceptions retain the
  existing guarded workflow failure completion; no business result was committed.
- reconcile_completed_ocr_job dispatches captured rerun/LLM execution to these
  same paths. Firstpass retains its existing atomic first_pass_terminal_receipt.
- ingest_worker._run_auto_llm_reparse consumes its captured execution and own
  returned receipt. Early error/crash persists its own diagnostic and failed
  terminal in the same guarded completion transaction, never a latest source.
- _complete_ocr_rerun_in_session calls mark_ocr_run_completed(write_session=...)
  with both captured queue tokens, preserving workflow-then-job lock order.
  A revision conflict causes caller rollback, not a secondary current read.
- update_job rejects an existing rerun receipt before and after locking. Events
  after a committed result cannot overwrite its terminal status. Receipt replay
  is rejected by the common actual terminal helper.

The result transaction also restores its captured mutable nonworkflow boundary
on rollback. Cache persistence disables old artifact preservation. No old/cache/
latest evidence supplies a missing own result. A standalone immutable revision
archive remains noncanonical without its guarded DB adoption decision.

Focused behavior declarations call real raw getters/version resolver and real
rerun persistence/completion helpers. Cases cover one snapshot, wrong actual ID,
missing/null/empty/incomplete inputs, raw zero/null preservation, success/current
rejection, captured workflow tokens, superseded revision, missing own output and
mid-write rollback. Transaction doubles explicitly are not ORM/concurrency proof.
No tests or runtime/DB commands were run. Formal ORM/concurrency and live parity
verification remain technical-phase work, alongside the 193 deferred unit caller
and fallback-expectation migrations. H1 JSON/date integration is not verified.
This freezes this source unit only, not overall H2 completion.

## API common failure completion correction

The two rerun timeout/crash branches previously committed job.failed before a
separate unchecked workflow completion. _complete_workflow_ocr_run now holds
the captured job/attempt/revision context and one caller-owned result Session.
The common actual completion helper locks workflow then job, invokes workflow
completion in that Session, writes the own terminal receipt, verifies its exact
job/order/attempt/revision/evidence/status, and commits as one unit. Conflict,
missing capture or receipt/write failure returns no receipt after rollback.
No conflict path updates the superseded job or fetches current tokens.

Actual API caller inventory:

- _run_ocr_rerun_background timeout and crash: no independent failed update,
  latest order/evidence/metrics supplementation or separate workflow commit.
- _run_reparse_background early returned error and crash: common completion
  with captured tokens and only the actual returned evidence ID; committed
  result receipts are not completed again.
- _dispatch_workflow_ocr_task add_task failure: same atomic failure helper;
  rejected/superseded completion is logged explicitly and dispatch remains 503.
- _mark_stale_order_reparse_job workflow-owned branch: captured queue tokens
  from the supplied job input, no workflow token recapture, same completion
  and returned receipt. Legitimate nonworkflow handling remains guarded by
  update_job; it does not infer workflow ownership or missing tokens.
- _heal_active_order_reparse_job_from_workflow no longer writes terminal job
  from a read projection. Actual result owners already commit job/workflow
  together; the supplied job remains the read result.
- Remaining update_job_for_workflow_attempt in API callbacks is nonterminal
  slot progress only. No callback independently writes workflow-owned terminal.

Failure stage metadata can be explicitly retained by API failure completion;
ordinary diagnostic/rejected result owners keep the existing review metadata
defaults. No ready/rejected acceptance policy, operator selection, quantity or
recognizer output changed. Diagnostic evidence cannot be adopted by this helper.

Full repository source search found no actual _enqueue_order_evidence_rerun
caller, only its definition and a contract-test monkeypatch. Removed the unused
old dispatch. The test reference in
backend/tests/contract/test_orders_workflow_state_api.py:340 still needs technical
phase migration; it is not classified as runtime verification pending alone.

Focused test_api_failure_atomic_completion source invokes the real API/common
completion helpers and actual timeout/crash/dispatch callbacks using a caller
transaction double. Cases declare captured tokens, exact receipt, missing input,
superseded no mutation, and mid-terminal-write rollback. These are not ORM lock
or concurrency proof and were not executed. Existing 193 migrations and formal
ORM/concurrency/runtime verification remain deferred; no full H2 completion.

## GET readonly source correction

This supersedes the preceding stale-API description: reading job metrics on GET
is not a processing-start capture and cannot authorize completion. Removed both
workflow and nonworkflow terminal updates from _mark_stale_order_reparse_job.
It returns the identical supplied job, without token extraction, completion,
current workflow recapture or fabricated terminal status. A stale diagnostic is
attached separately to the response order. force does not authorize mutation.

Source caller map and neighboring paths:

- GET list_orders full and lightweight projections retain actual persisted job
  status. Full projection's stale helper is diagnostic-only. Cache-derived
  success/missing-job status is diagnostic-only, not an OCR completion substitute.
- GET get_order detail uses actual job status; failed ReadTimeout no longer
  becomes running. _apply_stale_ocr_status adds diagnostic without replacing
  status, metrics, timestamp or error with fabricated terminal values.
- _heal_active_order_reparse_job_from_workflow remains nonmutating. List/detail
  workflow attachment calls refresh=False; get_workflow_state only reads stored
  workflow. No GET calls common failure completion or worker reconciliation.
- workflow-v2 GET OCR results, saved sheet, sheet-source, inspection and auto-edit
  job now use _get_workflow_readonly instead of the mutation helper whose SQLite
  conditional UPDATE is itself a write. Revision mismatch on auto-edit GET is
  returned as a separate diagnostic; persisted job status is unchanged.
- workflow/quad/header/row and pre-save status already use readonly lookup.
- OCR raw/output, evidence, job-status and overlay GETs retain read paths:
  get_ocr_output has allow_job_reconcile=False and rejects enabling read recovery;
  overlay assignment disables artifact repair and preview cache helper is pure.
- Bag summary uses transient projection, not Bag writes. Current-sheet review
  getter passes allow_state_mutation=False. Legacy draft/workflow-state/critical
  decision/OCR-page/OCR-sheet/candidate-preview GETs remain explicit gone paths.

Remaining common completion callers in orders.py are manual reparse background
early failure/crash, rerun background timeout/crash and dispatch failure only.
Their captured processing-start job/attempt/revision and atomic receipt contract
is unchanged. No business quantity, recognizer, authored JSON or selected-evidence
adoption policy changed in this correction.

Focused test_order_get_readonly_diagnostics source calls actual list/detail and
diagnostic helpers with old tokens and forbidden completion/update doubles. It
checks running/failed/done state preservation, zero quantity, cache diagnostics
and mutation-lock exclusion at workflow GET entrypoints. A formal-schema ORM
case invokes the real saved-sheet getter, records SQL, requires SELECT-only,
checks no workflow bootstrap, and preserves zero/null order lines. Entry doubles
are not full projection or concurrency proof. No tests/DB/runtime were run.

Frozen source residuals: 193 deferred migrations, the previously documented
deleted-enqueue contract-test reference, and unexecuted formal ORM/concurrency/
runtime verification. This bounded GET source unit does not claim overall H2.

## Pending external output outcome contract

Selected-document LLM reparse's two waiting branches previously returned only
running/output_reference, which API callback treated as missing terminal OEV.
Both now use _mark_reparse_job_awaiting_output in an actual guarded result
Session, preserving workflow state/revision and leaving the real job nonterminal.
It returns explicit outcome=pending with a persisted waiting receipt containing
original job/order/attempt/revision or nonworkflow execution ID, selected document,
own input/output reference and request arguments. No evidence is fabricated or
required before output exists. Terminal job status/receipt cannot revert to wait.
No latest/current tokens or old evidence supply the receipt. Original input URI
must match the locked own job. Missing reference or rejected fence stops explicitly.

is_own_pending_ocr_result validates one explicit captured ownership kind and exact
receipt/output identity. Merely running, missing payload, wrong token, unknown
execution or a terminal receipt is not a valid waiting outcome. Missing genuine
terminal evidence still fails. An incompatible arrived firstpass payload is no
longer relabeled pending; only actual pending/unarrived output uses that branch.

Actual production caller inventory:

- _run_reparse_background checks own pending before requiring terminal OEV;
  current success/error/crash still use the existing atomic terminal contract.
- _run_ocr_rerun_background and rerun_ocr_evidence_only wrapper recognize only
  matching explicit own pending outcome; direct synchronous recognizer failures
  and missing terminal output retain explicit failure, not arbitrary wait.
- _run_auto_llm_reparse and _reconcile_finished_llm_reparse preserve own pending
  without saving failed diagnostics or demanding a terminal receipt.
- run_ocr_job_recovery_once passes the original persisted waiting receipt to
  reconcile_pending_workflow_reparse. The service checks that same receipt and
  references under the original workflow-then-job fence, reads only own output,
  and resumes using original request/selected-document/start tokens. Still-pending
  output is nonterminal. Final evidence ID comes only from its returned result
  and atomic terminal receipt. No new OCR retry or timeout policy is introduced.
- Nonworkflow ocr_reparse recovery is allowed only with matching persisted own
  waiting receipt/execution. It preserves original arguments and selected source.
- Selected-document own resume validates the captured URI against the fenced
  real job rather than rejecting every selected-document continuation. Existing
  structured schema/own output-reference validation is retained; no active/cache
  input substitutes for incompatible/missing own input.
- Firstpass ingest catches OCRPipelineOutputPendingError, registers processing
  without terminal success, and returns without terminal OEV requirement. Its
  recovery returns False for actual pending output before any result application.
  Rerun reconciliation likewise leaves actual pending output untouched.

Focused test_ocr_pending_outcome_contract calls actual waiting writer, API
reparse/rerun callbacks, auto-LLM callback, workflow resume and firstpass recovery
entry. Declarations cover waiting original fence, missing terminal output,
wrong revision, superseded no load/write, exact selected source/own returned ID,
and zero input preservation. Transaction doubles are not ORM/concurrency proof.
Existing zero/null terminal evidence and own-schema incompatibility tests remain.
No runtime, test, DB or network commands were run. 193 migrations, deleted-enqueue
contract reference and formal technical verification remain deferred. This is
source-unit freeze only, not overall H2 completion.

## Own-job metrics patch source unit

Failure class: progress replaced the complete metrics JSON, deleting the queue
attempt and processing-start input needed by the next actual-write CAS.
Both update_job_for_workflow_attempt and update_job now patch only enumerated
stage/diagnostic fields onto the validated, locked job row. The nonworkflow path
refreshes that row after obtaining the actual-write fence. Neither path copies
ownership from caller metrics. Unknown patch keys cannot introduce canonical
adoption/source fields; existing unknown metrics remain intact. Original
workflow/nonworkflow ownership, first-pass/reparse/selected-version input,
pending and terminal receipts, and evidence identity are immutable to progress.
Recovery count increments from the locked row, not a caller's read snapshot.
Accepted workflow updates return the actual updated DTO so later progress and
pending consumers do not mistake a successful CAS for a missing job.

Production caller inventory (repository-wide source search):

- order_service._update_ocr_execution_job and _update_reparse_job_progress:
  common updater; _merge_ocr_job_metrics builds a patch without reading a job.
- order_service._run_reparse_with_heartbeat and optional LLM audit: explicit
  stage/quality/diagnostic patch, including timeout metadata, through that updater.
- order_service._reconcile_finished_llm_reparse, direct rerun start and reparse
  start: no full metrics copied from an earlier job DTO.
- orders._run_ocr_rerun_background slot wait/acquisition: patch only, original
  captured attempt/revision; no current metrics read.
- ingest_worker._first_pass_job_metrics, processing/error callers,
  _mark_ocr_job_awaiting_output and _mark_ocr_job_final_failed: patch only.
  Recovery waiting count increments in the fenced updater; the original waiting
  timestamp is preserved. Auto-LLM delegates progress to the same service path.
- order_service._mark_reparse_job_awaiting_output: protected locked-row merge,
  then explicit same-Session own pending receipt. Pending remains nonterminal.
- order_service._complete_ocr_rerun_in_session: protected locked-row merge of
  actual pipeline outcome diagnostics, then explicit own evidence and terminal
  receipt. Dynamic pipeline diagnostic keys are accepted only in this guarded
  outcome, not in the progress API.
- Firstpass, explicit skip and rejected completion writers retain metrics from
  their guarded caller Session and explicitly assign their own outcome/receipt.
- workflow queue reservation, capture_nonworkflow_execution and
  advance_nonworkflow_order_input intentionally claim/advance execution input;
  workflow completion intentionally writes result revision/blocker under its
  same-Session fence. These are not progress JSON replacements.

test_own_job_metrics_patch declares actual updater/ORM and reparse-helper cases:
two consecutive CAS updates, exact original zero/null inputs and pending receipt,
stale protected/unknown patch keys, superseded no mutation, atomic recovery count,
terminal refusal, and guarded outcome diagnostics retaining ownership. Ownership
locks are test doubles; these definitions do not establish concurrency proof.
No tests, runtime, DB, network or build were executed. Source diff checks only.
193 legacy caller/expectation migrations and formal technical verification remain
deferred. No additional production metrics caller gaps were found in this bounded
source audit; this is not overall H2 completion.

## Original request and owned delivery source unit

Base: d3d39bf3198f3ba631c82c7c8bd396323dcd2484, named branch
codex/modernization-h2-workflow-revisions-20261006. Existing staged changes retained.

Failure classes: an accepted custom reparse request was absent from queued
metrics and lost through the stage allowlist; external recovery reconstructed
null/default request parameters. Separately force-upload produced a delivery
URI different from the selected document, which the waiting writer rejected.

Original request contract:
- orders._enqueue_order_reparse_job stores all five original fields in the
  accepted queue mutation: prompt, preset, provider, model and llm_assist. Explicit
  null presence and raw values are preserved, without normalization/default fill.
- reparse_order captures/compares the complete request under the existing own
  execution fence before _reparse_order_with_attempt computes. Workflow callback,
  nonworkflow direct call and ingest auto-LLM use that shared contract. Partial
  or changed request returns reparse_original_request_conflict; missing fields
  cannot be reconstructed from quality_track/requested_provider.
- Existing feedback recursion explicitly carries the original request while
  retaining its existing calculation arguments. It does not recapture a request
  from late job reads or overwrite the receipt with the effective retry provider.
- Progress/heartbeat and guarded outcome telemetry cannot replace these five
  fields. A new nonworkflow execution claim removes the prior execution's request
  and delivery, rather than treating it as the new request.
- Waiting freezes exact request_kwargs. Both workflow pending recovery and
  nonworkflow LLM recovery compare that receipt with the fenced original request;
  they pass only these exact values into own resume. Missing/mismatch stops.

Delivery contract and all production run_ocr_pipeline callers:
- run_ocr_pipeline preserves selected_input_reference before creating its
  delivery input/output names. Before dispatch/wait it stores a separate guarded
  pipeline_delivery_receipt containing real job, target order, attempt/revision
  or execution ID, original selected URI, actual transport input/output URI and
  returned upload generation. No guessed URI or latest evidence is accepted.
- _mark_reparse_job_awaiting_output accepts only that exact own receipt and
  retains job.input_reference as the selected document. Pending embeds the own
  transport receipt; its output identity is not the original PDF identity.
- ingest normal/new upload, timeout and recovery resubmit all use
  mark_own_pipeline_waiting under the same actual-write fence. Their returned
  transport URI is mandatory; no original-PDF URI substitution remains. A
  refused wait raises ocr_pending_delivery_conflict, not fabricated waiting.
  A failed resubmit may refer only to its earlier explicit delivery receipt;
  if that receipt was replaced by another delivery, the guarded comparison fails.
- order_service._run_roi_ocr_pipeline and reparse's pipeline invocation use the
  same recording entry. The resume result gate checks exact own output delivery
  when the payload is not already an explicitly persisted own evidence result.
- fax_extractor's pipeline-provider branch now obtains the real fenced job and
  original input, rather than creating a MAIN-* synthetic identity. Force upload
  still delivers the actual supplied bytes, as its former no-input path did.
  Missing own execution stops explicitly; no alias/job invention is used.
- _enqueue_workflow_v2_evidence_rerun runs its existing direct evidence producer,
  not reparse request recovery; its policy and manual-review outcomes are unchanged.
- Legacy reparse/rerun API routes remain Gone and create no new source contract.

Focused behavior source extends test_ocr_pending_outcome_contract: actual queue
custom request capture; exact custom prompt/preset/provider/model recovery; null
presence and conflicting request refusal; actual mocked force-upload pipeline
timeout through the real delivery recorder and waiting writer; foreign URI no
write; nonworkflow new delivery waiting with original document retained. Existing
zero input, pending/nonterminal, genuine terminal-missing and superseded cases
remain. Transaction/lock doubles are not PostgreSQL concurrency verification.

This bounded source unit has no known remaining request/delivery production
caller migration. Test execution/formal verification and the 193 legacy test
migrations remain deferred. No runtime/test/build/DB/network/commit/deploy was
performed. Stage freeze and source diff checks are not an overall H2 success claim.

## Shared result mapping and GCS generation source unit

Four source defects addressed: FAX pending was swallowed as main_ocr_failed;
ingest pipeline-provider inference created a second transport/result while
evidence retained the first; recovery reused saved application lines rather than
mapping the arrived result; recorded transport generation did not constrain
producer download or result loading/adoption.

Actual production paths:
- ingest normal/new upload -> captured mapping template/policy -> pipeline own
  result -> map_own_ingest_result -> extract_fax_data structured adapter -> existing
  parse_order_lines -> existing atomic lines/document/evidence/job receipt.
  Pipeline-provider mapping reuses that exact verified result, never a second
  force-upload/cache/current evidence. Other recognizer providers retain their
  existing implementation/policies. Raw recognizer payload is not rewritten.
- Pending registration stores pre-calculation mapping_input, not a saved lines
  result. _reconcile_completed_ocr_job_with_input pins the arrived delivery PDF
  generation and calls the SAME mapper. Old saved lines are ignored. Missing
  mapping input or model/mapping failure produces explicit failed diagnostic
  completion, not fabricated quantities. Existing atomic apply is retained.
- reparse main FAX inference -> explicit own payload reuse where available;
  OCRPipelineOutputPendingError -> own waiting writer -> original request receipt.
  Repair/feedback and optional audit wrappers rethrow actual pending rather than
  converting it to failed/unknown inference. Review/audit providers are explicitly
  OpenAI/Gemini; their existing genuine failures retain their existing policy.
  Selected historical evidence remains an explicit LLM input, not an own current
  pipeline result. Pipeline FAX returns its raw own result in FaxExtractedData;
  reparse carries that exact payload/output URI into diagnostics and evidence.
  Normal pipeline reparse waits on its initial execution rather than starting a
  second FAX force-upload. Own resume validates generation inside its fenced
  Session before structured-schema adaptation.
- Direct ROI pipeline, reparse fresh pipeline, ingest firstpass and recovery
  resubmit all enter run_ocr_pipeline. Upload generation comes from the upload
  response, without a later latest-blob reload. Reuse is possible only with an
  existing validated own delivery and generation-pinned identical PDF bytes;
  otherwise the explicitly supplied bytes get a new delivery, not a source fallback.
- ocr_pipeline/app/main.py requires event generation (metageneration is not input
  identity), downloads blob(generation=...) with if_generation_match, and names
  output by input generation. Partial, done, error and duplicate response paths
  carry the same input generation/reference/output identity. Done output records
  the SHA256 of actual downloaded bytes, without changing recognizer values.
- Common delivery receipt includes exact pipeline job identity, generation, bytes
  SHA256 and original wait timeout. Backend polling validates partial/terminal
  output; mismatch is explicit failure, not an external-wait timeout.
- Worker output read, shared retry/single-read loaders, raw-text/job-output helper
  reads and own resume gate use the same receipt validation. Raw-text lookup no
  longer selects a latest job; cross-output chaining is removed. Missing ownership
  cannot turn an auxiliary read into current results.
  Ingest retains the actual output URI instead of substituting an ocr_output.json
  mirror. Recovery resubmit downloads the prior own delivery's pinned generation.
  Pending ingest exits mapping/retry immediately, including FAX-raised waiting.
  Operator raw-text GET/output-builder reads explicit selected evidence without
  finding a latest job; own execution raw-text reads require its delivery receipt.
- ocr_evidence_service.persist_evidence_run validates actual Yomitoku results
  again INSIDE its actual-write Session before insertion. It reuses the caller
  Session rather than opening a second lock transaction. An input/job change or
  wrong generation therefore cannot adopt evidence after an earlier precheck.
- Workflow pending recovery completes invalid lineage through its existing
  guarded atomic failure path. Nonworkflow worker recovery likewise produces an
  own failure receipt, not an adopted wrong-generation result. Existing pending,
  firstpass, skip and terminal atomicity/receipt protections remain.

Source audit additionally identified the unused _list_latest_completed_ocr_outputs
definition and an archival training-data output reader: neither is an actual
pipeline/FAX callback or current order result-adoption producer in this caller map.
No claim is made that all unrelated historical/diagnostic helpers are migrated.

Focused behavior definitions call real FAX adaptation, delivery validation,
firstpass recovery/shared mapping, generation-aware storage loader and pipeline
server download/event helper. Cases cover no second recognition, zero rows,
actual pending versus failed result, arrived result instead of saved 999 quantity,
foreign input/output/job/generation/PDF digest, and pinned download/missing event
generation. Parser/transaction/ownership doubles isolate contracts; these are not
full OCR, real database/concurrency or integration proof. Tests were NOT run.
Additional FAX behavior definitions assert the returned raw own object identity,
new recognition when no current own result is supplied, and rejection of selected
old generation as an own result. Zero quantities remain unchanged.

Caller inventory (backend/src + ocr_pipeline/app + scripts source search):
run_ocr_pipeline callers are FAX pipeline, order ROI dispatch, order fresh/main
pipeline, worker resubmit, and worker firstpass. FAX callers are the worker shared
mapper, operator LLM review, optional LLM audit, reparse main, LLM repair, and LLM
feedback. Review/audit/repair/feedback keep explicitly selected LLM providers;
their genuine failure policies are unchanged. Retry/single output loaders serve
rerun completion, pending workflow recovery, firstpass/LLM recovery, main reparse
and current-result diagnostics. The unused visible-rerun/job-output helpers have
no production adoption caller. Actual raw-text API/output-builder uses selected
evidence separately from execution input. Actual evidence insertion revalidates
pipeline provenance in the caller Session. No known scoped production caller is
left on an unbound pipeline generation or saved-lines recovery contract.

Remaining technical phase: formal/runtime verification, 193 legacy caller/test
expectation migrations, and old pipeline fixture/schema/filename assumptions
must be aligned with the new explicit generation protocol. Backend and pipeline
producer must be integrated together; no mixed-version deployment is validated.
No runtime/test/build/DB/network/commit/deploy performed. This is bounded source
freeze, not overall H2 completion.

## Actual firstpass recognizer, explicit week, captured date policy

Failure class: lines could be generated by configured OpenAI/Gemini while the
atomic firstpass evidence still described only initial Yomitoku; missing week
could be supplemented from OCR dates; row/date parsing could reload policy after
the execution's mapping input had already been captured.

Shared correction and actual callers:
- FaxExtractedData carries raw recognizer_payload for pipeline/OpenAI/Gemini.
  map_own_ingest_result returns lines AND recognizer_result. Nonpipeline results
  retain exact raw payload, adapter rows, provider diagnostics and execution/PDF/
  template/week provenance; initial Yomitoku is auxiliary_pipeline_result only.
  No configured provider, candidate, quantity or recognizer algorithm is changed.
- _process_ingest_captured and _reconcile_completed_ocr_job_with_input both consume
  that mapping DTO and pass its actual recognizer_result plus captured mapping
  policy to create_order_from_ingest. Recovery never supplies stored old lines.
  Auto-LLM receives this same own persisted evidence, not initial auxiliary OCR
  mislabeled as the quantity producer.
- create_order_from_ingest checks exact mapped-lines provenance before application.
  Existing caller Session atomically writes lines/document/evidence/job receipt.
  persist_evidence_run validates recognizer execution, original transport digest,
  template and explicit week in that SAME actual-write Session. Evidence extraction
  retains raw payload, recognized rows, provenance and input generation/digest;
  these fields cannot disappear in its whitelist. Mapped dates use ISO DTO values,
  leaving actual parser dates and quantities unchanged.
- Genuine hakodate quantity strategy was audited: extract_fax_data's hakodate
  branch is structure-only and performs no quantity recognition. Missing own raw
  quantity result now persists explicit first_pass_own_recognizer_result_missing
  diagnostics, not a Yomitoku-backed successful empty quantity application.
  The existing dedicated _hakodate_canonical_payload_from_manifest_item and
  rerun_ocr_evidence_only recognizer/candidate/review path is unchanged; no
  synthetic quantity recognition or provider switch was added to firstpass.
- require_explicit_ingest_week uses the EXISTING sheet week parser. Missing,
  malformed and month-only/unchosen week inputs stop; explicit range meaning is
  retained. API upload context and uploaded-PDF registration validate before
  persistence; uploaded recovery payload, ingest queue normalization, direct
  inline registration, captured computation and create_order_from_ingest also
  validate. Existing-target computation checks the captured order week matches.
  Uploaded recovery reports invalid input through its existing error mechanism.
  Filename, OCR date hints, recognized line dates and received-year calculations
  no longer supply canonical ingest week. No H1 effective-date policy is invented.
- mapping_input.policy is captured before inference. Mapper date hints and both
  date reads inside parse_order_lines receive it explicitly. Recovery uses that
  same stored policy, including menu matching at atomic application; no policy
  reread there. The other three production parser callers (draft materialization,
  sheet OCR conversion, reparse) pass their own local policy explicitly too.
  Date parsing/normalization/year-adjustment algorithms are unchanged. Legacy
  non-captured date-helper callers retain their existing interface, not a new
  source for a captured firstpass calculation.

Focused test source: test_ingest_recognizer_input_contract.py calls real configured
FAX adapters, common mapper/parser, evidence record extraction and shared write
validation. It covers OpenAI/Gemini quantity 0/12 versus auxiliary 999, exact raw
payload/provenance retention, explicit week rejection before registration/OCR,
policy reread prohibition across normal/recovery mapping, wrong execution/digest/
week rejection, and hakodate structure-only diagnostics. Provider and transaction
doubles are explicit; this is not real OCR/database/concurrency proof. Existing
pending recovery assertions retain raw-result equality and zero checks with the
new provenance DTO. No tests were run.

Scope source inventory: six production create_order_from_ingest calls (worker
failure, uploaded placeholder, inline placeholder, crash, normal application,
plus service recovery), two mapper
calls, four parse_order_lines calls and three raw-result FAX provider branches
were reviewed. The shared week checks also cover nonworker queue/script callers
through ingest_job_service. All prior staged changes are preserved. Remaining:
193 deferred caller/expectation migrations and formal technical/runtime tests.
The box-method dedicated recognizer was source-audited, not runtime-verified.
This bounded source freeze is not overall H2 completion or a runtime success claim.
