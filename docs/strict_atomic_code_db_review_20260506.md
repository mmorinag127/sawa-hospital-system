# Strict Atomic Code And DB Review

Date: 2026-05-06

Related design rules: `docs/strict_atomic_state_design_rules_20260506.md`

Detailed remediation plan: `docs/strict_atomic_remediation_detail_plan_20260506.md`

## Scope

Reviewed scope:

- Backend Python files under `backend/src`: 134 files
- Backend API routes: 162 routes
- Backend service files: 75 files
- SQLAlchemy model classes: 40 classes
- Migrations: `0001` through `0014`
- Frontend TS/TSX files under `frontend/src`: 44 files
- Current workflow-v2 and legacy order-detail paths

This is a code/schema review. It is not a live row-by-row stg/prod data audit.
Live data audit should be a separate read-only DB job before the rewrite migration.

## Rating Definitions

| Rating | Meaning |
|---|---|
| `厳密にOK` | New strict rules can keep this with minor or no changes. |
| `少し修正必要` | Mostly compatible, but naming, contract, or minor guard is needed. |
| `大幅に修正必要` | Useful domain logic exists, but read/write separation or lineage enforcement is substantially wrong. |
| `書き直し` | Current structure mixes responsibilities so heavily that incremental cleanup is riskier than rewriting around a strict boundary. |
| `破棄すべき` | Should not remain in current workflow. Keep only for archived read-only compatibility if needed. |

## Executive Verdict

The current system has the correct domain concepts, but they are not enforced structurally.

Good concepts already exist:

- workflow-v2 steps
- facility template versions
- OCR evidence runs
- saved sheet drafts
- confirmed snapshots
- candidate/current separation in parts of the UI

However, the implementation still mixes:

- read and write
- projection and materialization
- current and candidate
- canonical state and cache
- workflow-v2 and legacy order-detail paths
- template registration and resolved config fallback

The strict design cannot be reached by only patching the latest bug.
The safest path is to preserve the working Hakodate/OCR computation modules and rewrite the state-management boundary around them.

## P0 Design Violations Found

### 1. GET facility mutates active template version

File: `backend/src/api/facilities.py`

`GET /facilities/{facility_id}` calls:

```text
ensure_active_template_version_from_resolved_config(... created_by="facility-api-get")
```

Impact:

- Read request can archive/create active facility template version.
- This directly caused `template_version_mismatch`.

Rating: `大幅に修正必要`

Required fix:

- GET must call read-only `get_active_template_version`.
- Version creation only via explicit command or migration.

### 2. Evidence persist can silently create template version

File: `backend/src/services/ocr_evidence_service.py`

`persist_evidence_run()` calls `ensure_active_template_version_from_resolved_config()` when `template_version_id` is missing.

Impact:

- OCR evidence can materialize against a template version created implicitly at persist time.
- Evidence lineage is not fixed at OCR job creation.

Rating: `大幅に修正必要`

Required fix:

- `template_version_id` must be required for current workflow evidence.
- Legacy evidence without version becomes repair candidate, not current.

### 3. GET sheet-source can materialize workflow/template context

File: `backend/src/api/orders.py`

`GET /orders/{order_id}/workflow-v2/sheet-source` calls `build_sheet_from_selected_ocr()`, which calls `_ensure_workflow_template_version_from_context()`.

Impact:

- Preview GET can write workflow/order template version.
- Projection and materialization are mixed.

Rating: `大幅に修正必要`

Required fix:

- `GET sheet-source` must project only.
- `POST sheet/build` should materialize sheet draft.

### 4. GET order can reconcile OCR job/evidence

File: `backend/src/api/orders.py`

`GET /orders/{order_id}` can call:

- `update_ocr_job`
- `reconcile_completed_ocr_job`
- job output parsing

Impact:

- Opening order details may change job/evidence state.

Rating: `大幅に修正必要`

Required fix:

- order detail GET reads current status only.
- worker or explicit repair/reconcile command updates jobs/evidence.

### 5. GET evidence can backfill from legacy cache

File: `backend/src/api/orders.py`

`GET /orders/{order_id}/evidence` calls `get_latest_ocr_evidence_run(... backfill_from_cache=True)`.

Impact:

- Reading evidence can create evidence from cache.
- Cache becomes canonical through read.

Rating: `大幅に修正必要`

Required fix:

- GET evidence reads persisted evidence only.
- cache backfill becomes explicit repair dry-run/apply.

### 6. GET draft-sheet creates/persists draft

File: `backend/src/api/orders.py`

`GET /orders/{order_id}/draft-sheet` calls `ensure_hakodate_evidence_draft_current()`.

Impact:

- Opening a sheet can create saved draft.
- Current editor state can change by inspection.

