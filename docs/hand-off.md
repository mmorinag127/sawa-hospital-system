# Handoff Memo

## Status
- Working directory: `/Users/mmorinag/Sawa/2025.12/workspace`
- Taskfile.yml tasks:
  - `backend_setup` (create python3.11 venv + install deps)
  - `backend_clean_venv` (remove venv)
  - `backend_run` (run uvicorn with local env vars)
  - `backend_start` (backend_setup -> backend_run)
  - `ocr_test` (POST ingest test)
- GCP credentials:
  - PROJECT_ID: `mythic-lattice-483204-t8`
  - GOOGLE_APPLICATION_CREDENTIALS: `/Users/mmorinag/Sawa/2025.12/MythicLatticeData.json`
- Input examples:
  - `/Users/mmorinag/Sawa/2025.12/input_example/Fureai Order Form.pdf`
  - `/Users/mmorinag/Sawa/2025.12/input_example/Seal.pdf`
- Facility master:
  - Template: `backend/src/data/facility_master.template.json`
  - Test: `backend/src/data/facility_master.test.json`
  - Single template: `docs/templates/facility_master_single.template.yaml`

## Recent Changes
- Imports unified to `src.*` (api/services/workers/models/db).
- `backend/src/main.py` import updated to `from src.api ...`.
- SQLAlchemy reserved name fix: `AuditLog.metadata` -> `metadata_json = Column("metadata", JSON, ...)`.
  - `backend/src/lib/logging.py` updated to use `metadata_json`.
- Taskfile.yml:
  - `backend_setup` uses python3.11 + upgrades pip/setuptools/wheel.
  - Added `backend_clean_venv` and `backend_start`.

## Last Known Errors (resolved)
- ModuleNotFoundError -> fixed with `src.*` imports.
- SQLAlchemy InvalidRequestError (`metadata` reserved) -> fixed.
- pandas/numpy build failures -> fixed by using python3.11 venv.

## Next Steps
1) Run `task backend_run`.
2) If it starts, run `task ocr_test`.
3) If it fails, capture and share the error log.
