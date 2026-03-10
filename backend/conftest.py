import base64
import os

import pytest


# Keep the existing test suite behavior stable while production defaults fail closed.
os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("OPERATOR_USER", "operator")
os.environ.setdefault("OPERATOR_PASSWORD", "operator-pass")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-pass")


def _basic_auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def operator_auth_headers() -> dict[str, str]:
    return _basic_auth_headers(
        os.environ["OPERATOR_USER"],
        os.environ["OPERATOR_PASSWORD"],
    )


@pytest.fixture
def admin_auth_headers() -> dict[str, str]:
    return _basic_auth_headers(
        os.environ["ADMIN_USER"],
        os.environ["ADMIN_PASSWORD"],
    )
