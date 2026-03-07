import base64
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from loguru import logger

from src.services.storage_service import save_bytes_to_gcs
from src.services.gmail_state_store import GmailStateConfigError, load_watch_state, save_watch_state


class GmailIngestConfigError(RuntimeError):
    pass


class GmailHistoryExpiredError(RuntimeError):
    pass


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise GmailIngestConfigError(f"missing env: {name}")
    return value


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y")


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _decode_base64_url(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def _sanitize_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value).strip("_")
    return cleaned or fallback


def _sanitize_filename(name: str, fallback: str) -> str:
    safe = _sanitize_segment(os.path.basename(name) if name else "", fallback)
    if not safe.lower().endswith(".pdf"):
        safe = f"{safe}.pdf"
    return safe


def _gmail_scopes() -> list[str]:
    scopes_raw = os.getenv(
        "GMAIL_WATCH_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify",
    )
    return [s.strip() for s in scopes_raw.split(",") if s.strip()]


def _gmail_session() -> AuthorizedSession:
    client_id = _get_env("GMAIL_CLIENT_ID")
    client_secret = _get_env("GMAIL_CLIENT_SECRET")
    refresh_token = _get_env("GMAIL_REFRESH_TOKEN")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=_gmail_scopes(),
    )
    return AuthorizedSession(creds)


def _gmail_user() -> str:
    return os.getenv("GMAIL_WATCH_USER", "me")


def get_gmail_profile_email() -> str | None:
    try:
        session = _gmail_session()
    except Exception:
        return None
    user_id = _gmail_user()
    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/profile"
    try:
        response = session.get(url, timeout=20)
        if response.status_code >= 300:
            return None
        data = response.json()
        email = data.get("emailAddress")
        return str(email) if email else None
    except Exception:
        return None


@dataclass(frozen=True)
class GmailAttachment:
    message_id: str
    filename: str
    mime_type: str
    attachment_id: str | None
    data: bytes | None


def _iter_parts(part: dict) -> Iterable[dict]:
    parts = part.get("parts") or []
    for child in parts:
        yield from _iter_parts(child)
    yield part


def _extract_pdf_attachments(message: dict) -> list[GmailAttachment]:
    payload = message.get("payload") or {}
    message_id = message.get("id", "")
    attachments: list[GmailAttachment] = []
    for part in _iter_parts(payload):
        mime_type = part.get("mimeType") or ""
        filename = part.get("filename") or ""
        if not (mime_type == "application/pdf" or filename.lower().endswith(".pdf")):
            continue
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        data = body.get("data")
        decoded = _decode_base64_url(data) if data else None
        if attachment_id or decoded:
            attachments.append(
                GmailAttachment(
                    message_id=message_id,
                    filename=filename,
                    mime_type=mime_type or "application/pdf",
                    attachment_id=attachment_id,
                    data=decoded,
                )
            )
    return attachments


def _get_message_received_at(message: dict) -> str:
    raw = message.get("internalDate")
    if isinstance(raw, str) and raw.isdigit():
        ts = int(raw) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return datetime.now(tz=timezone.utc).isoformat()


def _list_messages(
    session: AuthorizedSession,
    user_id: str,
    query: str,
    label_ids: list[str],
    max_results: int,
) -> list[str]:
    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages"
    params: dict[str, Any] = {"maxResults": max_results}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids
    for attempt in range(1, 4):
        response = session.get(url, params=params, timeout=30)
        if response.status_code == 429:
            time.sleep(1.5 * attempt)
            continue
        if response.status_code >= 300:
            raise RuntimeError(f"gmail list failed: {response.status_code} {response.text}")
        data = response.json()
        return [
            m["id"]
            for m in data.get("messages", [])
            if isinstance(m, dict) and m.get("id")
        ]
    raise RuntimeError("gmail list failed: 429 rate limit")


