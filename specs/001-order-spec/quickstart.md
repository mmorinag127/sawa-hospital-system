# Quickstart: 病院・施設 発注FAX自動取込〜出力 (MVP)

**Branch**: 001-order-spec  
**Spec**: specs/001-order-spec/spec.md  
**Date**: 2025-12-23

## 1) Environment

- Python 3.11, Node 20+ (for frontend), PostgreSQL, Redis, Tesseract (Japanese), pdfplumber deps, pandas/xlsxwriter.
- Configure object storage (S3-compatible) or filesystem path for PDFs/outputs.
- Set timezone to JST for workers/API.

## 2) Configuration

- Create env files for backend (DB_URI, REDIS_URI, STORAGE_BUCKET/PATH, AUTH settings for admin Google OAuth and operator basic auth).
- Seed facility master configs (templates, label rules, invoice mappings) and initial weekly menu if available.
- Set OCR retry defaults (3) and retention window (1–2 months).

## 3) Run Services

- Start Redis and PostgreSQL.
- Run backend API (FastAPI) and Celery workers (ingest + exports).
- Run frontend (React/Next.js) targeting backend API base URL.

## 4) Ingest & Review Flow

- Configure mail forwarding to ingestion endpoint/service; verify PDF lands in storage.
- New PDF → order appears as 未着/要確認; open PDF viewer, edit lines inline.
- Use 施設未確定 cases to test manual facility selection; confirm to trigger outputs.

## 5) Outputs & Verification

- After 確定, download/inspect label CSV (CP932), delivery note Excel, and manufacturing aggregate CSV.
- Validate zero-quantity suppression and change-column precedence; ensure duplicate week PDF supersedes older.

## 6) Testing

- Run pytest suites (unit/contract/integration) plus Playwright e2e for operator UI (未着→確定).
- Include scenarios: OCR retry exhaustion, backlog catch-up after simulated downtime, duplicate facility-week replacement, facility override in weekly menu.

## 7) Observability

- Enable structured logging with FAC/WEK IDs; ensure audit logs capture uploads, edits, config changes, confirmations.
- Monitor backlog endpoint and worker queues; alerts for stuck ingest/exports.