Rating: `大幅に修正必要`

Required fix:

- GET sheet reads saved sheet or projects unsaved preview.
- `POST /sheet/build` creates draft.

### 7. GET workflow-state refreshes and persists workflow

File: `backend/src/api/orders.py`

`GET /orders/{order_id}/workflow-state` defaults `refresh=true`.

Impact:

- Reading workflow can write workflow state.

Rating: `大幅に修正必要`

Required fix:

- GET workflow uses projection without persist.
- `POST /workflow/refresh` or worker performs explicit refresh if still needed.

### 8. Facility list/get syncs master into DB

File: `backend/src/services/facility_service.py`

`list_facilities()` and `get_facility()` call `_ensure_facility_sync()`.

Impact:

- Reading facilities can create facilities, remap facility ids, and update orders.

Rating: `大幅に修正必要`

Required fix:

- Master sync must be explicit migration/job.
- Reads must block if DB is not initialized.

## Backend API Review

| Area | Files | Rating | Reason |
|---|---|---:|---|
| Auth/config/health basic reads | `auth.py`, `auth_config.py`, `health.py` | `厳密にOK` | Mostly read or explicit auth command. |
| System status | `system.py` | `少し修正必要` | Should remain read-only; static scan saw sync wording but code mainly summarizes state. Add guard. |
| Facilities | `facilities.py` | `大幅に修正必要` | GET mutates template version. Generic config edit still too broad. |
| Facility master | `facility_master.py` | `少し修正必要` | Explicit PUT is fine; master-to-DB sync must not happen in reads. |
| Orders legacy API | `orders.py` legacy routes | `書き直し` | Many read endpoints reconcile/backfill/materialize. Legacy paths still coexist with workflow-v2. |
| Orders workflow-v2 commands | `orders.py` workflow-v2 POST/PUT/DELETE routes | `少し修正必要` | Direction is correct, but sheet-source GET and read materialization remain. |
| Ingest upload/jobs | `ingest.py` | `少し修正必要` | Worker/job pattern is acceptable; ensure list/read endpoints do not lease or retry. |
| Menus | `menus.py`, `menu_masters.py`, `menu_rules.py`, `base_menus.py` | `少し修正必要` | Less tied to OCR lineage; schema ensure on read should be moved to migration/startup. |
| Outputs/totals | `outputs.py`, `totals.py` | `少し修正必要` | Mostly read/export. Need output bundle lineage rather than recomputation from loose order lines. |
| OCR registry/admin | `ocr_registry.py` | `少し修正必要` | Admin commands are acceptable; registry reads must not reclassify unresolved data. |
| Shipping | `shipping.py` | `少し修正必要` | Tracking commands are explicit. Keep separate from order workflow canonical path. |
| Worker API | `worker.py` | `少し修正必要` | Worker commands can mutate, but need idempotency and one mutating job per order. |

## Backend Service Review

