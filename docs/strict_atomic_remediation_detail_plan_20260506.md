# Strict Atomic Remediation Detail Plan

Date: 2026-05-06

Branch: `rewrite/strict-atomic-remediation-plan-20260506`

Related documents:

- `docs/strict_atomic_state_design_rules_20260506.md`
- `docs/strict_atomic_code_db_review_20260506.md`

## Purpose

この文書は、strict atomic review で `少し修正必要` / `大幅修正必要` / `書き直し` / `破棄すべき` と判定した箇所について、実装前に修正内容を具体化するためのもの。

今回は実装しない。削除しない。deploy しない。

## Parent Agent Judgment

サブエージェント結果に対する親エージェント判定:

| Agent scope | Result | Parent judgment | Reason |
|---|---:|---:|---|
| backend API/service read-write boundary | completed | `OK` | 対象worktree上の endpoint / service / helper に基づき、failure class、shared decision point、修正単位、検証条件が具体化されている。 |
| DB/schema/model/migration lineage | completed | `OK` | テーブル、model、migration、constraint、row audit、migration順序、blocker、rollback/invalidated方針が具体化されている。 |
| frontend/API contract first attempt | completed | `NG` | `/Users/mmorinag/Sawa/2025.12/workspace` 側を根拠にしており、今回の対象branch/worktreeの証拠になっていなかった。 |
| frontend/API contract retry | completed | `OK` | 対象worktree `/Users/mmorinag/Sawa/2025.12/worktrees/template-lineage-hardening-20260505` に固定され、workflow-v2、inspection、legacy detail、一覧、施設ページの修正単位が具体化されている。 |

## Backend Remediation Units

### 1. Facility / Template Read Boundary

Classification: `書き直し`

Target files:

- `backend/src/api/facilities.py`
- `backend/src/services/facility_service.py`
- `backend/src/services/facility_template_version_service.py`

Target functions / endpoints:

- `GET /facilities`
- `GET /facilities/{facility_id}`
- `facility_service.list_facilities`
- `facility_service.get_facility`
- `facility_service._ensure_facility_sync`
- `facility_service._sync_facilities_from_master`
- `facility_template_version_service.ensure_active_template_version_from_resolved_config`

Failure classes:

- `read-triggered facility master sync`
- `read-triggered active template rotation`

Shared decision point:

- facility read path currently decides whether master data and active template versions should be created/updated.

Required change:

- Split facility/template into read service and command/migration service.
- `GET /facilities*` must only read existing state and return blocker if unresolved.
- Facility master import must be an explicit migration/admin command.
- Active template version archive/create/activate must be explicit operator command or migration.
- `created_by="facility-api-get"` and equivalent read-source template creation must become impossible.

Invariant:

- GET must not create facilities, remap facility ids, update order facility codes, archive template versions, or create active template versions.

Verification:

- Repeated `GET /facilities` and `GET /facilities/{id}` leaves `facilities`, `facility_template_versions`, and `orders.template_version_id` unchanged.
- Uninitialized facility returns blocker instead of auto-sync.
- Active template version count does not change after facility page load.

### 2. OCR Evidence Read Boundary And Legacy Fallback Removal

Classification: `書き直し`

Target files:

- `backend/src/api/orders.py`
- `backend/src/services/order_service.py`
- `backend/src/services/ocr_evidence_service.py`

Target functions / endpoints:

- `GET /orders/{order_id}`
- `GET /orders/{order_id}/document`
- `GET /orders/{order_id}/ocr-output`
- `GET /orders/{order_id}/evidence`
- `order_service.get_latest_ocr_evidence_run`
- `order_service._resolve_active_ocr_evidence_run`
- `order_service._load_active_ocr_payload`
- `order_service.get_ocr_output`
- `order_service.reconcile_ocr_rerun_state`
- `order_service.reconcile_completed_ocr_job`
- `ocr_evidence_service.persist_evidence_run`
- `ocr_evidence_service.backfill_evidence_run_from_cached_payload`

Failure classes:

- `read-triggered job/evidence mutation`
- `legacy cache/message/revision fallback promoted to current evidence`

Shared decision point:

- read helpers decide whether cache, message id, revision, or OCR job output should become persisted evidence/current evidence.

