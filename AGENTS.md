# workspace Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-05

## Active Technologies
- Python 3.11 + FastAPI, Pydantic, SQLAlchemy, Celery (async ingest/exports), pdfplumber + Tesseract OCR, pandas for CSV/Excel generation (001-order-spec)
- Next.js 14 + React 18 + TypeScript, Axios, Playwright (UI e2e) (001-order-spec)
- PostgreSQL (orders, menus, configs), Redis for queues, S3-compatible object storage or filesystem for PDFs/outputs (001-order-spec)

## Project Structure

```text
backend/
frontend/
tests/
```

## Commands

- Backend run: `task backend_run`
- Frontend dev (Cloud Run proxy): `task frontend_run_cloudrun_proxy`
- Backend tests: `task backend_test_us_all` or `task backend_test`
- Frontend E2E: `task frontend_test_e2e`

## Code Style

- Follow existing conventions; prefer small, explicit helpers over magic.

## Recent Changes
- 001-order-spec: Added auth guard in frontend and case-insensitive email checks in backend auth.
- 001-order-spec: Added output error handling, menu validation, retention worker, and monitoring placeholders.
- 001-order-spec: Added quickstart runbook and golden/perf tests scaffolding.

<!-- MANUAL ADDITIONS START -->
## Operator Rules (Manual)
- Do not use partial `gcloud run services update` that can drop env vars. If Cloud Run env changes are required, update all env vars in one operation or use IaC only.
- Do not change behavior in areas already working. Only modify what the user explicitly requests.
- Before touching Cloud Run settings, enumerate and verify all required env vars; do not proceed if any are missing.
<!-- MANUAL ADDITIONS END -->
