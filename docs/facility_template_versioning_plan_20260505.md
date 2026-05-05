# Facility Template Versioning Plan

Date: 2026-05-05

## Problem

Facility template data was split across multiple mutable sources:

- `facility_configs.fax_template_id`
- `facility_configs.fax_template_override.columns`
- OCR evidence payloads
- saved draft sheets
- bagging/output snapshots
- legacy template candidate approval paths

This made it possible to edit a facility template while old OCR evidence, saved sheets, and bagging output still looked selectable. The visible failure class is `template canonical source split + non-atomic downstream invalidation`.

## Target Model

The canonical template source is `facility_template_versions`.

Each active version stores:

- `facility_id`
- `template_id`
- normalized visible columns
- stable `column_id`
- semantic payload for each quantity column
- `template_digest`
- validation result

Every downstream artifact must carry the same `template_version_id`:

- order
- OCR job
- OCR evidence run
- saved sheet draft
- current state
- confirmed snapshot

If a downstream artifact has a missing or different `template_version_id`, workflow-v2 must block instead of guessing.

## Column Semantics

Visible column definitions are normalized into stable column records:

- `column_id` is generated from source index and role when missing.
- non-quantity columns are excluded from aggregation.
- quantity columns keep `diet_type`, `area_id`, and `bag_type`.
- placeholder/unknown quantity columns are read targets but excluded from aggregation.

This keeps totals, bagging, and output behavior compatible with existing downstream semantics while removing the need for the user to edit internal names manually.

## Mutation Rules

Allowed mutation paths:

- `PUT /facilities/{facility_id}/fax-template`
- `PUT /orders/{order_id}/workflow-v2/facility-template-columns`

Blocked mutation paths:

- legacy order facility-template-columns service path
- legacy Hakodate template candidate approval path
- generic facility config endpoint when it attempts to change template definition keys

On template column edit for an order:

- create a new active facility template version
- archive previous active versions
- update the order/workflow template version
- delete OCR evidence, saved sheets, current state, bagging, output, and confirmed snapshots derived from the old version
- return to Step1/Step2 rerun state

## Live Data Migration

The migration adds the new table and lineage columns without deleting existing data.

Existing facilities get an active template version lazily when:

- facility detail is read
- workflow-v2 context is confirmed
- OCR is queued

Existing downstream artifacts without `template_version_id` are intentionally not auto-adopted during selection. They are treated as mismatched once a workflow has a confirmed version, forcing OCR regeneration and preventing stale evidence from entering the sheet.

## Verification Plan

Required checks:

- Unit: workflow context confirmation creates/uses a template version.
- Unit: OCR job, evidence, draft, current state, bagging, and confirmed snapshot preserve the same template version.
- Unit: selecting mismatched OCR evidence blocks with `template_version_mismatch`.
- Unit: saving facility columns creates a new version and deletes derived OCR/sheet/bagging/output state.
- Contract: facility template registration returns a template version and resolved config references the same version.
- Contract: generic facility config update cannot modify template definition keys.
- Compile: modified backend services and API modules compile.

## Implementation Plan

1. Add `facility_template_versions` as the canonical version table.
2. Add nullable `template_version_id` lineage to order, OCR job, OCR evidence, saved sheet, current state, workflow state, and confirmed snapshot tables.
3. Normalize visible template columns into records that carry both stable `column_id` and existing downstream semantics.
4. Route template registration and workflow-v2 facility column saves through the version service.
5. Block direct template-definition writes through generic facility config mutation.
6. On version mismatch, block workflow-v2 sheet generation and selection with `template_version_mismatch`.
7. Keep bagging/output compatibility by preserving existing `diet_type`, `area_id`, `bag_type`, and aggregation semantics.

## Implemented Mapping

- Model/migration: `backend/src/models/facility_template_version.py` and `backend/migrations/0014_facility_template_versions.py`.
- Canonical service: `backend/src/services/facility_template_version_service.py`.
- Workflow lineage: `backend/src/services/order_workflow_v2_service.py`.
- Evidence/job/draft/current lineage: `ocr_evidence_service.py`, `ocr_job_service.py`, `draft_sheet_service.py`, and `order_current_state_service.py`.
- Facility API path: `backend/src/api/facilities.py` uses the version service for template registration.
- Legacy stop paths: generic facility config template edits and legacy Hakodate candidate approval are blocked instead of mutating hidden template state.

## Executed Verification

- `python -m py_compile ...` on changed backend model/service/API/test modules.
- `backend/.venv/bin/python -m pytest backend/tests/unit/test_order_workflow_v2_service.py -q` -> 29 passed.
- `backend/.venv/bin/python -m pytest backend/tests/integration/test_facility_config_apply.py -q` -> 38 passed.
- `backend/.venv/bin/python -m pytest backend/tests/contract/test_facility_config.py -q` -> 3 passed.
- `backend/.venv/bin/python -m pytest backend/tests/integration/test_draft_sheet_service.py::test_build_initial_sheet_draft_from_latest_evidence_run backend/tests/integration/test_draft_sheet_service.py::test_get_current_sheet_context_does_not_persist_semantic_refresh_on_read -q` -> 2 passed.
- `git diff --check` -> passed.

Known follow-up:

- Historical tests that directly use legacy facility-template-columns endpoints must be retired or rewritten to workflow-v2 endpoints.