Required change:

- Remove `backfill_from_cache=True` from read paths.
- Split OCR read projection from explicit repair/recovery command.
- `persist_evidence_run` must require `template_version_id` in current workflow.
- `message_id` fallback, cache backfill, `_edited_ocr` merge, and position fallback must not become current evidence.
- OCR job state changes must be worker/command-only.

Invariant:

- persisted evidence is immutable.
- cache/revision cannot become current via GET.
- job state is not updated by page read.

Verification:

- `GET /orders/{id}`, `/document`, `/ocr-output`, `/evidence` leave `ocr_jobs`, `order_ocr_evidence_runs`, and `order_ocr_cache` unchanged.
- A cache-only order does not create evidence on read.
- Evidence without template version blocks current workflow.

### 3. Current Sheet / Workflow Projection Boundary

Classification: `書き直し`

Target files:

- `backend/src/services/workflow_state_service.py`
- `backend/src/services/order_service.py`
- `backend/src/services/critical_decision_service.py`
- `backend/src/services/order_current_state_service.py`

Target functions:

- `workflow_state_service.refresh_workflow_state`
- `workflow_state_service.project_workflow_state`
- `workflow_state_service._build_workflow_state_projection`
- `workflow_state_service._load_workflow_current_sheet_context`
- `order_service.refresh_current_sheet_context`
- `order_service.get_current_sheet_context`
- `order_service.build_initial_sheet_draft`
- `order_service.ensure_hakodate_evidence_draft_current`
- `order_service.get_order_workflow_state`
- `critical_decision_service.sync_pending_decisions`
- `order_current_state_service.persist_current_state`

Failure class:

- `projection materializes canonical state`

Shared decision point:

- projection currently decides whether workflow state, critical decisions, current state, or draft should be created/updated.

Required change:

- Projection cannot call decision sync, draft build/persist, or current state persist.
- Preview must be transient.
- Candidate choices must become rows only through explicit command.
- `refresh_workflow_state` should be split into pure projection and command-side materialization if still needed.

Invariant:

- projection does not insert/update/delete `order_workflow_states`, `order_current_states`, `order_sheet_drafts`, or `order_critical_decisions`.

Verification:

- Order with no workflow row and no draft returns blocker without writes.
- Repeated workflow/current-sheet reads leave current-state JSON and draft count unchanged.

### 4. Workflow-v2 GET Contract

Classification: `大幅修正必要`

Target files:

- `backend/src/api/orders.py`
- `backend/src/services/order_workflow_v2_service.py`

Target functions / endpoints:

- `GET /orders/{order_id}/workflow-v2`
- `GET /orders/{order_id}/workflow-v2/ocr-results`
- `GET /orders/{order_id}/workflow-v2/sheet`
- `GET /orders/{order_id}/workflow-v2/sheet-source`
- `GET /orders/{order_id}/workflow-v2/inspection`
- `order_workflow_v2_service._get_or_create_workflow`
- `order_workflow_v2_service.get_workflow`
- `order_workflow_v2_service.list_ocr_results`
- `order_workflow_v2_service.get_saved_sheet`
- `order_workflow_v2_service.build_sheet_from_selected_ocr`
- `order_workflow_v2_service.get_inspection`
- `order_workflow_v2_service._ensure_workflow_template_version_from_context`

Failure class:

- `workflow-v2 read bootstraps workflow/template lineage`

Shared decision point:

- workflow-v2 read path currently bootstraps workflow row and template lineage.

Required change:

- Workflow bootstrap belongs to context-confirm command.
- GET returns `not_initialized` / blocker when workflow row does not exist.
- `sheet-source` must be pure preview from fixed `selected_ocr_result_id` and `template_version_id`.
- Template version selection must not happen inside GET.

Invariant:

- workflow-v2 GET creates no workflow row, does not confirm template version, and does not update order/template pointers.

Verification:

- All workflow-v2 GET endpoints have zero canonical writes.
- Uninitialized order remains uninitialized after GET.
- Template-unresolved order returns blocker rather than building sheet-source.

### 5. Legacy Current-Workflow Path Removal

Classification: `破棄すべき`

Target files:

- `backend/src/api/orders.py`
- `backend/src/services/order_service.py`
- `frontend/src/pages/orders/[id].tsx`

Target endpoints / functions:

- legacy `draft-sheet`
- legacy `workflow-state`
- legacy `critical-decisions`
- legacy `ocr-pages`
- legacy `ocr-sheet`
- legacy `draft-sheet/candidate-preview`
- `api/orders.py._attach_reparse_sheet_state`
- `order_service.get_ocr_sheet`
- `order_service.get_candidate_draft_preview`

Failure class:

- `legacy side-channel can still produce or repair current state`

Required change:

- Legacy endpoints may only be archive/read-only compatibility.
- Current workflow must not call legacy helpers as current read path.
- Legacy reader must not contain `ensure`, `sync`, `reconcile`, `backfill`, or `get_or_create` behavior.

Invariant:

- Current authority is workflow-v2 commands and immutable artifact chain only.

Verification:

- Deprecated legacy GET remains 410 or archive-only.
- Archived legacy reader performs zero canonical writes.
- workflow-v2 does not depend on legacy current helpers.

### 6. Import-Time / Runtime Schema Mutation

Classification: `破棄すべき`

Target files include:

- `backend/src/services/order_service.py`
- `backend/src/services/facility_service.py`
- `backend/src/services/facility_template_version_service.py`
- `backend/src/services/order_workflow_v2_service.py`
- `backend/src/services/workflow_state_service.py`
- `backend/src/services/ocr_evidence_service.py`
- `backend/src/services/order_current_state_service.py`
- `backend/src/services/critical_decision_service.py`

Failure class:

- `service import mutates schema outside migration boundary`

Required change:

- Move all DDL and `_ensure_*_schema` behavior to migrations or explicit repair command.
- Service import and GET request cannot execute `Base.metadata.create_all`, `ALTER TABLE`, or schema repair.

Invariant:

- App boot and read request do not mutate schema.

Verification:

- Migrated DB app boot emits no DDL.
- GET endpoint contract tests show no DDL or canonical DML.

## DB / Model / Migration Remediation Units

### 1. Template Lineage Root

Classification: `大幅修正必要`

Target schema:

- `facility_template_versions`
- `facility_configs`
- `facilities`
- `orders.template_version_id`
- migration `0014_facility_template_versions.py`

Failure class:

- `canonical template source split / multiple active template / read-created template version`

Shared decision points:

- `ensure_active_template_version_from_resolved_config`
- `_ensure_active_version_for_backfill`

Required change:

- Make `facility_template_versions` the single canonical template source.
- Demote `facility_configs.config_json` template definitions from canonical source to operator settings or historical input.
- Require `orders.template_version_id` when an order enters current workflow.
- Stop read-side version creation before data migration.

Required constraints:

- unique active template per facility.
- duplicate digest prevention per facility.
- status enum/check.
- current-workflow orders must have non-null `template_version_id`.

Migration order:

1. Stop read-side version creation.
2. Audit active duplicates and digest duplicates.
3. Materialize active version only where unique source exists.
4. Backfill order/evidence/sheet lineage.
5. Add unique and non-null constraints.

Blockers:

- multiple active template versions.
- multiple resolved candidates.
- order facility does not match template version facility.

Verification SQL:

```sql
SELECT facility_id, COUNT(*)
FROM facility_template_versions
WHERE status = 'active'
GROUP BY facility_id
HAVING COUNT(*) <> 1;

SELECT o.id
FROM orders o
LEFT JOIN facility_template_versions ftv ON ftv.id = o.template_version_id
WHERE o.template_version_id IS NULL
   OR ftv.facility_id <> o.facility_code;
```

### 2. OCR Lineage Aggregate

Classification: `大幅修正必要`; `ocr_jobs` is close to `書き直し`

Target schema:

- `ocr_jobs`
- `order_ocr_evidence_runs`
- `uploaded_pdfs`
- `order_documents`

Failure class:

- `template-free OCR lineage / job-to-order linkage inferred by naming / cache-backed canonicalization`

Required change:

- Add explicit `order_id` and input artifact lineage to OCR jobs.
- Require `template_version_id` for current evidence.
- Demote `source='legacy-cache-backfill'` evidence to repair candidate.
- Add digest/FK consistency for uploaded PDF and order document.
- Stop inferring job identity from `OCR-{order_id}` naming.