| Service / Group | Rating | Reason | Target |
|---|---:|---|---|
| `order_service.py` | `書き直し` | 700+ functions; OCR, sheet, output, cache, legacy, workflow, LLM, repair, and UI projection are mixed. | Split into command/read/projection/repair services. |
| `order_workflow_v2_service.py` | `大幅に修正必要` | Better model, but get/read still creates workflow rows and mutates prerequisites/template version. | Keep domain model, split read vs command. |
| `workflow_state_service.py` | `書き直し` | Legacy workflow projection persists on refresh and syncs critical decisions. | Replace with pure projection plus explicit workflow commands. |
| `facility_template_version_service.py` | `大幅に修正必要` | Good canonical concept, but `ensure_active...` is dangerous and used by reads. | Separate read service and command service; forbid read creation. |
| `facility_service.py` | `大幅に修正必要` | Read methods sync master and can remap IDs/order facility codes. | Explicit master import/migration only. |
| `ocr_evidence_service.py` | `大幅に修正必要` | Evidence run model is useful, but persist can create template version implicitly. | Require template version and immutable run lineage. |
| `draft_sheet_service.py` | `少し修正必要` | Draft artifact concept is useful. | Enforce non-null lineage for current workflow and command-only creation. |
| `order_current_state_service.py` | `大幅に修正必要` | Duplicates current state in JSON and can mask source-of-truth issues. | Replace with artifact pointers or remove after workflow-v2 is canonical. |
| `ocr_job_service.py` | `少し修正必要` | Job model is useful. | Add idempotency, lock, and stricter template version requirement. |
| `ocr_pipeline_service.py` / `ocr_pipeline_state_store.py` | `少し修正必要` | Worker/job direction is acceptable. | Ensure status persistence is worker-only, not page GET. |
| `hakodate_*pipeline*`, `hakodate_assignment_service.py`, `hakodate_cell_ocr_batch_service.py` | `厳密にOK` to `少し修正必要` | Core computation can be retained if kept pure and artifact-driven. | Preserve as deterministic compute modules; no canonical writes. |
| `hakodate_best_method_runtime/*` | `少し修正必要` | Useful verification/runtime code. | Keep out of workflow mutation path; treat as compute/diagnostic. |
| `position_column_mapping_service.py` | `破棄すべき` for current workflow | Legacy fallback class has repeatedly produced wrong row/column remaps. | Keep only archived diagnostic, not current workflow. |
| `ocr_fallback.py` | `破棄すべき` for current workflow | Fallback path contradicts strict blocker model. | Remove from current workflow; archive only if needed. |
| `config_service.py` | `大幅に修正必要` | Master, DB override, registry, workbook source are merged dynamically; canonical source can split. | Explicit resolved source selection and versioned config materialization. |
| `config_validator.py` | `厳密にOK` | Pure validation direction is right. | Keep pure; do not call mutating normalizers from read paths. |
| `menu_service.py` | `大幅に修正必要` | Schema ensure and get/create master behavior are mixed with reads/updates. | Split menu read, command, schema migration. |
| `output_builder.py`, `total_service.py` | `少し修正必要` | Computation is useful. | Inputs must be confirmed snapshot/output bundle, not loose fallback lines. |
| `daily_output_override_service.py` | `少し修正必要` | Explicit override commands are acceptable. | Ensure audit and output lineage. |
| `uploaded_pdf_service.py`, `ingest_job_service.py` | `少し修正必要` | Job/lease model is acceptable. | Strict idempotency and no read-side leasing. |
| `manual_upload_service.py` | `少し修正必要` | Explicit upload command is fine. | Ensure it only creates uploaded PDF/order in one atomic command. |
| `critical_decision_service.py` | `大幅に修正必要` | `sync_pending_decisions` creates/deletes decisions during workflow refresh. | Make decision sync explicit command or pure projection. |
| `candidate_resolution_service.py`, `apply_gate_service.py` | `少し修正必要` | Mostly projection/gating logic. | Keep pure; no command imports. |
| `storage_service.py` | `少し修正必要` | Clear load/save split exists. | For GET, save methods must be unreachable. |
| `system_maintenance_service.py`, cleanup services | `少し修正必要` | Maintenance can mutate. | Admin-only explicit commands; never normal UI read. |
| OCR provider services (`openai`, `gemini`, `yomitoku`) | `少し修正必要` | External calls are not canonical by themselves. | Results become candidates until explicit selection. |
| Shipping services | `厳密にOK` to `少し修正必要` | Separate bounded workflow. | Keep isolated from order OCR lineage. |

## Frontend Review

| Frontend area | Rating | Reason | Target |
|---|---:|---|---|
| `orders/[id]/workflow-v2.tsx` | `大幅に修正必要` | UI mostly follows step model, but calls `GET sheet-source` to build sheet, fetches facility detail GET that currently mutates, and carries complex local state. | Keep UX, simplify around command/read API contracts. |
| `orders/[id]/inspection-v2.tsx` | `少し修正必要` | Correct idea: read-only inspection. | Ensure backend endpoint is truly read-only. |
| `orders/[id].tsx` | `書き直し` | Legacy order detail contains old OCR sheet, draft, fallback, facility template editing, rerun, apply, confirm, and overlay logic. | Remove current workflow authority; keep redirect/read-only archive only. |
| `orders/index.tsx` | `少し修正必要` | List is read UI, but it calls runtime/status paths that can be heavy. | Read-only list endpoint with no reconcile. |
| `daily-delivery-notes.tsx` | `少し修正必要` | Useful output debugging UI. | Read from confirmed output bundle/current confirmed lines only. |
| `facilities/[id].tsx` | `大幅に修正必要` | Facility read currently triggers template version mutation; config edit is broad. | Read-only facility view plus explicit template command UI. |
| `pdf-upload.tsx` | `少し修正必要` | Explicit upload is okay. | Auto OCR/context suggestion must create candidates, not canonical state. |
| Admin/config pages | `少し修正必要` | Explicit admin commands are acceptable. | Add clear actor/audit and no hidden repair from load. |
| Non-order informational pages | `厳密にOK` | Mostly static/read-only. | Keep. |

## DB / Model Review

