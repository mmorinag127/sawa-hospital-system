---

description: "Task list for Facility OCR Pipeline implementation"
---

# Tasks: Facility OCR Pipeline

**Input**: Design documents from `specs/001-ocr-pipeline-update/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Contract/integration tests for ingest -> facility resolution -> template selection -> OCR -> outputs are REQUIRED by the constitution (including retries, duplicate handling, and backlog recovery). Add unit tests as needed.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story. PC-only UX and status vocabulary are maintained by existing UI conventions.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 [P] Align template registry fixtures in `backend/src/data/fax_templates.yaml` with match/warp/roi/postprocess fields and add at least one sample template
- [ ] T002 [P] Extend config validation for template registry fields in `backend/src/services/config_validator.py`
- [ ] T003 [P] Confirm facility prompt merging and template overrides in `backend/src/services/config_service.py` (add missing fields as needed)

---

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T004 Add OCR job tracking model and migration in `backend/src/models/ocr_job.py`, `backend/src/models/__init__.py`, `backend/migrations/`
- [ ] T005 Implement job lifecycle helpers (create, update, dedupe) in `backend/src/services/ocr_job_service.py`
- [ ] T006 Add artifact storage helpers for OCR outputs/unclassified inputs in `backend/src/services/storage_service.py`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Auto-extract weekly order data (Priority: P1) 🎯

**Goal**: Produce structured, template-aligned OCR output for single-page weekly order PDFs.

**Independent Test**: Run an ingest on a known single-page PDF and verify JSON output with quantity grid and notes.

### Tests for User Story 1

- [ ] T007 [P] [US1] Contract test for OCR output shape in `backend/tests/contract/test_ocr_output.py`
- [ ] T008 [P] [US1] Integration test for ingest -> output flow in `backend/tests/integration/test_ocr_pipeline.py`

### Implementation for User Story 1

- [ ] T009 [P] [US1] Implement PDF render + preprocess helpers in `backend/src/services/pdf_render.py` and `backend/src/services/preprocess.py`
- [ ] T010 [P] [US1] Implement template match + warp in `backend/src/services/fax_template_matcher.py`
- [ ] T011 [P] [US1] Implement ROI cropping + quantity extraction in `backend/src/services/fax_roi_extractor.py`
- [ ] T012 [US1] Integrate OCR provider selection + prompts in `backend/src/services/fax_extractor.py`
- [ ] T013 [US1] Wire pipeline into ingest/reparse flow in `backend/src/services/order_service.py`, `backend/src/api/orders.py`, `backend/src/workers/ingest_worker.py`
- [ ] T014 [US1] Persist OCR outputs and audit logs in `backend/src/services/storage_service.py` and `backend/src/lib/logging.py`

**Checkpoint**: User Story 1 fully functional and testable independently

---

## Phase 4: User Story 2 - Handle unknown templates (Priority: P2)

**Goal**: Flag and preserve inputs that fail template matching.

**Independent Test**: Ingest a PDF that does not match any template and verify unclassified status + artifacts.

### Tests for User Story 2

- [ ] T015 [P] [US2] Integration test for unclassified handling in `backend/tests/integration/test_ocr_unclassified.py`

### Implementation for User Story 2

- [ ] T016 [US2] Add unclassified path + artifact dump in `backend/src/services/fax_extractor.py` and `backend/src/services/storage_service.py`
- [ ] T017 [US2] Expose unclassified status in order APIs in `backend/src/api/orders.py`

**Checkpoint**: User Story 2 independently functional

---

## Phase 5: User Story 3 - Highlight low-confidence cells (Priority: P3)

**Goal**: Surface failed cells for review.

**Independent Test**: Process a PDF with unreadable cells and verify failed cell list is shown in output and UI.

### Tests for User Story 3

- [ ] T018 [P] [US3] Contract test for failed cell reporting in `backend/tests/contract/test_ocr_failed_cells.py`

### Implementation for User Story 3

- [ ] T019 [US3] Include failed cell metadata in outputs in `backend/src/services/order_service.py` or `backend/src/services/output_builder.py`
- [ ] T020 [US3] Display failed cells in order detail UI in `frontend/src/pages/orders/[id].tsx`

**Checkpoint**: User Story 3 independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T021 [P] Update runbook notes in `docs/` if needed for OCR pipeline operations
- [ ] T022 [P] Add retry/backlog safety checks in `backend/src/services/ingest_policy.py` and `backend/src/workers/ingest_worker.py`
- [ ] T023 [P] Run quickstart validation steps and record outcomes in `specs/001-ocr-pipeline-update/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational completion
- **Polish (Phase 6)**: Depends on desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Starts after Foundational; no dependencies on other stories
- **User Story 2 (P2)**: Starts after Foundational; integrates with US1 outputs
- **User Story 3 (P3)**: Starts after Foundational; consumes US1 outputs

### Parallel Opportunities

- Tasks marked [P] can run in parallel within their phase
- Tests per user story can be written in parallel before implementation