def _list_history_message_ids(
    session: AuthorizedSession,
    user_id: str,
    start_history_id: str,
    label_ids: list[str],
) -> tuple[list[str], str | None]:
    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/history"
    params: dict[str, Any] = {
        "startHistoryId": start_history_id,
        "historyTypes": "messageAdded",
    }
    if label_ids:
        params["labelId"] = label_ids[0]
    message_ids: set[str] = set()
    latest_history_id: str | None = None
    while True:
        response = session.get(url, params=params, timeout=30)
        if response.status_code == 404:
            raise GmailHistoryExpiredError("historyId too old or invalid")
        if response.status_code >= 300:
            raise RuntimeError(f"gmail history failed: {response.status_code} {response.text}")
        data = response.json()
        latest_history_id = data.get("historyId") or latest_history_id
        for item in data.get("history", []):
            for added in item.get("messagesAdded", []):
                message = added.get("message") or {}
                message_id = message.get("id")
                if message_id:
                    message_ids.add(message_id)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        params["pageToken"] = page_token
    return sorted(message_ids), latest_history_id


def _get_message(session: AuthorizedSession, user_id: str, message_id: str) -> dict:
    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}"
    response = session.get(url, params={"format": "full"}, timeout=30)
    if response.status_code >= 300:
        raise RuntimeError(f"gmail message fetch failed: {response.status_code} {response.text}")
    return response.json()


def _get_attachment_bytes(
    session: AuthorizedSession,
    user_id: str,
    message_id: str,
    attachment: GmailAttachment,
) -> bytes:
    if attachment.data is not None:
        return attachment.data
    if not attachment.attachment_id:
        raise RuntimeError("missing attachment id")
    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}/attachments/{attachment.attachment_id}"
    response = session.get(url, timeout=30)
    if response.status_code >= 300:
        raise RuntimeError(f"gmail attachment fetch failed: {response.status_code} {response.text}")
    data = response.json().get("data")
    if not data:
        raise RuntimeError("attachment payload missing data")
    return _decode_base64_url(data)


def _mark_message_read(session: AuthorizedSession, user_id: str, message_id: str) -> None:
    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}/modify"
    response = session.post(url, json={"removeLabelIds": ["UNREAD"]}, timeout=30)
    if response.status_code >= 300:
        raise RuntimeError(f"gmail modify failed: {response.status_code} {response.text}")


def mark_message_read(message_id: str) -> None:
    session = _gmail_session()
    user_id = _gmail_user()
    _mark_message_read(session, user_id, message_id)


def _parse_override_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _parse_bool(value)
    return None


def _parse_override_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_override_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return _parse_csv(value)
    return None