| Table / Model | Rating | Reason | Target |
|---|---:|---|---|
| `facility_template_versions` | `大幅に修正必要` | Canonical table exists, but active uniqueness is not constrained and read-created versions occurred. | Add unique active per facility, command-only creation, non-read source guard. |
| `facility_configs` | `大幅に修正必要` | JSON config still contains template definitions and can split from version table. | Reduce to operator settings; template columns live in version table. |
| `facilities`, `facility_areas` | `少し修正必要` | Basic master data is fine, but read-side sync/remap is dangerous. | Explicit master import. |
| `orders` | `大幅に修正必要` | Has `template_version_id`, but lineage can be nullable and status is broad string. | Explicit state machine columns or workflow pointer. |
| `order_documents` | `少し修正必要` | Good document artifact table. | Link to uploaded_pdf/order with immutable digest. |
| `uploaded_pdfs`, `uploaded_pdf_attempts` | `少し修正必要` | Good ingestion job model. | Ensure order creation is idempotent and atomic. |
| `ocr_jobs` | `大幅に修正必要` | Has template version but nullable; job status/metrics JSON carries too much workflow meaning. | Non-null template version for current OCR, idempotency key, order lock. |
| `order_ocr_evidence_runs` | `大幅に修正必要` | Good immutable evidence concept, but template version nullable and legacy backfill exists. | Non-null for current; legacy rows cannot become current without migration. |
| `order_ocr_cache` | `破棄すべき` as canonical source | Cache has been used for backfill/current recovery. | Keep only non-canonical display cache or remove. |
| `order_ocr_revisions` | `破棄すべき` for current workflow | Legacy revision path can mask saved sheet/evidence truth. | Archive/read-only only. |
| `order_sheet_drafts` | `大幅に修正必要` | Good artifact concept, but template/evidence lineage nullable and no revision chain. | Non-null lineage, immutable saved revisions, explicit current pointer. |
| `order_current_states` | `書き直し` | Duplicates payload and pointer state; easy to diverge from workflow. | Replace with canonical artifact pointer projection or remove. |
| `order_workflow_states` | `大幅に修正必要` | Good central state concept, but read path can create/update it and metadata JSON carries too much. | Command-only transitions, read projection separate. |
| `order_confirmed_snapshots` | `大幅に修正必要` | Snapshot is useful but lacks output bundle lineage. | Link to output bundle and saved sheet/output artifact ids. |
| `order_lines` | `大幅に修正必要` | Final materialization table, but legacy fallback has been used as input. | Only final-confirm output; never fallback source for current workflow. |
| `bags`, `label_rows`, `delivery_notes`, `manufacturing_aggregate_rows` | `書き直し` | Output artifacts lack full input lineage and version linkage. | Introduce bagging_result/output_bundle tables or add strict lineage ids. |
| `daily_output_portion_overrides` | `少し修正必要` | Explicit override table is fine. | Link to output bundle/date and audit actor. |
| menu tables | `少し修正必要` | Domain tables are acceptable. | Move schema ensure to migrations and freeze menu snapshot used by orders. |
| `order_critical_decisions` | `大幅に修正必要` | Decision model is useful, but sync during read/projection mutates. | Decisions created by explicit candidate resolution command. |
| `order_sheet_patch_candidates` | `少し修正必要` | Candidate model fits strict design. | Keep candidates separate from current until explicit apply. |
| `audit_logs` | `大幅に修正必要` | Audit exists but many canonical mutations do not write sufficient audit. | Require audit on every command/repair/migration. |
| users/notifications/shipping | `厳密にOK` to `少し修正必要` | Mostly outside OCR lineage. | Keep isolated. |

## Migration Review

| Migration area | Rating | Reason | Target |
|---|---:|---|---|
| `0014_facility_template_versions` | `大幅に修正必要` | Adds necessary lineage, but nullable columns and no unique active constraint allow drift. | Add strict constraints after data migration. |
| `0013_order_current_states` | `書き直し` | Current state JSON can duplicate and diverge from artifact lineage. | Replace with derived read model or strict pointer-only state. |
| Import-time `Base.metadata.create_all` | `破棄すべき` | Schema mutation at import/read time bypasses migrations. | Migrations only. |
| Older schema migrations | `少し修正必要` | Mostly historical. | Keep, but new strict migrations should introduce constraints and new artifact tables. |

## Legacy / Fallback Paths To Remove From Current Workflow

These may remain only as archived compatibility readers, not as current workflow producers:

- legacy `draft-sheet` current generation from GET
- legacy `ocr-sheet` recovery from cache
- `order_ocr_cache` to evidence backfill
- `position_column_mapping_service` fallback for current workflow
- legacy order detail `[id].tsx` as editing surface
- generic `OrderLine` fallback for Step3/Step4
- `order_ocr_revisions` as current editable source
- read-side facility master sync
- read-side workflow refresh with persistence

## API File Classification

