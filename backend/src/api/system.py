from datetime import datetime, timezone
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.api.auth import require_role, GOOGLE_OAUTH_CLIENT_IDS
from src.services.gmail_state_store import load_watch_state
from src.services.gmail_ingest_service import get_gmail_profile_email
from src.services.ingest_job_service import summarize_ingest_jobs
from src.services.ocr_pipeline_state_store import load_pipeline_state
from src.services.ocr_pipeline_service import get_pipeline_config, get_pipeline_runtime_status
from src.services.ocr_quality_service import summarize_reparse_quality
from src.services.system_maintenance_service import (
    clear_operational_data,
    export_database_snapshot,
    get_db_quota_status,
    get_sqlite_db_path,
)

router = APIRouter()


def _parse_expiration(expiration: str | None) -> tuple[str | None, str | None, bool]:
    if not expiration:
        return None, None, False
    try:
        exp_ms = int(expiration)
    except (TypeError, ValueError):
        return None, None, False
    exp_dt = datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc)
    exp_iso = exp_dt.isoformat()
    expired = exp_dt < datetime.now(tz=timezone.utc)
    return str(exp_ms), exp_iso, expired


@router.get("/system/status", dependencies=[Depends(require_role("operator"))])
def system_status():
    try:
        state = load_watch_state() or {}
    except Exception as exc:  # noqa: BLE001
        state = {
            "status": "error",
            "error_code": "state_load_failed",
            "error_message": str(exc),
        }
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
    gmail_client_id = os.getenv("GMAIL_CLIENT_ID")
    gmail_client_secret = os.getenv("GMAIL_CLIENT_SECRET")
    gmail_refresh_token = os.getenv("GMAIL_REFRESH_TOKEN")
    gmail_config_ok = bool(gmail_client_id and gmail_client_secret and gmail_refresh_token)
    gmail_account = None
    if gmail_config_ok:
        try:
            gmail_account = get_gmail_profile_email()
        except Exception:
            gmail_account = None
    oauth_config_ok = bool(GOOGLE_OAUTH_CLIENT_IDS)
    expiration_ms, expiration_iso, expired = _parse_expiration(state.get("expiration"))
    error_code = state.get("error_code")
    status = state.get("status") or "unknown"
    if error_code == "invalid_grant":
        status = "invalid_grant"
    elif expired:
        status = "expired"
    elif status == "error":
        status = "error"
    elif not gmail_config_ok:
        status = "misconfigured"
    else:
        status = "ok"
    ingest_summary = summarize_ingest_jobs()
    db_quota = get_db_quota_status()
    try:
        ocr_reparse_quality = summarize_reparse_quality()
    except Exception as exc:  # noqa: BLE001
        ocr_reparse_quality = {
            "gate": {"status": "error"},
            "error": f"quality_summary_failed:{exc}",
        }
    return {
        "gmail_watch": {
            "status": status,
            "expiration_ms": expiration_ms,
            "expiration_iso": expiration_iso,
            "updated_at": state.get("updated_at"),
            "error_code": error_code,
            "error_message": state.get("error_message"),
        },
        "gmail_config": {
            "client_id_set": bool(gmail_client_id),
            "client_secret_set": bool(gmail_client_secret),
            "refresh_token_set": bool(gmail_refresh_token),
            "configured": gmail_config_ok,
            "account": gmail_account,
        },
        "oauth_config": {
            "google_client_ids": GOOGLE_OAUTH_CLIENT_IDS,
            "configured": oauth_config_ok,
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
            "max_inflight": pipeline_runtime.get("max_inflight"),
            "inflight": pipeline_runtime.get("inflight"),
            "request_timeout_seconds": pipeline_runtime.get("request_timeout_seconds"),
            "timeout_seconds": pipeline_runtime.get("timeout_seconds"),
            "poll_interval_seconds": pipeline_runtime.get("poll_interval_seconds"),
        },
        "ingest_jobs": ingest_summary,
        "db_quota": db_quota,
        "ocr_reparse_quality": ocr_reparse_quality,
    }


@router.get("/system/db/quota", dependencies=[Depends(require_role("operator"))])
def get_db_quota():
    return get_db_quota_status()


@router.get("/system/db/download", dependencies=[Depends(require_role("admin"))])
def download_db(snapshot: bool = False):
    sqlite_path = get_sqlite_db_path()
    if sqlite_path and not snapshot:
        filename = sqlite_path.name or "system.db"
        return FileResponse(
            sqlite_path,
            media_type="application/x-sqlite3",
            filename=filename,
        )
    output_path, filename, media_type, _ = export_database_snapshot()
    return FileResponse(output_path, media_type=media_type, filename=filename)


@router.post("/system/clear-all", dependencies=[Depends(require_role("admin"))])
def clear_all_data(body: dict | None = None):
    payload = body if isinstance(body, dict) else {}
    confirm = str(payload.get("confirm") or "").strip()
    if confirm != "CLEAR_ALL":
        raise HTTPException(status_code=400, detail="confirm must be CLEAR_ALL")
    include_audit_logs = bool(payload.get("include_audit_logs", True))
    result = clear_operational_data(include_audit_logs=include_audit_logs)
    return {
        "result": result,
        "quota": get_db_quota_status(),
    }