Required constraints:

- current OCR jobs require `template_version_id`.
- current evidence runs require `template_version_id`.
- duplicate evidence digest prevention per order/template.
- uploaded PDF current order/document references must be explicit.

Migration order:

1. Audit null lineage jobs/evidence.
2. Backfill only when order/doc/upload source is uniquely provable.
3. Mark ambiguous rows `repair_blocked`.
4. Add non-null and uniqueness constraints for current workflow cohort.

Verification SQL:

```sql
SELECT COUNT(*)
FROM ocr_jobs
WHERE template_version_id IS NULL;

SELECT id, source
FROM order_ocr_evidence_runs
WHERE source = 'legacy-cache-backfill';
```

### 3. Current Sheet State Normalization

Classification: `書き直し`

Target schema:

- `order_sheet_drafts`
- `order_workflow_states`
- `order_current_states`
- `order_sheet_patch_candidates`
- `order_critical_decisions`
- migrations `0006` and `0013`

Failure class:

- `current-state duplication / JSON shadow state / read-side materialization`

Required change:

- Shrink or remove `order_current_states` as canonical state.
- Use immutable drafts plus a single current pointer.
- Stop treating `state_json` as canonical authority.
- Replace string lineage such as `baseline_revision_id` with artifact FK.
- Add context lineage to critical decisions.

Required constraints:

- current pointer unique per order.
- current drafts require template/evidence lineage.
- current patch candidates require base draft/evidence lineage.
- critical decisions unique by active decision type and context.

Migration order:

1. Compare pointer columns and JSON payloads.
2. Move matching rows to pointer read model.
3. Invalidate mismatches.
4. Add constraints.

Verification SQL:

```sql
SELECT order_id
FROM order_current_states
WHERE COALESCE(template_version_id, '') <> COALESCE(state_json->>'template_version_id', '');

SELECT id
FROM order_sheet_drafts
WHERE template_version_id IS NULL
   OR base_evidence_run_id IS NULL;
```

### 4. Confirmed Snapshot And Output Lineage

Classification:

- `order_confirmed_snapshots`: `大幅修正必要`
- output tables: `書き直し`

Target schema:

- `order_confirmed_snapshots`
- `order_lines`
- `bags`
- `label_rows`
- `delivery_notes`
- `manufacturing_aggregate_rows`
- `daily_output_portion_overrides`

Failure class:

- `downstream artifact without immutable upstream pointer`

Required change:

- Make bagging/output depend on exactly one confirmed snapshot or output bundle.
- Add `confirmed_snapshot_id` or `output_bundle_id` to output tables.
- Add snapshot FK and line digest to `order_lines`.
- Scope overrides to output bundle/date.

Migration order:

1. Backfill only outputs with provable 1:1 snapshot relation.
2. Archive or block unprovable outputs.
3. Add orphan-output prevention.
4. Add output-scope uniqueness.

Verification SQL:

```sql
SELECT b.id
FROM bags b
LEFT JOIN order_confirmed_snapshots s ON s.order_id = b.order_id
WHERE s.id IS NULL;

SELECT order_id, date, COUNT(*)
FROM delivery_notes
GROUP BY order_id, date
HAVING COUNT(*) > 1;
```

### 5. Legacy Cache / Revision Demotion

Classification: `破棄すべき`

Target schema:

- `order_ocr_cache`
- `order_ocr_revisions`

Failure class:

- `diagnostic artifact promoted to canonical state`

Required change:

- Remove current workflow read authority from cache/revision tables.
- Keep only archive/read-only diagnostic access if necessary.
- Repair must produce candidate and require explicit apply.

Blocker:

- order that has only cache/revision and no persisted evidence/draft.

Verification SQL:

```sql
SELECT c.order_id
FROM order_ocr_cache c
LEFT JOIN order_ocr_evidence_runs e ON e.order_id = c.order_id
WHERE e.id IS NULL;

SELECT r.order_id
FROM order_ocr_revisions r
LEFT JOIN order_sheet_drafts d ON d.order_id = r.order_id
WHERE d.id IS NULL;
```

