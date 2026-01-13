import base64
import json

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from src.workers.ingest_worker import enqueue_ingest_async
from src.services.gmail_watch_service import GmailWatchConfigError, refresh_gmail_watch
from src.services.gmail_ingest_service import GmailIngestConfigError, ingest_from_notification

router = APIRouter()


def _decode_pubsub_data(message: dict) -> dict | None:
    data = message.get("data")
    if not data:
        return None
    try:
        decoded = base64.b64decode(data).decode("utf-8")
        payload = json.loads(decoded)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pub/Sub data decode failed", error=str(exc))
        return None
    return payload if isinstance(payload, dict) else None


@router.post("/pubsub/push")
async def pubsub_push(request: Request):
    payload = await request.json()
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        logger.warning("Pub/Sub push missing message")
        return {"ok": True}

    decoded = _decode_pubsub_data(message)
    if decoded and {"message_id", "pdf_uri", "received_at"}.issubset(decoded):
        enqueue_ingest_async(decoded)
        logger.info("Pub/Sub ingest enqueued", message_id=decoded.get("message_id"))
    elif decoded and {"emailAddress", "historyId"}.issubset(decoded):
        try:
            ingests = ingest_from_notification(decoded)
        except GmailIngestConfigError as exc:
            logger.warning("Gmail ingest skipped", error=str(exc))
            return {"ok": True}
        except Exception:  # noqa: BLE001
            logger.exception("Gmail ingest failed")
            return {"ok": True}
        for payload in ingests:
            enqueue_ingest_async(payload)
        logger.info("Gmail ingest enqueued", items=len(ingests))
    else:
        logger.info(
            "Pub/Sub message received",
            message_id=message.get("messageId"),
            attributes=message.get("attributes"),
        )
    return {"ok": True}


@router.post("/watch-refresh")
async def watch_refresh():
    try:
        result = refresh_gmail_watch()
    except GmailWatchConfigError as exc:
        logger.warning("Watch refresh skipped", error=str(exc))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Watch refresh failed")
        raise HTTPException(status_code=500, detail="watch refresh failed") from exc
    return {
        "ok": True,
        "historyId": result.get("historyId"),
        "expiration": result.get("expiration"),
    }