| File | Rating | Rewrite note |
|---|---:|---|
| `backend/src/api/__init__.py` | `厳密にOK` | Package marker only. |
| `backend/src/api/auth.py` | `少し修正必要` | Login/logout are commands; read routes need normal guard only. |
| `backend/src/api/auth_config.py` | `少し修正必要` | Config read/admin command split should be explicit. |
| `backend/src/api/base_menus.py` | `少し修正必要` | Domain commands are acceptable; schema/bootstrap must not run on read. |
| `backend/src/api/facilities.py` | `大幅に修正必要` | GET currently mutates active template version. Split read vs template command. |
| `backend/src/api/facility_master.py` | `少し修正必要` | Master edit is command; read must not trigger facility sync. |
| `backend/src/api/health.py` | `厳密にOK` | Health/read endpoint. |
| `backend/src/api/ingest.py` | `少し修正必要` | Upload/job commands are acceptable; list/status must not lease/retry. |
| `backend/src/api/menu_masters.py` | `少し修正必要` | Menu master commands are acceptable; keep menu snapshot lineage. |
| `backend/src/api/menu_rules.py` | `少し修正必要` | Explicit rule commands are acceptable. |
| `backend/src/api/menus.py` | `少し修正必要` | Mostly menu CRUD; remove schema ensure from read path. |
| `backend/src/api/ocr_registry.py` | `少し修正必要` | Registry admin is command; unresolved registry selection must block. |
| `backend/src/api/order_forms.py` | `少し修正必要` | Generation commands are acceptable; generated artifacts need lineage. |
| `backend/src/api/orders.py` | `書き直し` | Mixed legacy/current route file. GET routes reconcile/backfill/materialize. Split by workflow command/read/projection. |
| `backend/src/api/outputs.py` | `少し修正必要` | Output reads/exports are useful; must read output bundle lineage. |
| `backend/src/api/shipping.py` | `少し修正必要` | Separate bounded workflow; keep isolated from OCR/order state. |
| `backend/src/api/system.py` | `少し修正必要` | Status read should remain pure; maintenance commands elsewhere. |
| `backend/src/api/totals.py` | `少し修正必要` | Read/projection endpoint; must use confirmed output lineage only. |
| `backend/src/api/users.py` | `少し修正必要` | User admin commands need audit; not part of OCR lineage. |
| `backend/src/api/worker.py` | `少し修正必要` | Worker mutation is allowed; add idempotency and job locks. |

## Service File Classification