def ingest_from_notification(notification: dict) -> list[dict[str, str]]:
    raw_bucket = _get_env("RAW_BUCKET")
    override_query = notification.get("query") if isinstance(notification, dict) else None
    override_label_ids = notification.get("label_ids") if isinstance(notification, dict) else None
    override_max_results = notification.get("max_results") if isinstance(notification, dict) else None
    override_mark_read = notification.get("mark_read") if isinstance(notification, dict) else None
    override_prefix = notification.get("prefix") if isinstance(notification, dict) else None
    force_full_scan = _parse_override_bool(
        notification.get("force_full_scan") if isinstance(notification, dict) else None
    )

    query = os.getenv("GMAIL_INGEST_QUERY", "is:unread has:attachment")
    if isinstance(override_query, str) and override_query.strip():
        query = override_query.strip()
    label_ids = _parse_csv(os.getenv("GMAIL_INGEST_LABEL_IDS"))
    override_labels = _parse_override_list(override_label_ids)
    if override_labels is not None:
        label_ids = override_labels
    max_results = int(os.getenv("GMAIL_INGEST_MAX_RESULTS", "10"))
    override_max = _parse_override_int(override_max_results)
    if override_max is not None:
        max_results = override_max
    mark_read = _parse_bool(os.getenv("GMAIL_INGEST_MARK_READ"), default=True)
    override_mark = _parse_override_bool(override_mark_read)
    if override_mark is not None:
        mark_read = override_mark
    prefix = _sanitize_segment(os.getenv("GMAIL_INGEST_PREFIX", "gmail"), "gmail")
    if isinstance(override_prefix, str) and override_prefix.strip():
        prefix = _sanitize_segment(override_prefix.strip(), "gmail")

    session = _gmail_session()
    user_id = _gmail_user()
    notification_history_id = str(notification.get("historyId")) if notification.get("historyId") else None
    stored_history_id = None
    state: dict[str, Any] = {}
    try:
        state = load_watch_state() or {}
        stored_history_id = state.get("historyId")
    except GmailStateConfigError as exc:
        logger.warning("Watch state load skipped", error=str(exc))

    message_ids: list[str]
    latest_history_id: str | None = None
    if force_full_scan:
        message_ids = _list_messages(session, user_id, query, label_ids, max_results)
    elif notification_history_id and stored_history_id:
        try:
            message_ids, latest_history_id = _list_history_message_ids(
                session,
                user_id,
                stored_history_id,
                label_ids,
            )
        except GmailHistoryExpiredError:
            logger.warning("HistoryId expired; falling back to query scan")
            message_ids = _list_messages(session, user_id, query, label_ids, max_results)
        except Exception:  # noqa: BLE001
            logger.exception("History scan failed; falling back to query scan")
            message_ids = _list_messages(session, user_id, query, label_ids, max_results)
    else:
        message_ids = _list_messages(session, user_id, query, label_ids, max_results)

    if notification_history_id and stored_history_id and not message_ids:
        logger.warning("History scan empty; falling back to query scan")
        message_ids = _list_messages(session, user_id, query, label_ids, max_results)

    logger.info(
        f"Gmail ingest scan: messages={len(message_ids)} query={query!r} labels={label_ids}",
        notification_email=notification.get("emailAddress"),
        notification_history_id=notification_history_id,
    )

    ingests: list[dict[str, str | bool]] = []
    for message_id in message_ids:
        message = _get_message(session, user_id, message_id)
        received_at = _get_message_received_at(message)
        attachments = _extract_pdf_attachments(message)
        if not attachments:
            continue
        seen_filenames: dict[str, int] = {}
        for idx, attachment in enumerate(attachments, start=1):
            blob_bytes = _get_attachment_bytes(session, user_id, message_id, attachment)
            name_hint = attachment.filename or attachment.attachment_id or f"attachment-{idx}"
            safe_filename = _sanitize_filename(name_hint, f"attachment-{idx}")
            count = seen_filenames.get(safe_filename, 0) + 1
            seen_filenames[safe_filename] = count
            if count > 1:
                stem, dot, suffix = safe_filename.rpartition(".")
                stem = stem or safe_filename
                safe_filename = f"{stem}-{count}{dot}{suffix}" if dot else f"{stem}-{count}"
            safe_message = _sanitize_segment(message_id, "message")
            object_path = f"{prefix}/{safe_message}/{safe_filename}"
            pdf_uri = save_bytes_to_gcs(raw_bucket, object_path, blob_bytes, attachment.mime_type)
            ingest_message_id = f"{message_id}:{_sanitize_segment(safe_filename, str(idx))}"
            ingests.append(
                {
                    "message_id": ingest_message_id,
                    "gmail_message_id": message_id,
                    "gmail_mark_read": mark_read,
                    "pdf_uri": pdf_uri,
                    "received_at": received_at,
                }
            )
    logger.info(f"Gmail ingest prepared: enqueued={len(ingests)}")
    if notification_history_id:
        try:
            save_watch_state(
                latest_history_id or notification_history_id,
                (state or {}).get("expiration") if stored_history_id else None,
            )
        except GmailStateConfigError as exc:
            logger.warning("Watch state save skipped", error=str(exc))
    return ingests
