# H1 JSON Dated Lineage Resolver Source Unit

WT: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h1-version-resolution`
Branch: `codex/modernization-h1-version-resolution-20261007`
Base: `da40d6ac9c4037871e9dcb588902ba79839507a1`

## Canonical Read and Explicit Write

Failure class: DB active/lazy/backfill authority, global storage revision used as semantic
lineage, synthetic cells, read-side writes and cached ORM projection verification.
Invariant: one explicit date selects one complete authored facility/version from one JSON
snapshot; semantic lineage belongs to that specific facility/version/content, not the
global file revision. DB is only immutable FK support. Null/zero/unknown data are preserved.

Read sources: applicable AGENTS; approved modernization plan section 127; preceding dated
JSON contract/resolver; FacilityTemplateVersion/13 FK models; migrations 0014/0016/0025.
Inclusive periods retained. No week boundary policy or date selection is invented.

Actual read chain: facility_template_version_service.resolve_facility_config_for_date ->
canonical_facility_lineage.resolve -> facility_master_service.resolve_facility_for_date ->
one get_snapshot and dated complete-version validation. It performs NO Session lookup,
execute, add, flush or commit. Missing/invalid dates, overlap/gaps/unrecorded history and
incomplete templates stop through the common JSON validator.

Return tuple remains (full compatible config, attribute-compatible descriptor, error).
Descriptor is not a persisted ORM row and is explicitly materialized=False; serialization
marks canonical_selection. Its deterministic technical PK is NOT evidence a FK exists.
Before any of the 13 model writes, the next caller unit must explicitly invoke
materialize_facility_version_lineage(session, selection) and adopt that own returned row.
get_active compatibility exports use the same read-only JSON selection, not DB status.

## Two Separate Proofs

Semantic identity: facility_id + operator authored_version_id + SHA256 of complete authored
version (periods, full facility/template, null/zero/unknown metadata). Technical PK =
CFV + SHA256(identity), template_digest = SHA256(identity). Same selected version/content
under a changed global JSON revision has the SAME lineage. Different content, validity,
authored identity or facility changes it. Authored IDs are only facility scoped.

canonical_lineage carries this semantic receipt, full authored_version, projection_id and
materialized status. canonical_source_proof carries the ACTUAL own read's revision,
source=canonical_json and effective_date. This is editor CAS/storage proof, not semantic
identity. Projection validation_json.creation_source_proof records its ORIGINAL creation
read proof; a reuse never rewrites it to pretend the row was created from the current read.

Existing config_json is exact facility content; columns_json mirrors authored columns.
cells absence is NOT []: full receipt/config retain absence; cells_presence distinguishes
absent/null/present; nullable cells_json uses None for absence or authored null, with that
explicit marker, and [] only if actually authored. Serializer omits absent cells and
preserves authored null/empty. authored_template_digest hashes exact template JSON,
including presence. No cells default or normalization is used in the canonical contract.
The old standalone template_digest utility remains for legacy callers only.

No model/migration is required. Migration 0016's active-per-facility and facility+digest
constraints are preserved: mirrors are archived technical FK rows, not business validity
decisions. DB status neither selects nor overrides JSON. Technical insertion created_at
is not a historical start; no activated_at, authored dates or version IDs are fabricated.

## Exact Materialization and Fresh Database Verification

materialize takes the fixed own selection, verifies its semantic receipt, original full
facility working input, explicit effective date and nonempty read proof. It does NOT
reread JSON or take a latest token. Caller Session must have no new/dirty/deleted objects;
otherwise it stops BEFORE SQL, preserving those objects. Callers materialize first, then
perform business writes in the same caller-owned transaction.

It locks the exact parent facility FK row and reads the exact derived projection PK with
FOR UPDATE. SELECT targets Core table columns and mappings, not ORM Session.get/entity
identity-map objects; reads are under no_autoflush. Existing rows are validated against
the selected semantic receipt, full body, periods, technical PK/digest and cells marker.
No refresh(populate_existing)/expire discards a pending ORM mutation. read_projection
also uses fresh Core columns/no_autoflush for read-only history.

Missing mirror is inserted explicitly via Core INSERT RETURNING in the caller transaction,
returning its own stored row; no separate commit/rollback, ORM flush or latest query.
Parent lock serializes missing-row creation. Constraint conflict explicitly stops with
projection_conflict, without retry; caller must roll back the failed transaction.
Read-only selection works even if no parent/mirror exists; explicit materialization
requires the parent and stops if missing. Old/tampered/missing provenance is not repaired.

## Removed or Stopped Reentry

- active compatibility selectors: JSON read only, explicit date required.
- ensure_active_template_version_from_resolved_config: disabled; old current stamp removed.
- backfill/private missing-lineage stamp helpers: disabled; no cached/current history fill.
- _resolved_config_from_parts: disabled; DB/root-base merges and defaults removed.
- old column/template registration writers: disabled; unreachable writer bodies removed.
- source-index repair: disabled; compatibility column normalization validates/deep-copies
  exact authored columns, without enrichment. Unused guess/semantic/date/latest helpers removed.
- template_version_applies_on: provenance/date check only, not canonical source selection.
- explicit legacy history: fresh ID read marked legacy_readonly_history, never adoption.
- existing startup schema readiness check remains separate; no lazy data initialization.

## Actual Caller Inventory and Next Unit

Caller sources inspected read-only; none below is written in this unit:

| Actual caller | Required adoption |
| --- | --- |
| order_service.py register OCR evidence ~9794-9806 | Replace stopped ensure_active/current config stamping with explicit authoritative date + common resolver in caller transaction; retain returned receipt and PK, no guess from current config. |
| order_workflow_v2_service.py ~783 | Compatibility resolver now JSON dated; supplied effective date must be operator/canonical source, not unresolved week policy. |
| order_workflow_v2_service.py ~1693/1709/1715 | Delete lazy sync branch; use one selected snapshot instead of repeated calls/current config or requested-template normalization. Confirm date semantics before multi-day document adoption. |
| order_workflow_v2_service.py ~765/1171/1357 | Pinned row/config consumers must verify canonical receipt, not status=active or columns-only validation. Preserve exact authored values; legacy rows stop. |
| output_builder.py ~291-320 | Existing dated resolver call is wired through common entry, but pinned ID vs current receipt and undated fallback path still require explicit historical/reprint contract. Do not silently switch old order to current revision. |
| api/facilities.py ~83 | Undated get_active no longer selects DB default. UI legacy reference must use explicit read-only history, or an explicit selected date for current canonical view. |
| scripts/backfill_facility_template_version_lineage.py ~34 | Stopped export. Historical provenance repair requires verified historical source; no current-date/source-index reconstruction. |
| ocr_job_service.py ~308; ingest_worker.py | Job creation interface currently accepts optional PK; thread the same selected receipt/PK from registration, reject missing provenance, no independent facility resolution. |
| ocr_evidence_service.py ~521-524 | Remove order/latest PK fallback for provenance adoption; verify receipt/target/date of intended candidate/current source. |
| order_service.py ~12156-12172/12853+ | Draft/evidence/current-state materialization must not fill missing lineage from latest/current sibling; use own selected receipt. |
| order_output_artifact_service.py ~16/42; output_builder output saves | Preserve same receipt/PK through bagging/output and verify existing artifact identity rather than rebase to latest config. |
| order_pipeline_lineage_audit_service.py and repair_service.py | Extend identity checks beyond equal technical PK to canonical receipt/digests; repair cannot auto-fill old provenance. |
| main.py ~81 | Existing explicit schema readiness check retained; no dated data initialization. |
| canonical_facility_store.py ~96 | Pure production column validator reused; no session/read/normalization added by this unit. |

13 actual FK models and adoption points:
1. Order: explicit dated registration/context receipt, keep own PK.
2. OcrJob: job input inherits that receipt, never missing/latest stamp.
3. OrderOcrEvidenceRun: candidate/current input receipt and output match.
4. OrderSheetDraft: selected evidence and operator working base receipt.
5. OrderWorkflowState: context/saved head and metadata receipt parity.
6. OrderCurrentState: recorded saved/current provenance, no fallback precedence.
7. OrderConfirmedSnapshot: immutable confirmation receipt.
8. OrderBaggingResult: saved-sheet/evidence receipt match.
9. OrderOutputBundle: bagging/confirmed receipt match.
10. Bag: same bagging/source order receipt.
11. LabelRow: same output/order receipt.
12. DeliveryNote: same output/order receipt.
13. ManufacturingAggregateRow: same explicit per-order/day receipt; no arbitrary aggregate
    date or week-boundary choice.

Undated config/OCR/template/output callers listed in the preceding H1 dated doc remain
next-unit work too. Existing standalone normalize_template_columns callers must handle
explicit invalid authored input instead of relying on enrichment. Legacy historical
reprint is separate from live resolution; verified stored receipts can be inspected, but
unknown old provenance is not made canonical. Week-boundary policy stays unresolved.


## Source Cases and Freeze

Source cases cover inclusive date and single JSON read with zero DB calls; unrelated root
revision retains lineage/current proof while immutable creation proof stays original;
changed selected content changes lineage; absent/null/[] cells; fresh actual DB mismatch
despite matching identity-cache object; pending new/dirty/deleted no autoflush/discard;
fresh Core read leaves dirty cached working values untouched; missing parent/constraint
conflict no retry; missing/invalid/history gap/overlap/incomplete; old provenance/lazy
backfill disabled; same authored ID across facilities; changed working input or invalid
source proof before writes.

Changed paths: facility_template_version_service.py, canonical_facility_lineage.py,
backend/tests/unit/test_canonical_facility_lineage.py, this document. Source cases are
not executed. Diff checks and staging only; no runtime/test/build/DB/network/commit.
Old JSON WT and H2/order_service/orders.py/ingest_worker/workflow/output_builder untouched.
Parent source audit, actual dated arguments/proof adoption across all 13 models,
verified historical/reprint contract and week-boundary choice remain next-unit work.