| File | Rating | Rewrite note |
|---|---:|---|
| `backend/src/services/__init__.py` | `厳密にOK` | Package marker only. |
| `apply_gate_service.py` | `少し修正必要` | Keep as pure gate/projection. It must not apply changes. |
| `base_menu_service.py` | `少し修正必要` | Keep menu domain logic; isolate schema/bootstrap. |
| `candidate_resolution_service.py` | `少し修正必要` | Candidate model is good; remove import-time schema mutation. |
| `config_service.py` | `大幅に修正必要` | Master, DB override, registry, workbook are dynamically merged. Needs single explicit resolved source. |
| `config_validator.py` | `厳密にOK` | Validation is the correct shape if kept pure. |
| `critical_decision_service.py` | `大幅に修正必要` | Decision sync must not happen during read/projection. |
| `daily_output_override_service.py` | `少し修正必要` | Explicit override command is valid; needs audit and lineage. |
| `draft_sheet_service.py` | `大幅に修正必要` | Draft artifact is valid, but creation must be command-only and lineage non-null. |
| `evidence_manifest_service.py` | `少し修正必要` | Keep as read/manifest helper if it does not create canonical evidence. |
| `facility_master_service.py` | `少し修正必要` | Master import is useful; must be explicit migration/job. |
| `facility_service.py` | `大幅に修正必要` | Read methods call `_ensure_facility_sync`. Reads must not create/remap facilities. |
| `facility_template_version_service.py` | `大幅に修正必要` | Canonical concept is right, but `ensure_active...` is dangerous. Split read/command. |
| `fax_extractor.py` | `少し修正必要` | Compute helper can be kept. No canonical writes. |
| `fax_parser.py` | `少し修正必要` | Compute/parser helper can be kept. No canonical writes. |
| `fax_roi_extractor.py` | `少し修正必要` | Compute helper can be kept. |
| `fax_template_matcher.py` | `少し修正必要` | Matching helper is allowed as candidate/projection, not canonical selection. |
| `gemini_ocr_service.py` | `少し修正必要` | Provider result is candidate only until selected. |
| `grid_detector.py` | `少し修正必要` | Pure compute helper can be retained. |
| `hakodate_assignment_service.py` | `厳密にOK` | Retain if deterministic/pure and all inputs explicit. |
| `hakodate_cell_ocr_batch_service.py` | `厳密にOK` | Retain OCR cell compute; no canonical writes. |
| `hakodate_fixed_quad_registration_service.py` | `少し修正必要` | Retain geometry logic; enforce no fallback that changes structure silently. |
| `hakodate_ocr_evidence_service.py` | `少し修正必要` | Evidence construction is useful; persistence belongs to command service. |
| `hakodate_preprocessing_pipeline_service.py` | `少し修正必要` | Retain pipeline compute; output must be candidate artifact only. |
| `hakodate_step_review_pipeline_service.py` | `少し修正必要` | Retain diagnostic/step review; cannot be current mutation path. |
| `ingest_job_service.py` | `少し修正必要` | Job model useful; enforce idempotency and no read-side leasing. |
| `ingest_policy.py` | `厳密にOK` | Policy helper can remain pure. |
| `intake_mode_service.py` | `少し修正必要` | Mode selection must be explicit and audited if canonical. |
| `manual_upload_service.py` | `少し修正必要` | Explicit upload command is valid; make order creation atomic. |
| `menu_rule_service.py` | `少し修正必要` | Explicit rule commands valid. |
| `menu_service.py` | `大幅に修正必要` | Schema ensure/get-or-create patterns mixed into service. Split read/command/migration. |
| `menu_upload_archive_service.py` | `少し修正必要` | Archive service can remain if append-only/audited. |
| `menu_vocabulary.py` | `厳密にOK` | Vocabulary helper. |
| `notification_service.py` | `少し修正必要` | Separate side-effect domain; command-only sends. |
| `ocr_evidence_service.py` | `大幅に修正必要` | Persist can create template version and delete current state. Require explicit lineage. |
| `ocr_fallback.py` | `破棄すべき` | Fallback conflicts with blocker-first current workflow. |
| `ocr_job_service.py` | `少し修正必要` | Keep job model; enforce one active mutating job/order and template version. |
| `ocr_llm_review_service.py` | `少し修正必要` | LLM output is suggestion/candidate only. |
| `ocr_patch_candidate_service.py` | `少し修正必要` | Candidate concept is correct; remove import-time schema mutation. |
| `ocr_pipeline_service.py` | `少し修正必要` | Worker pipeline acceptable; no page GET execution. |
| `ocr_pipeline_state_store.py` | `少し修正必要` | State store acceptable for job status, not canonical artifact replacement. |
| `ocr_quality_service.py` | `厳密にOK` | Pure scoring/projection if no persistence. |
| `ocr_registry_service.py` | `少し修正必要` | Registry can remain; unresolved template/provider selection blocks. |
| `ocr_revision_store.py` | `破棄すべき` for current workflow | Legacy revisions cannot be current source. Archive only. |
| `ocr_sheet_revision_service.py` | `大幅に修正必要` | Revision concept useful, but must align with selected evidence/saved sheet lineage. |
| `ocr_training_dataset_service.py` | `少し修正必要` | Training dataset can remain; remove import-time schema mutation. |
| `ocr_week_rerun_service.py` | `少し修正必要` | Rerun is explicit command/job only. |
| `openai_ocr_service.py` | `少し修正必要` | Provider output candidate only. |
| `order_current_state_service.py` | `書き直し` | Duplicate current JSON state can diverge from artifact chain. Replace with pointer/projection. |
| `order_form_service.py` | `少し修正必要` | Generation logic useful; artifacts need immutable ids. |
| `order_operational_cleanup_service.py` | `少し修正必要` | Admin maintenance only; not normal read path. |
| `order_service.py` | `書き直し` | Monolith mixes OCR, cache, sheet, output, legacy, repair, projection and commands. |
| `order_workflow_v2_service.py` | `大幅に修正必要` | Step model useful, but read path creates workflow/template state. |
| `output_builder.py` | `少し修正必要` | Keep compute logic; input must be saved sheet/output bundle lineage. |
| `pdf_render.py` | `厳密にOK` | Rendering helper if pure. |
| `position_column_mapping_service.py` | `破棄すべき` for current workflow | Legacy row/column inference is obsolete in Hakodate current path. |
| `preprocess.py` | `少し修正必要` | Image compute helper can remain. |
| `quantity_subgrid_experiment.py` | `破棄すべき` from production path | Experiment only; move to diagnostics/archive. |
| `sagawa_tracking_service.py` | `少し修正必要` | Separate shipping domain. |
| `sheet_week_service.py` | `少し修正必要` | Week projection useful; canonical week changes command-only. |
| `shipping_service.py` | `少し修正必要` | Separate workflow; keep isolated. |
| `shipping_status_store.py` | `少し修正必要` | Remove import/function-time schema mutation; commands explicit. |
| `storage_service.py` | `少し修正必要` | Keep load/save split; GET cannot call save. |
| `structure_guided_ocr.py` | `破棄すべき` for current workflow | Old structural OCR approach conflicts with Hakodate template pipeline. Archive if needed. |
| `system_maintenance_service.py` | `少し修正必要` | Admin-only mutation; remove import-time schema mutation. |
| `template_builder.py` | `少し修正必要` | Template build should be explicit command. |
| `template_field_schema_service.py` | `少し修正必要` | Field schema helper useful; ensure it is derived from template version. |
| `template_resolution_service.py` | `大幅に修正必要` | Resolution must be explicit and blocker-based, not fallback/default. |
| `total_service.py` | `少し修正必要` | Calculation useful; input must be confirmed/saved lineage, not loose fallback. |
| `uploaded_pdf_service.py` | `少し修正必要` | Upload artifact model useful; immutable digest required. |
| `user_service.py` | `少し修正必要` | User/admin service separate; audit commands. |
| `week_candidate_service.py` | `少し修正必要` | Candidate/suggestion only; selection command required. |
| `workbook_pdf_renderer.py` | `少し修正必要` | Renderer helper; no canonical writes from read path. |
| `workflow_state_service.py` | `書き直し` | Legacy refresh persists and syncs decisions; replace with pure projection plus workflow commands. |
| `yomitoku_text_recognizer_topk.py` | `少し修正必要` | OCR helper can remain as provider/candidate generator. |

