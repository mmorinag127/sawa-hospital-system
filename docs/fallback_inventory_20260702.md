# Fallback Inventory 2026-07-02

Scope: active runtime/deploy roots under `backend/src`, `ocr_pipeline/app`, `frontend/src`, `frontend/Dockerfile`, `.github/workflows`, and `Taskfile.yml`.

## Removed Or Blocked

- `frontend/src/pages/api/[...path].ts`, `frontend/Dockerfile`: removed hardcoded production API proxy fallback. Missing `API_PROXY_TARGET` now blocks with `api_proxy_target_missing`.
- `backend/src/services/week_candidate_service.py`: removed calendar-week candidate generation from week option/current-week resolution. Week choices are menu-backed only.
- `backend/src/services/uploaded_pdf_service.py`, `backend/src/services/order_service.py`: removed filename/calendar-derived week hints. Unresolved week is not silently inferred.
- `backend/src/services/fax_extractor.py`, `backend/src/services/order_service.py`, `backend/src/services/config_validator.py`, `frontend/src/pages/facilities/[id].tsx`: disabled OpenAI/Gemini provider fallback to pipeline. Config accepts only `none`; UI only exposes `none`.
- `backend/src/services/order_service.py`: disabled row-index quantity projection fallback by default and force-disabled it in sheet projection.
- `backend/src/services/order_service.py`, `backend/src/services/ocr_sheet_revision_service.py`, `backend/src/services/draft_sheet_service.py`: removed `fallback_sheet` overlay/rebase paths. Revision/draft sheet payloads are built from the selected revision/draft source only.
- `backend/src/services/order_service.py`: removed legacy/cache/job fallback paths from `get_ocr_output`, `get_ocr_pages`, canonical bootstrap sheet, and legacy first-pass merge.
- `backend/src/services/candidate_resolution_service.py`, `backend/src/services/workflow_state_service.py`, `backend/src/services/order_service.py`, `backend/src/services/position_column_mapping_service.py`: removed active position-based payload augmentation. Position fallback metadata can no longer create or overwrite OCR quantities.
- `backend/src/services/apply_gate_service.py`: removed stale issue suppression that allowed position fallback/authoritative sheet state to hide OCR blockers.
- `backend/src/services/ocr_evidence_service.py`: blocked false recovery by allowing unresolved `template_version_id` to persist when the facility/template is not yet resolved, while still validating facility match when a template version is provided.
- `backend/src/services/ocr_job_service.py`, `backend/src/services/order_service.py`, `backend/src/services/workflow_state_service.py`: restored canonical OCR rerun job-state handling for `OCR-{order_id}` and completed-output reconciliation. This is not OCR value fallback; it is the regular job state source.
- `ocr_pipeline/app/postprocess.py`: removed alternate-crop OCR fallback for facility name, menu band, and notes. A missing primary crop result now stays missing instead of being replaced by another crop.
- `ocr_pipeline/app/template_match.py`: removed local/backend template path fallback. A missing configured template image path now fails instead of silently resolving another copy.

## Proved Non-Target

- `backend/src/services/hakodate_physical_menu_row_service.py`: `fallback_index` is only a deterministic row-id/key suffix for physical menu rows, not a quantity/OCR inference fallback.
- `backend/src/services/master_order_form_template_service.py`, `backend/src/services/facility_template_version_service.py`: `fallback_index` is a deterministic column-name/id suffix when importing structured template columns, not runtime OCR/result substitution.
- `backend/src/services/hakodate_step_review_pipeline_service.py`: geometric snap fallback belongs to the explicit 4-point/header/row-intersection correction path. It does not infer facility/week/menu count or substitute OCR quantities.
- `ocr_pipeline/app/structured_issues.py`, `ocr_pipeline/app/yomitoku_runner.py`, `ocr_pipeline/app/issue_detection.py`, `ocr_pipeline/app/quantity_subgrid.py`: `row_index` fields are OCR table coordinates/diagnostics, not row-index quantity projection fallback.
- `frontend/src/pages/orders/[id].tsx`: remaining `fallback` strings are UI preview labels, focus/navigation defaults, or display-only low-confidence labels. They do not persist OCR quantities or choose facility/week/template.
- `frontend/src/features/*`, `frontend/src/pages/shipping-history.tsx`: fallback values are shipping/date display defaults outside OCR/order correctness path.
- `backend/src/api/ocr_registry.py`, `backend/src/api/orders.py`, `backend/src/api/facilities.py`: legacy endpoints are blocked with 410/disabled responses, not active fallback execution.
- `backend/src/hakodate_best_method_runtime/*`: benchmark/render helper runtime is not copied by the deploy workflow for backend service execution.