### 6. Migration / Audit Framework

Classification:

- migration boundary: `書き直し`
- runtime mutators: `破棄すべき`

Target:

- migrations `0006`, `0013`, `0014`
- `scripts/apply_facility_template_version_migration.py`
- `scripts/backfill_facility_template_version_lineage.py`
- `audit_logs`
- import-time `create_all`
- runtime `ALTER TABLE`

Failure classes:

- `migration bypass`
- `schema drift by service import`
- `unaudited repair`

Required change:

- DDL only through migrations.
- Data migration only updates rows with unique source resolution.
- Every repair/migration writes actor, reason, before/after digest, source ids, and idempotency key.

Migration order:

1. Stop runtime mutators.
2. Run preflight row audit.
3. Materialize canonical sources.
4. Backfill lineage.
5. Mark invalidated/block rows.
6. Apply strict DDL.
7. Verify GET/read zero write.

Required tests:

- empty DB -> migrations only -> app boot.
- snapshot DB -> GET only -> row counts and updated_at unchanged.
- ambiguous facility/template -> blocker and zero writes.

## Frontend / API Contract Remediation Units

### 1. Step1 Context And Facility Template Authority

Classification: `大幅修正必要`

Target files:

- `frontend/src/pages/orders/[id]/workflow-v2.tsx`
- `frontend/src/pages/facilities/[id].tsx`
- `backend/src/api/facilities.py`
- `backend/src/services/facility_service.py`

Failure class:

- `read-side canonical mutation`

Required UI state:

- `context_unresolved`
- `facility_template_unresolved`
- `context_confirmed`

Required change:

- Step1 displays facility, delivery week, and template status.
- Step1 commands confirm context.
- Facility/template admin commands live outside normal order read flow.
- `GET /facilities/{id}` becomes read-only.

Verification:

- Facility page load does not change active template or order context.
- workflow-v2 first load does not create or rotate template version.

### 2. Workflow-v2 Main Read Model

Classification: `大幅修正必要`

Target files:

- `frontend/src/pages/orders/[id]/workflow-v2.tsx`
- `backend/src/api/orders.py`
- `backend/src/services/order_workflow_v2_service.py`

Failure class:

- `read path creates/updates workflow state`

Required UI states:

- `uploaded`
- `context_confirmed`
- `ocr_blocked`
- `ocr_ready`
- `ocr_running`
- `ocr_completed`
- `sheet_saved`
- `bagging_ready`
- `confirmed`

Required change:

- `GET /orders/{id}/workflow-v2` is authoritative read projection only.
- It does not create workflow row, write suggestions, or refresh prerequisite state.
- Workflow state changes only after command completion.

Verification:

- Repeated refresh, second tab, inspection transition do not change workflow row, blockers, or state.
- Page headline and blockers are stable across list/detail/inspection.

### 3. Step2 OCR Selection And Sheet Source Boundary

Classification: `大幅修正必要`

Target files:

- `frontend/src/pages/orders/[id]/workflow-v2.tsx`
- `backend/src/api/orders.py`
- `backend/src/services/order_workflow_v2_service.py`

Failure class:

- selected OCR and projected sheet boundary is unclear.

Required UI states:

- `ocr_result_none`
- `ocr_result_selected`
- `sheet_preview_ready`
- `sheet_preview_blocked`

Required change:

- `GET /workflow-v2/ocr-results` lists candidates only.
- `POST /workflow-v2/ocr-results/{id}/select` selects exactly one current OCR result.
- `GET /workflow-v2/sheet-source` previews from selected OCR and fixed template without saving.
- Legacy candidate preview does not coexist as current source.

Verification:

- Selecting OCR changes selected pointer only through command.
- GET sheet-source does not create draft.
- Page, inspection, and confirm use same selected OCR id.

### 4. Step3 Sheet Save And Template Column Change Separation

Classification: `大幅修正必要`

Target files:

- `frontend/src/pages/orders/[id]/workflow-v2.tsx`
- `backend/src/services/order_workflow_v2_service.py`

Failure class:

- admin schema change and operator sheet save are mixed.

Required UI states:

