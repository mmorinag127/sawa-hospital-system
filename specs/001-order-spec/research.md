# Research: 病院・施設 発注FAX自動取込〜出力 (MVP)

**Branch**: 001-order-spec  
**Date**: 2025-12-23  
**Spec**: specs/001-order-spec/spec.md

## Decisions

1) Stack (Backend)
- Decision: Python 3.11 + FastAPI with SQLAlchemy/Pydantic and Celery workers.
- Rationale: Mature async API support, strong PDF/OCR ecosystem, easy contract/integration testing.
- Alternatives considered: Django (heavier for API-first), Node/Express (weaker OCR libs).

2) OCR/Extraction
- Decision: pdfplumber for table/text extraction plus Tesseract OCR (Japanese, retries configurable).
- Rationale: On-prem friendly, controllable retries, supports mixed print/handwriting with tuning.
- Alternatives considered: Cloud OCR APIs (higher accuracy but adds latency/compliance dependency).

3) Storage
- Decision: PostgreSQL for relational data; S3-compatible bucket (or filesystem) for PDFs/outputs; Redis for queues.
- Rationale: Reliable relational model for orders/menus/config; object storage for large binaries; Redis works with Celery.
- Alternatives considered: MySQL (similar but less JSON/operator ergonomics), pure filesystem (harder to scale/audit).

4) Frontend/UI
- Decision: PCブラウザ限定でReactベース（Next.js app router）を想定、PDFビューア＋インライン編集＋確定ワンアクション。
- Rationale: Strong component ecosystem, Playwright e2e coverage, fits operator workflow.
- Alternatives considered: Vue/Nuxt (viable), desktop app (overkill, slower iteration).

5) Outputs
- Decision: pandas/xlsxwriter for Excel, standard CSV writer with CP932 output option; schemas driven by facility master config.
- Rationale: Stable formatting control, easy testing via golden files, matches legacy encodings.
- Alternatives considered: LibreOffice automation (heavier), raw string templating (brittle).

6) Observability/IDs
- Decision: Structured logging with FAC/WEK IDs and request/span IDs; audit tables for edits/uploads/config changes.
- Rationale: Constitution requires traceability and auditability across ingest→output.
- Alternatives considered: Log-only without audits (insufficient for compliance).

7) Reliability/Backlog
- Decision: Ingest queue that requeues on failure; idempotent facility-week upsert; scheduled backlog catcher after downtime.
- Rationale: Meets success criteria (2min ingest, 30min backlog catch-up).
- Alternatives considered: Fire-and-forget ingest (risk of loss), manual backlog handling (violates SLA).

## Clarifications Resolved

- Language/Framework, OCR approach, storage choices, and output tooling fixed above.
- PCブラウザのみ、重複週PDFは最新版採用、マスター駆動で施設差分吸収 — all aligned with spec/constitution.

## Open Items

None — all technical context unknowns resolved for planning.***
