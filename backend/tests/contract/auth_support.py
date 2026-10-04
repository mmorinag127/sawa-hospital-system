"""Registered hospital operator for contract tests; only Google is mocked."""

from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.api import auth as auth_module
from test_portal_google_auth import _cleanup, _seed_user


HUMAN_AUDIENCE = "hospital-contract.apps.googleusercontent.com"


@dataclass(frozen=True)
class HospitalOperator:
    user_id: str
    account: str
    token: str
    verifier: Mock

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@contextmanager
def registered_hospital_operator(monkeypatch, *, status="active", systems=("hospital",)):
    account = f"hospital-contract-{uuid4().hex}@example.invalid"
    token = f"hospital-contract-token-{uuid4().hex}"
    with monkeypatch.context() as patch:
        patch.setenv("AUTH_DISABLED", "false")
        patch.setenv("AUTH_PROVIDER", "local")
        patch.setattr(auth_module, "AUTH_PROVIDER", "local")
        patch.setattr(auth_module, "GOOGLE_OAUTH_CLIENT_IDS", [HUMAN_AUDIENCE])

        def verify(candidate_token, request, audience):
            if candidate_token != token or audience != HUMAN_AUDIENCE:
                raise ValueError("Invalid contract token or audience")
            assert isinstance(request, auth_module.google_requests.Request)
            return {"email": account, "email_verified": True, "aud": HUMAN_AUDIENCE}

        verifier = Mock(side_effect=verify)
        patch.setattr(auth_module.id_token, "verify_oauth2_token", verifier)
        # Reuse the existing canonical test grant schema and real user inserts.
        user_id = _seed_user(account, status=status, systems=systems)
        try:
            yield HospitalOperator(user_id, account, token, verifier)
        finally:
            _cleanup(user_id)


@pytest.fixture
def hospital_operator(monkeypatch):
    with registered_hospital_operator(monkeypatch) as operator:
        yield operator