## Frontend File Classification

| File / Area | Rating | Rewrite note |
|---|---:|---|
| `frontend/src/pages/orders/[id]/workflow-v2.tsx` | `大幅に修正必要` | UX direction is right, but API calls must align with strict command/read split. |
| `frontend/src/pages/orders/[id]/inspection-v2.tsx` | `少し修正必要` | Keep as read-only inspection; backend must enforce read-only. |
| `frontend/src/pages/orders/[id].tsx` | `書き直し` | Legacy order detail must lose editing/current authority. Redirect or archive-only. |
| `frontend/src/pages/orders/index.tsx` | `少し修正必要` | Read list; avoid endpoints that reconcile on load. |
| `frontend/src/pages/daily-delivery-notes.tsx` | `少し修正必要` | Keep debug toggle; read confirmed output lineage only. |
| `frontend/src/pages/facilities/[id].tsx` | `大幅に修正必要` | Facility read currently relies on backend GET that mutates. Template edit must be explicit command UI. |
| `frontend/src/pages/facilities/index.tsx` | `少し修正必要` | Facility list read only after backend sync removal. |
| `frontend/src/pages/pdf-upload.tsx` | `少し修正必要` | Upload is command; auto inference appears as suggestion only. |
| `frontend/src/pages/ocr-*` | `大幅に修正必要` | OCR admin/legacy views must not create current evidence or current sheet. |
| `frontend/src/pages/menu-*`, `menus/*`, `base-menus.tsx`, `menu-rules.tsx` | `少し修正必要` | Menu admin acceptable; no schema bootstrap on page load. |
| `frontend/src/pages/order-forms.tsx`, `weekly-orders.tsx`, `facility-orders.tsx` | `少し修正必要` | Generation/order-form pages need artifact lineage but can stay. |
| `frontend/src/pages/shipping*` | `少し修正必要` | Separate workflow; keep isolated. |
| `frontend/src/pages/system-status.tsx`, `users.tsx` | `少し修正必要` | Admin/read UI; actions explicit. |
| Static/public pages and shared nav/hooks/api client | `厳密にOK` | Keep with normal cleanup. |

## Model Class Classification

| Model group | Rating | Rewrite note |
|---|---:|---|
| Document and uploaded PDF models | `少し修正必要` | Good artifact base; add immutable digest and explicit order linkage. |
| Facility models | `大幅に修正必要` | Master/config/template source split is unsafe. Facility config should not be a second template source. |
| Facility template version model | `大幅に修正必要` | Keep concept; add unique active, immutable data, command-only source. |
| Ingest/OCR job models | `少し修正必要` | Keep job tables; add strict idempotency, locks, required template version. |
| Menu models | `少し修正必要` | Keep canonical menus; freeze order menu snapshots. |
| Order base/order line/order menu snapshot | `大幅に修正必要` | Order aggregate remains, but line materialization must be final output, not fallback input. |
| OCR evidence/cache/revision models | `大幅に修正必要` to `破棄すべき` | Evidence run is good; cache/revision cannot be current. |
| Sheet draft/current state/workflow state models | `大幅に修正必要` to `書き直し` | Saved sheet is useful; current/workflow state must become pointer/state machine, not mutable JSON source. |
| Output models | `書き直し` | Need bagging result/output bundle lineage. Current tables are too loose for strict chain. |
| User/audit/notification models | `少し修正必要` | Audit must become mandatory for canonical commands. |
| Shipping tracking models | `少し修正必要` | Separate domain; keep isolated. |

