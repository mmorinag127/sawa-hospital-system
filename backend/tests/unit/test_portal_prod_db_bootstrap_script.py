import base64
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from scripts.portal_prod_db_bootstrap import _extract_email_from_id_token  # noqa: E402
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
