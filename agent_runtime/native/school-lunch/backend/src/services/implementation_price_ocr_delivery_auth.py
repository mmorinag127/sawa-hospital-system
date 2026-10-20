"""Independent drain authentication with explicit identity and audience."""
from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google.auth.exceptions import GoogleAuthError
from secrets import compare_digest

from src.services.implementation_price_ocr_dispatch_service import _required_env, OcrDispatchConfigurationError


def _require_identity(request, *, audience_name, identity_name, issuer_name, subject_name):
    try:
        audience = _required_env(audience_name)
        identity = _required_env(identity_name)
        issuer = _required_env(issuer_name)
        subject = _required_env(subject_name)
    except OcrDispatchConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Delivery drain identity is required")
    try:
        payload = id_token.verify_oauth2_token(authorization[7:], google_requests.Request(), audience)
    except (ValueError, GoogleAuthError) as exc:
        raise HTTPException(status_code=401, detail="Invalid delivery drain identity") from exc
    if (payload.get("email") != identity or payload.get("email_verified") is not True
        or payload.get("iss") != issuer or payload.get("sub") != subject or payload.get("aud") != audience):
        raise HTTPException(status_code=403, detail="Delivery drain identity is not authorized")


def require_delivery_drain_identity(request: Request):
    return _require_identity(request, audience_name="IMPLEMENTATION_PRICE_OCR_DRAIN_AUDIENCE",
        identity_name="IMPLEMENTATION_PRICE_OCR_DRAIN_SERVICE_ACCOUNT", issuer_name="IMPLEMENTATION_PRICE_OCR_DRAIN_ISSUER",
        subject_name="IMPLEMENTATION_PRICE_OCR_DRAIN_SUBJECT")


def require_worker_identity(request: Request):
    from src.services.implementation_price_ocr_dispatch_service import configured_mode
    try:
        mode = configured_mode()
        if mode == "local":
            token = _required_env("IMPLEMENTATION_PRICE_OCR_LOCAL_TOKEN")
            if not compare_digest(request.headers.get("Authorization", ""), "Bearer " + token):
                raise HTTPException(status_code=401, detail="Worker identity is required")
            return
    except OcrDispatchConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _require_identity(request, audience_name="IMPLEMENTATION_PRICE_OCR_TASKS_AUDIENCE",
        identity_name="IMPLEMENTATION_PRICE_OCR_TASKS_SERVICE_ACCOUNT", issuer_name="IMPLEMENTATION_PRICE_OCR_TASKS_ISSUER",
        subject_name="IMPLEMENTATION_PRICE_OCR_TASKS_SUBJECT")
