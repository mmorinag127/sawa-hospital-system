# Implementation Plan: Facility OCR Pipeline

**Branch**: `001-ocr-pipeline-update` | **Date**: 2026-01-08 | **Spec**: specs/001-ocr-pipeline-update/spec.md
**Input**: Feature specification from `specs/001-ocr-pipeline-update/spec.md`

## Summary

Deliver a facility-specific OCR pipeline for single-page weekly order PDFs that selects a template, aligns the page, extracts ROI-level quantities and notes, validates and retries failed cells, and outputs structured JSON with job tracking, duplicate protection, and unclassified handling.

## Technical Context

**Language/Version**: Python 3.11 (backend), TypeScript (frontend)
**Primary Dependencies**: FastAPI, SQLAlchemy, Celery; Next.js/React
**Storage**: PostgreSQL for app data, object storage for PDFs/outputs, template registry storage
**Testing**: pytest (backend), Playwright (frontend)
**Target Platform**: Cloud Run for workers and API; PC browser for UI
**Project Type**: web application
**Performance Goals**: single-page OCR job completes within 2 minutes for 95% of valid inputs
**Constraints**: single-page PDFs only; template-driven extraction; no facility hardcoding
**Scale/Scope**: ~100 facilities/day, tens to hundreds of templates

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Master-data-driven design (facility templates, mappings, label/invoice rules) — no
  hard-coded facility logic.
- Required tests identified for ingest -> facility resolution -> menu mapping -> outputs
  (label CSV, delivery note Excel, manufacturing totals), including OCR retry/duplicate
  replacement/zero suppression/change-column precedence/backlog recovery.
- UX constraints upheld: PC browser only, PDF viewer available, inline edits commit only on
  a single confirm action, status vocabulary = 未着/要確認/確定/エラー with direct-to-fix cues.
- Performance/reliability plan: immediate ingest on email receipt (JST), safe requeue on
  failure, backlog catch-up after downtime, capacity for ~100 facilities/day.

Status: pass. No violations noted for this feature scope.

## Project Structure

### Documentation (this feature)

```text
specs/001-ocr-pipeline-update/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
backend/
├── src/
│   ├── api/
│   ├── models/
│   ├── services/
│   └── workers/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

infra/
```

**Structure Decision**: Use the existing backend/ frontend split. The OCR pipeline logic and job handling live in backend services/workers, and any template management UI lives in frontend pages/components.

## Complexity Tracking

No violations requiring complexity tracking.
