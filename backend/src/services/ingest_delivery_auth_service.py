import os
from fastapi import HTTPException
from google.auth.transport import requests
from google.oauth2 import id_token


def require_identity(authorization, *, drain=False):
    prefix = "HOSPITAL_INGEST_DELIVERY_" + ("DRAIN_" if drain else "WORKER_")
    config = {key: os.getenv(prefix + key, "").strip() for key in ("ISSUER", "AUDIENCE", "EMAIL", "SUBJECT")}
    if not all(config.values()) or config["ISSUER"] not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=503, detail="ingest_delivery_auth_configuration_required")
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        claims = id_token.verify_oauth2_token(authorization[7:].strip(), requests.Request(), config["AUDIENCE"])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Unauthorized") from exc
    if (claims.get("iss") != config["ISSUER"] or claims.get("aud") != config["AUDIENCE"]
        or claims.get("email") != config["EMAIL"] or claims.get("sub") != config["SUBJECT"]
        or claims.get("email_verified") is not True):
        raise HTTPException(status_code=403, detail="Forbidden")
