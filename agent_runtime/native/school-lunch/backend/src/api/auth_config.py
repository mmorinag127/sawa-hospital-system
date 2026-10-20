from fastapi import APIRouter, Depends

from src.api.auth import GOOGLE_OAUTH_CLIENT_IDS, UserContext, get_current_user, is_auth_disabled

router = APIRouter()


@router.get("/auth/config")
def get_auth_config():
    return {
        "google_client_id": GOOGLE_OAUTH_CLIENT_IDS[0] if GOOGLE_OAUTH_CLIENT_IDS else "",
    }


@router.get("/auth/me")
def get_auth_me(user: UserContext = Depends(get_current_user)):
    return {
        "role": user.role,
        "auth_disabled": is_auth_disabled(),
    }
