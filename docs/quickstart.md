# Quickstart

## Prerequisites
- Python 3.11
- Node.js 18+ (npm)
- Docker Desktop (optional for Postgres/Redis/MinIO)
- Task (`task` CLI)

## Local Services (Optional)
Run Postgres/Redis/MinIO locally:

```bash
docker compose -f infra/docker-compose.yml up -d
```

## Backend
1) Copy env template and adjust as needed:

```bash
cp backend/.env.example backend/.env
```

2) Set up the venv and dependencies:

```bash
task backend_setup_dev
```

3) Run the API:

```bash
task backend_run
```

4) (Optional) OCR ingest smoke test:

```bash
task ocr_test
```

## Frontend
1) Install dependencies:

```bash
task frontend_setup
```

2) Run dev server (proxy to Cloud Run if needed):

```bash
task frontend_run_cloudrun_proxy
```

## Tests
- Backend US1-4 suites:

```bash
task backend_test_us_all
```

- Full backend test suite:

```bash
task backend_test
```

- Frontend E2E (Playwright):

```bash
task frontend_test_e2e
```

- Performance tests (opt-in):

```bash
RUN_PERF_TESTS=1 backend/.venv/bin/python -m pytest backend/tests/perf/test_performance.py
```

## Config References
- Facility master template: `backend/src/data/facility_master.template.json`
- Ingest policy template: `backend/src/data/ingest_policy.template.json`
- Security notes: `docs/security.md`
