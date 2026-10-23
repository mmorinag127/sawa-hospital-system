"""Authenticated read-only transport for the fixed production settings export."""
import base64
import hashlib
import json
import re
from urllib.parse import urlsplit
from src.services.staging_copy_refresh_service import RefreshError

def export_runtime_settings(source_revision, snapshot, *, endpoint="https://worker-prod-avlnzjjrca-dt.a.run.app"):
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".a.run.app") or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise RefreshError("refresh_production_settings_export_origin_invalid")
    token = fetch_id_token(Request(), "https://worker-prod-avlnzjjrca-dt.a.run.app")
    response = requests.get(endpoint + "/internal/staging-source-settings", params={"snapshot": snapshot},
        headers={"Authorization": "Bearer " + token}, timeout=120)
    if response.status_code != 200:
        raise RefreshError("refresh_production_settings_export_unavailable")
    value = response.json()
    if (value.get("schema") != 1 or value.get("revision") != source_revision.rsplit("/", 1)[-1]
            or value.get("database_snapshot") != snapshot
            or set(value.get("settings", {})) != {"FACILITY_MASTER_URI", "INGEST_POLICY_URI", "OCR_PIPELINE_STATE_URI"}):
        raise RefreshError("refresh_production_settings_export_mismatch")
    for key in ("FACILITY_MASTER_URI", "INGEST_POLICY_URI"):
        setting = value["settings"][key]
        if isinstance(setting, str) and setting.startswith("gs://"):
            continue
        if (not isinstance(setting, dict) or set(setting) != {"content_base64", "sha256", "path", "raw_file_sha256"}
                or not isinstance(setting["path"], str) or not setting["path"].startswith("/")
                or not re.fullmatch(r"[0-9a-f]{64}", str(setting["raw_file_sha256"]))):
            raise RefreshError("refresh_production_settings_export_mismatch")
        content = base64.b64decode(setting["content_base64"], validate=True)
        if hashlib.sha256(content).hexdigest() != setting["sha256"] or not isinstance(json.loads(content), dict):
            raise RefreshError("refresh_production_settings_export_mismatch")
    return value
