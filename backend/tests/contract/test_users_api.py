import pathlib
import sys

from fastapi.testclient import TestClient
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.user import User  # noqa: E402


def _clear_users() -> None:
    with session_scope() as session:
        session.execute(delete(User))


def test_users_api_upsert_and_update():
    _clear_users()
    client = TestClient(app)

    empty_res = client.get("/users")
    assert empty_res.status_code == 200
    assert empty_res.json().get("items") == []

    invalid_res = client.post("/users", json={"account": "invalid", "role": "operator"})
    assert invalid_res.status_code == 400
    assert invalid_res.json().get("detail") == "invalid_account"

    create_res = client.post(
        "/users",
        json={"account": "addonmeal2023@gmail.com", "role": "operator", "status": "active"},
    )
    assert create_res.status_code == 200
    create_payload = create_res.json()
    assert create_payload.get("created") is True
    user = create_payload.get("user") or {}
    assert user.get("account") == "addonmeal2023@gmail.com"
    assert user.get("role") == "operator"
    assert user.get("status") == "active"

    upsert_res = client.post(
        "/users",
        json={"account": "addonmeal2023@gmail.com", "role": "admin", "status": "active"},
    )
    assert upsert_res.status_code == 200
    upsert_payload = upsert_res.json()
    assert upsert_payload.get("created") is False
    upsert_user = upsert_payload.get("user") or {}
    assert upsert_user.get("account") == "addonmeal2023@gmail.com"
    assert upsert_user.get("role") == "admin"

    user_id = str(upsert_user.get("id") or "")
    assert user_id

    update_res = client.put(f"/users/{user_id}", json={"status": "inactive"})
    assert update_res.status_code == 200
    assert update_res.json().get("updated") is True

    list_res = client.get("/users")
    assert list_res.status_code == 200
    items = list_res.json().get("items") or []
    assert len(items) == 1
    assert items[0].get("account") == "addonmeal2023@gmail.com"
    assert items[0].get("role") == "admin"
    assert items[0].get("status") == "inactive"
