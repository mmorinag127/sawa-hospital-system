import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.api.auth import require_role, GOOGLE_OAUTH_CLIENT_IDS
from src.services.ingest_job_service import summarize_ingest_jobs
from src.services.intake_mode_service import get_intake_status
from src.services.ocr_pipeline_state_store import load_pipeline_state
from src.services.ocr_pipeline_service import get_pipeline_config, get_pipeline_runtime_status
from src.services.ocr_quality_service import summarize_reparse_quality
from src.services.uploaded_pdf_service import summarize_uploaded_pdfs
from src.services.system_maintenance_service import (
    clear_operational_data,
    export_database_snapshot,
    get_db_quota_status,
    get_sqlite_db_path,
)
from src.services.notification_service import record_event
from src.services.system_process_log_service import list_process_logs

router = APIRouter()


def _is_production_runtime() -> bool:
    service_name = str(os.getenv("K_SERVICE", "") or "").strip().lower()
    app_env = str(os.getenv("APP_ENV", "") or os.getenv("ENVIRONMENT", "") or "").strip().lower()
    return service_name.endswith("-prod") or app_env in {"prod", "production"}


@router.get("/system/status", dependencies=[Depends(require_role("operator"))])
def system_status():
    try:
        pipeline_state = load_pipeline_state() or {}
    except Exception as exc:  # noqa: BLE001
        pipeline_state = {
            "status": "error",
            "last_error": str(exc),
            "last_error_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    pipeline_config = get_pipeline_config()
    pipeline_runtime = get_pipeline_runtime_status()
    intake = get_intake_status()
    oauth_config_ok = bool(GOOGLE_OAUTH_CLIENT_IDS)
    ingest_summary = summarize_ingest_jobs()
    uploaded_pdf_summary = summarize_uploaded_pdfs()
    db_quota = get_db_quota_status()
    try:
        ocr_reparse_quality = summarize_reparse_quality()
    except Exception as exc:  # noqa: BLE001
        ocr_reparse_quality = {
            "gate": {"status": "error"},
            "error": f"quality_summary_failed:{exc}",
        }
    return {
        "intake": intake,
        "oauth_config": {
            "configured": oauth_config_ok,
            "google_client_id_count": len(GOOGLE_OAUTH_CLIENT_IDS),
        },
        "ocr_pipeline": {
            "status": pipeline_state.get("status"),
            "updated_at": pipeline_state.get("updated_at"),
            "last_success_at": pipeline_state.get("last_success_at"),
            "last_error_at": pipeline_state.get("last_error_at"),
            "last_error": pipeline_state.get("last_error"),
            "last_job_id": pipeline_state.get("last_job_id"),
            "last_output_ref": pipeline_state.get("last_output_ref"),
            "last_input_ref": pipeline_state.get("last_input_ref"),
            "configured": pipeline_config.get("configured"),
            "url_set": pipeline_config.get("url_set"),
            "bucket_set": pipeline_config.get("bucket_set"),
            "bucket": pipeline_config.get("bucket"),
            "input_prefix": pipeline_config.get("input_prefix"),
            "output_prefix": pipeline_config.get("output_prefix"),
            "trigger_mode": pipeline_config.get("trigger_mode"),
            "http_trigger_enabled": pipeline_config.get("http_trigger_enabled"),
            "gcs_trigger_enabled": pipeline_config.get("gcs_trigger_enabled"),
            "wait_strategy": pipeline_config.get("wait_strategy"),
            "sync_wait_supported": pipeline_config.get("sync_wait_supported"),
            "sync_wait_note": pipeline_config.get("sync_wait_note"),
            "max_inflight": pipeline_runtime.get("max_inflight"),
            "inflight": pipeline_runtime.get("inflight"),
            "request_timeout_seconds": pipeline_runtime.get("request_timeout_seconds"),
            "timeout_seconds": pipeline_runtime.get("timeout_seconds"),
            "poll_interval_seconds": pipeline_runtime.get("poll_interval_seconds"),
        },
        "ingest_jobs": ingest_summary,
        "uploaded_pdfs": uploaded_pdf_summary,
        "db_quota": db_quota,
        "ocr_reparse_quality": ocr_reparse_quality,
    }


@router.get("/system/db/quota", dependencies=[Depends(require_role("operator"))])
def get_db_quota():
    return get_db_quota_status()


@router.get("/system/process-logs", dependencies=[Depends(require_role("operator"))])
def get_process_logs(limit: int = 100):
    return list_process_logs(limit=limit)


@router.get("/system/db/download", dependencies=[Depends(require_role("admin"))])
def download_db(snapshot: bool = False):
    sqlite_path = get_sqlite_db_path()
    if sqlite_path and not snapshot:
        filename = sqlite_path.name or "system.db"
        record_event(
            "system_db_download",
            actor="admin",
            target="sqlite_db",
            metadata={"snapshot": False, "filename": filename},
        )
        return FileResponse(
            sqlite_path,
            media_type="application/x-sqlite3",
            filename=filename,
        )
    output_path, filename, media_type, _ = export_database_snapshot()
    record_event(
        "system_db_download",
        actor="admin",
        target="database_snapshot",
        metadata={"snapshot": True, "filename": filename},
    )
    return FileResponse(output_path, media_type=media_type, filename=filename)


@router.post("/system/clear-all", dependencies=[Depends(require_role("admin"))])
def clear_all_data(body: dict | None = None):
    payload = body if isinstance(body, dict) else {}
    confirm = str(payload.get("confirm") or "").strip()
    if confirm != "CLEAR_ALL":
        raise HTTPException(status_code=400, detail="confirm must be CLEAR_ALL")
    production_runtime = _is_production_runtime()
    if production_runtime:
        prod_confirm = str(payload.get("prod_confirm") or "").strip()
        if prod_confirm != "CLEAR_ALL_PRODUCTION":
            raise HTTPException(status_code=400, detail="prod_confirm must be CLEAR_ALL_PRODUCTION")
    include_audit_logs = bool(payload.get("include_audit_logs", True))
    record_event(
        "system_clear_all",
        actor="admin",
        target="operational_data",
        metadata={
            "include_audit_logs": include_audit_logs,
            "production_runtime": production_runtime,
        },
    )
    result = clear_operational_data(include_audit_logs=include_audit_logs)
    return {
        "result": result,
        "quota": get_db_quota_status(),
    }