- `sheet_preview`
- `sheet_dirty`
- `sheet_saved`
- `template_columns_unresolved`
- `template_columns_changed_requires_rerun`

Required change:

- `PUT /workflow-v2/sheet` saves operator sheet.
- `PUT /workflow-v2/facility-template-columns` is admin/repair command.
- Preview and saved draft are visually and contractually distinct.
- Template column changes explicitly invalidate OCR/downstream artifacts.

Verification:

- Sheet save advances draft lineage only.
- Template column change advances template lineage and invalidates downstream only through explicit command.

### 5. Step4-5 Downstream Truth

Classification: `大幅修正必要`

Target files:

- `frontend/src/pages/orders/[id]/workflow-v2.tsx`
- `frontend/src/pages/orders/[id]/inspection-v2.tsx`
- `backend/src/services/order_workflow_v2_service.py`

Failure class:

- bagging/output/confirm state lacks visible saved-sheet lineage.

Required UI states:

- `bagging_ready`
- `bagging_completed`
- `output_review_ready`
- `confirm_blocked`
- `confirmed`

Required change:

- inspection/read model returns authoritative `saved_sheet_id`, `bagging_result_id`, `output_bundle_id`, and `confirmed_snapshot_id`.
- Step4 and Step5 display source lineage.
- Confirm gate is based on a single blocker source.

Verification:

- workflow-v2, inspection-v2, orders list, and post-confirm detail agree on saved sheet lineage, blocker, and confirmed status.

### 6. Legacy Order Detail Authority Removal

Classification:

- `frontend/src/pages/orders/[id].tsx`: `書き直し`
- legacy current workflow authority: `破棄すべき`

Failure class:

- `mixed-truth legacy screen`

Required change:

- Current workflow operations live in workflow-v2 only.
- Legacy detail becomes archive/read-only or redirects to workflow-v2.
- Legacy endpoints `/draft-sheet`, `/workflow-state`, `/critical-decisions`, `/ocr-sheet` do not provide current truth.

Verification:

- User cannot complete current workflow from legacy detail.
- workflow-v2 alone contains state/headline/blockers.

### 7. Orders List Visible State Consistency

Classification: `少し修正必要`

Target files:

- `frontend/src/pages/orders/index.tsx`
- `backend/src/api/orders.py`

Failure class:

- transient dual truth in list hydration.

Required change:

- `/orders` returns a single current workflow summary per order.
- List does not show one state initially and overwrite it with a second truth later.
- List does not use legacy fallback headline fields.

Verification:

- Initial list render, list refresh, and detail transition show identical state/headline/blocker/next action.

## Cross-Cutting Implementation Order

Do not start by editing the monolith in place.

Recommended order:

1. Add static and runtime guards for GET/write and import-time DDL.
2. Stop read-side facility/template creation.
3. Stop read-side evidence/cache backfill.
4. Split workflow-v2 read projection from commands.
5. Introduce strict lineage constraints behind row audits.
6. Move frontend to single workflow-v2 authoritative read model.
7. Demote legacy order detail to archive/read-only.
8. Add output bundle / confirmed snapshot lineage.
9. Apply strict DB constraints.

## Verification Matrix

| Area | Required verification |
|---|---|
| GET/read purity | canonical table digests unchanged after every GET route. |
| Facility/template | active template count stable after facility page and order page reads. |
| OCR evidence | cache-only order does not create evidence on read. |
| Workflow-v2 | uninitialized order remains uninitialized after GET and shows blocker. |
| Sheet preview/save | preview GET creates no draft; save command creates one immutable draft. |
| Step4/5 | bagging/output/confirm all point to same saved sheet lineage. |
| Legacy | legacy detail cannot become current workflow authority. |
| DB migration | ambiguous rows are blocked, not auto-repaired. |
| UI parity | orders list, workflow-v2, inspection-v2, and final detail show same state/headline/blocker. |

## Open Items Before Implementation

- Define exact new tables or columns for output bundle and bagging result lineage.
- Decide whether `order_current_states` is deleted or reduced to pointer-only read model.
- Define repair UI for legacy cache/revision-only orders.
- Define migration handling for prod rows that cannot prove unique template/evidence lineage.
- Decide whether legacy detail redirects immediately or remains archive read-only for a transition period.
