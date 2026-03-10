from fastapi import APIRouter

from src.api.auth import GOOGLE_OAUTH_CLIENT_IDS, is_auth_disabled

router = APIRouter()


@router.get("/auth/config")
def get_auth_config():
    return {
        "auth_disabled": is_auth_disabled(),
        "google_client_id": GOOGLE_OAUTH_CLIENT_IDS[0] if GOOGLE_OAUTH_CLIENT_IDS else "",
        "google_client_ids": GOOGLE_OAUTH_CLIENT_IDS,
    }
