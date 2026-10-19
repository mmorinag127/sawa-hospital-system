import os
import json
from fastapi import HTTPException
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from google.auth import crypt, jwt
from google.auth import exceptions as auth_exceptions
from google.auth.transport import requests
from google.oauth2 import id_token


class _CertificateRequest(requests.Request):
    def __call__(self, *args, **kwargs):
        try:
            response = super().__call__(*args, **kwargs)
            if response.status == 200:
                certs = json.loads(response.data.decode('utf-8'))
                if not isinstance(certs, dict) or not certs or 'keys' in certs:
                    raise ValueError('certificate mapping required')
                for key_id, pem in certs.items():
                    if not isinstance(key_id, str) or not key_id or not isinstance(pem, str):
                        raise ValueError('certificate entry invalid')
                    encoded = pem.encode('utf-8')
                    key = (x509.load_pem_x509_certificate(encoded).public_key()
                           if b'-----BEGIN CERTIFICATE-----' in encoded
                           else serialization.load_pem_public_key(encoded))
                    if isinstance(key, RSAPublicKey):
                        crypt.RSAVerifier(key)
                    elif isinstance(key, EllipticCurvePublicKey):
                        crypt.EsVerifier(key)
                    else:
                        raise ValueError('unsupported certificate key type')
        except Exception as exc:
            raise auth_exceptions.TransportError('delivery_auth_certificates_unavailable') from exc
        # Return the exact transport response; Google retains all JWT validation.
        return response


def verified_delivery_claims(authorization, audience):
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        claims = id_token.verify_oauth2_token(authorization[7:].strip(), _CertificateRequest(), audience)
    except Exception as exc:
        # Google JWT rejection and verify_oauth2_token's issuer rejection are
        # authentication failures; transport/configuration/unknown failures are not.
        invalid = type(exc) in {ValueError, auth_exceptions.GoogleAuthError} or isinstance(
            exc, (auth_exceptions.MalformedError, auth_exceptions.InvalidValue))
        if isinstance(exc, TypeError):
            try:
                # Unverified metadata can prove a malformed header, never an identity.
                header = jwt.decode_header(authorization[7:].strip())
                invalid = not isinstance(header.get('alg'), str) or (
                    header.get('kid') is not None and not isinstance(header['kid'], str))
            except Exception:
                pass
        raise HTTPException(status_code=401 if invalid else 503,
                            detail="Unauthorized" if invalid else "delivery_auth_verification_unavailable") from exc
    if not isinstance(claims, dict):
        raise HTTPException(status_code=503, detail="delivery_auth_verification_unavailable")
    return claims


def require_identity(authorization, *, drain=False):
    prefix = "HOSPITAL_INGEST_DELIVERY_" + ("DRAIN_" if drain else "WORKER_")
    config = {key: os.getenv(prefix + key, "").strip() for key in ("ISSUER", "AUDIENCE", "EMAIL", "SUBJECT")}
    if not all(config.values()) or config["ISSUER"] not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=503, detail="ingest_delivery_auth_configuration_required")
    claims = verified_delivery_claims(authorization, config["AUDIENCE"])
    if (claims.get("iss") != config["ISSUER"] or claims.get("aud") != config["AUDIENCE"]
        or claims.get("email") != config["EMAIL"] or claims.get("sub") != config["SUBJECT"]
        or claims.get("email_verified") is not True):
        raise HTTPException(status_code=403, detail="Forbidden")
