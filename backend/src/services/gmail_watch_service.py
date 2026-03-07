import os
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from loguru import logger

from src.services.gmail_state_store import (
    GmailStateConfigError,
    save_watch_state,
    save_watch_error,
)


class GmailWatchConfigError(RuntimeError):
    pass


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise GmailWatchConfigError(f"missing env: {name}")
    return value


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "y")


def refresh_gmail_watch() -> dict[str, Any]:
    client_id = _get_env("GMAIL_CLIENT_ID")
    client_secret = _get_env("GMAIL_CLIENT_SECRET")
    refresh_token = _get_env("GMAIL_REFRESH_TOKEN")
    topic_name = _get_env("GMAIL_WATCH_TOPIC")

    user_id = os.getenv("GMAIL_WATCH_USER", "me")
    label_ids_raw = os.getenv("GMAIL_WATCH_LABEL_IDS", "")
    label_filter_action = os.getenv("GMAIL_WATCH_LABEL_FILTER_ACTION", "include")
    include_spam_trash = _parse_bool(os.getenv("GMAIL_WATCH_INCLUDE_SPAM_TRASH"))
    scopes_raw = os.getenv(
        "GMAIL_WATCH_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify",
    )

    scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()]
    label_ids = [l.strip() for l in label_ids_raw.split(",") if l.strip()]

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )
    session = AuthorizedSession(creds)

    payload: dict[str, Any] = {"topicName": topic_name}
    if label_ids:
        payload["labelIds"] = label_ids
        payload["labelFilterAction"] = label_filter_action
    if include_spam_trash:
        payload["includeSpamTrash"] = True

    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/watch"
    try:
        response = session.post(url, json=payload, timeout=30)
    except RefreshError as exc:
        logger.error("Gmail watch refresh failed", error="invalid_grant", detail=str(exc))
        try:
            save_watch_error("invalid_grant", str(exc))
        except GmailStateConfigError as state_exc:
            logger.warning("Watch error save skipped", error=str(state_exc))
        raise
    if response.status_code >= 300:
        logger.error(
            "Gmail watch refresh failed",
            error="http_error",
            status=response.status_code,
            detail=response.text,
        )
        try:
            save_watch_error("http_error", response.text)
        except GmailStateConfigError as state_exc:
            logger.warning("Watch error save skipped", error=str(state_exc))
        raise RuntimeError(f"watch refresh failed: {response.status_code} {response.text}")
    data = response.json()
    try:
        save_watch_state(data.get("historyId"), data.get("expiration"))
    except GmailStateConfigError as exc:
        logger.warning("Watch state save skipped", error=str(exc))
    logger.info(
        "Gmail watch refreshed",
        historyId=data.get("historyId"),
        expiration=data.get("expiration"),
    )
    return data
