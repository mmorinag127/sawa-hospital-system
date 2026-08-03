import base64
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from scripts.portal_prod_db_bootstrap import (  # noqa: E402
    _extract_email_from_id_token,
    _verify_live_portal_access,
)
from src.services.portal_access_bootstrap_service import PortalAccessBootstrapError  # noqa: E402


def _token(payload: dict) -> str:
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"header.{encoded_payload.rstrip('=')}.signature"


def test_extract_email_from_id_token_returns_claim_email():
    assert (
        _extract_email_from_id_token(
            _token({"email": "Deploy-Verifier@example.com", "email_verified": True})
        )
        == "Deploy-Verifier@example.com"
    )


def test_extract_email_from_id_token_blocks_missing_email():
    with pytest.raises(PortalAccessBootstrapError, match="must include an email claim"):
        _extract_email_from_id_token(_token({"email_verified": True}))


def test_extract_email_from_id_token_blocks_missing_token():
    with pytest.raises(PortalAccessBootstrapError, match="DEPLOY_ID_TOKEN is required"):
        _extract_email_from_id_token("")


def test_extract_email_from_id_token_blocks_unverified_email():
    with pytest.raises(PortalAccessBootstrapError, match="email claim must be verified"):
        _extract_email_from_id_token(
            _token({"email": "deploy-verifier@example.com", "email_verified": False})
        )


def test_extract_email_from_id_token_blocks_missing_email_verified_claim():
    with pytest.raises(PortalAccessBootstrapError, match="email claim must be verified"):
        _extract_email_from_id_token(_token({"email": "deploy-verifier@example.com"}))


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_verify_live_portal_access_accepts_http_200(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url == "https://web.example/api/auth/me"
        assert timeout == 10
        assert request.headers["Authorization"] == "Bearer token"
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert (
        _verify_live_portal_access(
            live_auth_url="https://web.example/api/auth/me",
            deploy_token="token",
        )
        == 200
    )


def test_verify_live_portal_access_blocks_non_200(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 10
        raise urllib.error.HTTPError(
            "https://web.example/api/auth/me",
            403,
            "Forbidden",
            {},
            None,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(PortalAccessBootstrapError, match="HTTP 403"):
        _verify_live_portal_access(
            live_auth_url="https://web.example/api/auth/me",
            deploy_token="token",
        )


def test_verify_live_portal_access_blocks_missing_token():
    with pytest.raises(PortalAccessBootstrapError, match="DEPLOY_ID_TOKEN is required"):
        _verify_live_portal_access(
            live_auth_url="https://web.example/api/auth/me",
            deploy_token="",
        )
