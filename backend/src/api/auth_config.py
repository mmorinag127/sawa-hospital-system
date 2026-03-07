from fastapi import APIRouter

from src.api.auth import AUTH_DISABLED, GOOGLE_OAUTH_CLIENT_IDS

router = APIRouter()


@router.get("/auth/config")
def get_auth_config():
    return {
        "auth_disabled": AUTH_DISABLED,
        "google_client_id": GOOGLE_OAUTH_CLIENT_IDS[0] if GOOGLE_OAUTH_CLIENT_IDS else "",
        "google_client_ids": GOOGLE_OAUTH_CLIENT_IDS,
    }
