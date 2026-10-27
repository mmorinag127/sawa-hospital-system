import os
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from src.api.auth import require_role, GOOGLE_OAUTH_CLIENT_IDS
from src.services.ingest_job_service import summarize_ingest_jobs
from src.services.intake_mode_service import get_intake_status
from src.services.ocr_pipeline_state_store import load_pipeline_state
from src.services.ocr_pipeline_service import get_pipeline_config
from src.services.ocr_operations_read_service import get_operations
from src.services.ocr_quality_service import summarize_reparse_quality
from src.services.uploaded_pdf_service import summarize_uploaded_pdfs
from src.services.system_maintenance_service import (
    clear_operational_data,
    export_database_snapshot,
    get_db_quota_status,
    get_sqlite_db_path,
)
from src.services.notification_service import record_event
from src.services.system_process_log_service import get_process_log, list_process_logs

router = APIRouter()


def _is_production_runtime() -> bool:
    service_name = str(os.getenv("K_SERVICE", "") or "").strip().lower()
    app_env = str(os.getenv("APP_ENV", "") or os.getenv("ENVIRONMENT", "") or "").strip().lower()
    return service_name.endswith("-prod") or app_env in {"prod", "production"}


@router.get("/system/status", dependencies=[Depends(require_role("operator"))])
def system_status(view: Literal["full", "operations", "operations-summary", "configuration", "quality"] = "full"):
    if view == "configuration":
        return {
            "intake": get_intake_status(),
            "oauth_config": {"configured": bool(GOOGLE_OAUTH_CLIENT_IDS), "google_client_id_count": len(GOOGLE_OAUTH_CLIENT_IDS)},
        }
    if view == "quality":
        try:
            quality = summarize_reparse_quality()
        except Exception as exc:  # noqa: BLE001
            quality = {"gate": {"status": "error"}, "error": f"quality_summary_failed:{exc}"}
        return {
            "intake": get_intake_status(),
            "oauth_config": {"configured": bool(GOOGLE_OAUTH_CLIENT_IDS), "google_client_id_count": len(GOOGLE_OAUTH_CLIENT_IDS)},
            "ocr_reparse_quality": quality,
        }
    if view in {"operations", "operations-summary"}:
        if view == "operations-summary":
            from src.services.ocr_operations_display_service import get_operations_display
            operations = get_operations_display()
        else:
            operations = get_operations()
        ingest_summary = summarize_ingest_jobs()
        uploaded_pdf_summary = summarize_uploaded_pdfs()
        operations["intake"] = {"uploaded_pdfs": uploaded_pdf_summary, "ingest_jobs": ingest_summary}
        return {
            "intake": get_intake_status(),
            "oauth_config": {"configured": bool(GOOGLE_OAUTH_CLIENT_IDS), "google_client_id_count": len(GOOGLE_OAUTH_CLIENT_IDS)},
            "ocr_operations": operations,
            "ocr_pipeline": {**operations["summary"], "source": operations["source"]},
            "ingest_jobs": ingest_summary,
            "uploaded_pdfs": uploaded_pdf_summary,
        }
    try:
        pipeline_state = load_pipeline_state() or {}
    except Exception as exc:  # noqa: BLE001
        pipeline_state = {
            "status": "error",
            "last_error": str(exc),
            "last_error_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    operations = get_operations()
    operations["external_history"] = {
        "source": "legacy_external_pipeline", "current": False,
        "recorded_at": pipeline_state.get("updated_at"),
        "observation": pipeline_state, "configuration": get_pipeline_config(),
    }
    intake = get_intake_status()
    oauth_config_ok = bool(GOOGLE_OAUTH_CLIENT_IDS)
    ingest_summary = summarize_ingest_jobs()
    uploaded_pdf_summary = summarize_uploaded_pdfs()
    operations["intake"] = {"uploaded_pdfs": uploaded_pdf_summary, "ingest_jobs": ingest_summary}
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
        "ocr_operations": operations,
        "ocr_pipeline": {**operations["summary"], "source": operations["source"]},
        "ingest_jobs": ingest_summary,
        "uploaded_pdfs": uploaded_pdf_summary,
        "db_quota": db_quota,
        "ocr_reparse_quality": ocr_reparse_quality,
    }


@router.get("/system/ocr-history", dependencies=[Depends(require_role("operator"))])
def ocr_history(limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, min_length=1, max_length=1024)):
    from src.services.ocr_operations_display_service import get_operations_history
    return get_operations_history(limit=limit, cursor=cursor)


@router.get("/system/db/quota", dependencies=[Depends(require_role("operator"))])
def get_db_quota():
    return get_db_quota_status()


@router.get("/system/process-logs", dependencies=[Depends(require_role("operator"))])
def get_process_logs(limit: int = 100, include_details: bool = True):
    return list_process_logs(limit=limit, include_details=include_details)


@router.get("/system/process-logs/{log_id}", dependencies=[Depends(require_role("operator"))])
def get_process_log_detail(log_id: str):
    item = get_process_log(log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="process_log_not_found")
    return item


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
