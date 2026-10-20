"""OIDC worker identity, independent of the public API service token."""

from fastapi import HTTPException
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .solve_delivery_dispatch_service import CloudTaskConfig


def require_identity(authorization, *, config: CloudTaskConfig, verifier=id_token.verify_oauth2_token):
    config.validate()
    if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(401, "Unauthorized")
    try:
        claims = verifier(authorization[7:].strip(), Request(), config.audience)
    except (ValueError, GoogleAuthError) as error:
        raise HTTPException(401, "Unauthorized") from error
    if (not isinstance(claims, dict) or claims.get("iss") != config.issuer
            or claims.get("aud") != config.audience or claims.get("email") != config.service_account
            or claims.get("sub") != config.subject or claims.get("email_verified") is not True):
        raise HTTPException(403, "Forbidden")
