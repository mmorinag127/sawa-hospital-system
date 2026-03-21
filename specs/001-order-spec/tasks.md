# Tasks: 病院・施設 発注FAX自動取込〜出力 (MVP)

**Input**: Design documents from `/specs/001-order-spec/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Contract/integration tests for ingest -> facility resolution -> menu mapping -> outputs are REQUIRED (OCR retries, duplicate replacement, zero suppression, change-column precedence, backlog recovery). Add unit tests as needed.

**Organization**: Tasks grouped by user story to enable independent implementation and testing. PC-only UX with single確定動作、status vocabulary (未着/要確認/確定/エラー) and performance/resilience (ingest/output ≤2min, backlog catch-up 30min @100施設/日) must be checked.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Create backend/.env.example with DB_URI, REDIS_URI, STORAGE_URI/PATH, TESSDATA_PREFIX, AUTH settings (admin OAuth, operator basic) and JST timezone.
- [x] T002 Provision docker-compose.yml in infra/docker-compose.yml for PostgreSQL, Redis, MinIO (or mount path) and bind storage paths for PDFs/outputs.
- [x] T003 Install OCR deps (Tesseract Japanese, pdfplumber prerequisites) and document steps in docs/ocr-setup.md.
- [x] T004 Configure mail ingress mock/adapter in backend/src/workers/ingest_mail_adapter.py to accept message_id, pdf_uri, received_at.
- [x] T005 [P] Scaffold frontend/ (React/Next.js) with lint/format/test setup and base API client in frontend/src/services/apiClient.ts.
- [x] T006 [P] Scaffold backend/ (FastAPI + Celery) with lint/format/test setup in backend/pyproject.toml and backend/src/main.py.

## Phase 2: Foundational (Blocking)

- [x] T007 Create DB migrations for all entities in backend/migrations/ (Facility, FacilityArea, FacilityMasterConfig, WeeklyMenu, MenuItem, OrderDocument, Order, OrderLine, Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow, User, AuditLog, Notification).
- [x] T008 Implement auth/RBAC middleware in backend/src/api/auth.py (admin OAuth, operator basic) and enforce roles on routes.
- [x] T009 Implement storage service in backend/src/services/storage_service.py for PDF/input saves and output retrieval (URIs).
- [x] T010 Implement logging/audit utilities in backend/src/lib/logging.py to include FAC/WEK IDs and write AuditLog entries.
- [x] T011 Configure Celery queues/workers in backend/src/workers/__init__.py for ingest and exports with retry/backoff.
- [x] T012 [P] Add health/backlog endpoint skeleton in backend/src/api/health.py exposing queue depth/oldest pending.

**Checkpoint**: Foundation ready; user stories may start.

## Phase 3: User Story 1 - 添付PDFを即時取込し要確認で一覧化 (Priority: P1) 🎯

**Goal**: 受信PDFを即時取り込み、施設×週の注文を生成し要確認で一覧表示する。  
**Independent Test**: 転送メールのPDFを投入し、要確認一覧に表示＆施設未確定は選択待ちになる。

### Tests
- [x] T013 [US1] Contract test ingest -> create OrderDocument/Order (未着/要確認) and store PDF in backend/tests/contract/test_ingest.py.
- [x] T014 [P] [US1] Integration test duplicate facility-week supersession in backend/tests/integration/test_ingest_supersede.py.
- [x] T015 [P] [US1] Integration test facility unresolved path in backend/tests/integration/test_facility_unresolved.py.

### Implementation
- [x] T016 [US1] Implement /ingest/upload endpoint in backend/src/api/ingest.py to enqueue ingest job with uploaded PDF.
- [x] T017 [US1] Implement ingest worker pipeline in backend/src/workers/ingest_worker.py (OCR retries, facility region extraction, create OrderDocument/Order).
- [x] T018 [US1] Implement duplicate facility-week handling in backend/src/services/order_service.py (supersede older docs, exclude from outputs).
- [x] T019 [US1] Implement order list API with filters/status in backend/src/api/orders.py.
- [x] T020 [US1] Build frontend order list view with filters/status badges in frontend/src/pages/orders/index.tsx showing facility-unresolved marker.

**Checkpoint**: US1 independently testable (ingest -> list -> supersede).

## Phase 4: User Story 2 - PDF原本を見ながら修正し確定で出力更新 (Priority: P1)

**Goal**: PDFを見ながら明細を修正し、確定でラベル/納品/総量出力を自動更新する。  
**Independent Test**: PDFを開き行を修正→確定し、出力3種が更新される。

### Tests
- [x] T021 [US2] Playwright e2e 未着/要確認→確定 with inline edits and PDF viewer in frontend/tests/e2e/order_confirm.spec.ts.
- [x] T022 [P] [US2] Integration test change-column precedence & zero suppression in backend/tests/integration/test_line_corrections.py.
- [x] T023 [P] [US2] Integration test status transitions (要確認→確定, エラー recovery) in backend/tests/integration/test_status_flow.py.

### Implementation
- [x] T024 [US2] Implement PDF viewer + inline editable lines in frontend/src/pages/orders/[id].tsx with single 確定 action.
- [x] T025 [US2] Implement PUT /orders/{id}/lines in backend/src/api/orders.py to save corrections (stay 要確認).
- [x] T026 [US2] Implement POST /orders/{id}/confirm in backend/src/api/orders.py to lock corrections and enqueue outputs.
- [x] T027 [US2] Implement outputs worker in backend/src/workers/output_worker.py to generate label CSV (CP932), delivery note Excel, manufacturing aggregate CSV.
- [x] T028 [US2] Apply zero suppression and change-column precedence in backend/src/services/output_builder.py.
- [x] T029 [US2] Add direct-to-fix error responses and safe requeue on failure in backend/src/api/orders.py and backend/src/workers/output_worker.py.

**Checkpoint**: US2 independently testable (viewer/edit/confirm → outputs).

## Phase 5: User Story 3 - 週次メニューをアップロードし項目を確定 (Priority: P1)

**Goal**: 週次メニューをアップロードし、単位/量/温冷/時間帯/区分を確定して出力に反映する。  
**Independent Test**: メニューをアップロード・編集し、袋分け/出力に設定が反映される。

### Tests
- [x] T030 [US3] Integration test menu upload → items created in backend/tests/integration/test_menu_upload.py.
- [x] T031 [P] [US3] Integration test per-line edits affect outputs in backend/tests/integration/test_menu_edits_output.py.
- [x] T032 [P] [US3] Integration test facility override via replacement in backend/tests/integration/test_menu_override.py.

### Implementation
- [x] T033 [US3] Implement POST /weekly-menus upload parse in backend/src/api/menus.py to create WeeklyMenu/Items.
- [x] T034 [US3] Implement menu editor UI in frontend/src/pages/menus/[weekId].tsx for unit/qty/temp/daypart/category edits.
- [x] T035 [US3] Implement override application in backend/src/services/menu_service.py (facility replacement).
- [x] T036 [US3] Add validation/error surfacing for malformed files in backend/src/api/menus.py.

**Checkpoint**: US3 independently testable (upload/edit/override).

## Phase 6: User Story 4 - 施設マスターでテンプレとルールを管理 (Priority: P2)

**Goal**: 施設マスターで抽出/袋分け/ラベル/納品設定を更新し、新規施設追加に対応する。  
**Independent Test**: 新施設をマスター設定だけで追加し、次回処理が設定通りに走る。

### Tests
- [x] T037 [US4] Integration test facility create + areas + configs applied in backend/tests/integration/test_facility_config_apply.py.
- [x] T038 [P] [US4] Contract test config update endpoint in backend/tests/contract/test_facility_config.py.

### Implementation
- [x] T039 [US4] Implement facility create/edit + areas in backend/src/api/facilities.py.
- [x] T040 [US4] Implement config editor endpoints for templates/packaging/label/invoice in backend/src/api/facilities.py.
- [x] T041 [US4] Implement facility config UI in frontend/src/pages/facilities/[id].tsx with upload/preview.
- [x] T042 [US4] Add template/mapping linters and preview in backend/src/services/config_validator.py.
- [x] T043 [US4] Seed/migration scripts for initial facilities/configs in backend/migrations/seed_facilities.py.

**Checkpoint**: US4 independently testable (config-only new facility).

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T044 Monitoring/alerts for queues/backlog endpoint in infra/monitoring/alerts.yml with ingest/output latency SLOs.
- [x] T045 Audit/notifications: ensure uploads/edits/config changes/confirmations logged and routed in backend/src/services/notification_service.py.
- [x] T046 Security hardening: retention job, encryption in transit/at rest review documented in docs/security.md.
- [x] T047 Performance/load test scripts for ingest/output/backlog in backend/tests/perf/test_performance.py (target 95% ≤2min, backlog catch-up 30min @100施設/日).
- [x] T048 Golden-file fixtures for label CSV/Excel/aggregate in backend/tests/fixtures/outputs/ with regression tests in backend/tests/integration/test_outputs_golden.py.
- [x] T049 Documentation updates in docs/quickstart.md (runbook) and AGENTS.md (tech context refresh).

## Dependencies & Execution Order

- Setup (Phase 1) → Foundational (Phase 2) → US1 (P1) → US2 (P1) → US3 (P1) → US4 (P2) → Polish.
- US2 depends on ingest/order list from US1. US3 depends on foundations only; US4 depends on foundations and config plumbing.
- Tests per story should be authored before implementation tasks within the same story.

## Parallel Opportunities

- Phase 1: T005/T006 parallel; T003 parallel after env files exist.
- Phase 2: T009–T012 parallel after migrations/auth scaffolding start.
- US1: T014/T015 parallel after T016/T017 scaffold; frontend T020 parallel with backend APIs.
- US2: T022/T023 parallel; T027/T028 parallel once confirm API stub exists.
- US3: T031/T032 parallel; T034 parallel with T033 parsing.
- US4: T038 parallel with T037; T041 parallel with T039/T040 after contract shape fixed.

## Implementation Strategy

### MVP First (User Story 1 Only)
1. Complete Phase 1–2 foundations.
2. Deliver US1 (ingest/list/supersede + tests).
3. Validate outputs of US1; deploy/demo.

### Incremental Delivery
1. US2 adds confirm + outputs; validate with Playwright and integration suites.
2. US3 adds menu management; validate overrides.
3. US4 adds facility master configurability.
4. Polish: monitoring, security, performance, golden files.