## Migration File Classification

| File | Rating | Rewrite note |
|---|---:|---|
| `0001_init.py` to `0012_order_archives.py` | `少し修正必要` | Historical migrations can remain; new strict migrations should supersede weak constraints. |
| `0013_order_current_states.py` | `書き直し` | Current-state JSON table enables split-brain; replace or reduce to read model/pointers. |
| `0014_facility_template_versions.py` | `大幅に修正必要` | Correct direction, but nullable lineage and missing active uniqueness allowed current bug class. |
| `seed_facilities.py` | `少し修正必要` | Seed is valid if explicit; must not run via normal read. |

## DB Row Audit Required Before Production Rewrite

This document reviewed declared schema and code paths. Before applying the rewrite to prod, run a read-only row audit that reports:

- facilities whose active template version is missing
- facilities with more than one active template version
- active template versions created by read-like sources such as `facility-api-get`
- orders whose workflow template version differs from selected OCR evidence
- saved sheets whose template/evidence lineage is missing or mismatched
- confirmed orders whose output/bagging/saved sheet lineage is missing
- rows that still depend on `order_ocr_cache`, `order_ocr_revisions`, or `order_current_states` as canonical source

Audit output must be a report first. It must not repair data during the read.

## Target Rewrite Modules

The rewrite should not start by modifying the monolith in place.
Create strict modules and progressively move endpoints.

Suggested modules:

- `workflow_read_service.py`
- `workflow_command_service.py`
- `workflow_projection_service.py`
- `workflow_repair_service.py`
- `artifact_lineage_service.py`
- `facility_template_read_service.py`
- `facility_template_command_service.py`
- `ocr_evidence_read_service.py`
- `ocr_evidence_command_service.py`
- `sheet_projection_service.py`
- `sheet_command_service.py`
- `bagging_command_service.py`
- `output_command_service.py`

Dependency rule:

```text
read service -> projection/validation only
projection service -> read models/helpers only
command service -> read/projection/validation + repositories
repair service -> command repositories, never called from GET
```

## Rewrite Priority

### Phase 0: Guards Before Rewrite

1. CI static guard for GET side effects.
2. Runtime GET write guard for canonical tables.
3. Stop `facility-api-get` version creation.
4. Block evidence persist without explicit template version in current workflow.

### Phase 1: Template/Evidence Boundary

1. Split facility template read/command service.
2. Make active template version command-only.
3. Make OCR job/evidence require template version.
4. Move cache backfill to explicit repair.

### Phase 2: Workflow Read/Command Split

1. Replace GET workflow refresh with pure projection.
2. Replace GET sheet-source materialization with `POST sheet/build`.
3. Make Step3 saved sheet the only Step4 input.
4. Make confirmed snapshot/output bundle atomic.

### Phase 3: Legacy Removal

1. Remove current editing authority from `orders/[id].tsx`.
2. Disable legacy `draft-sheet` / `ocr-sheet` current paths.
3. Remove position fallback from current workflow.
4. Keep inspection/archive readers only.

### Phase 4: DB Constraint Hardening

1. Backfill strict lineage ids.
2. Add unique active template constraint.
3. Add non-null constraints for current workflow artifacts.
4. Add output bundle / bagging result lineage.
5. Remove or isolate current state JSON duplication.

## Required Verification For The Rewrite

Contract tests:

- Every GET route leaves canonical table digests unchanged.
- Every command route writes audit log.
- Template version mismatch blocks.
- Evidence without template version cannot become current.
- Saved sheet with explicit date/daypart/menu cannot be remapped by position.
- Step4 cannot run without saved sheet id.
- Step5 cannot confirm without output bundle id.
- Confirmed order small sheet correction creates a new sheet revision and does not alter confirmed lines until reconfirm.

DB tests:

- One active template version per facility.
- Current workflow artifact chain has matching template version ids.
- Cache cannot be selected as evidence.
- Legacy rows cannot become current without migration.

Live verification:

- workflow-v2 page
- inspection page
- orders list
- daily output
- output bundle/download
- facility template view/edit

## Summary

The current code should be treated as a useful prototype plus production-learned domain knowledge, not as a safe state-management foundation.

Keep:

- Hakodate computation logic
- OCR cell extraction logic
- visible workflow-v2 UX concepts
- DB artifact concepts
- menu/output calculation knowledge

Rewrite or heavily isolate:

- order state orchestration
- read/write route boundaries
- legacy order detail editing
- cache/evidence/draft fallback behavior
- facility template materialization
- output artifact lineage

The rewrite should begin with guards, not feature work. Without guards, the same design mistake can re-enter through another read path.
