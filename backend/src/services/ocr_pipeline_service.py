from __future__ import annotations

from typing import Any
from datetime import datetime
import json
import os
import time
from loguru import logger
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from src.services.storage_service import (
    get_default_output_bucket,
    get_default_artifact_bucket,
)


def _sanitize_object_name(value: str) -> str:
    return (
        value.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )


def _get_pipeline_bucket() -> str:
    bucket = (
        os.getenv("OCR_PIPELINE_BUCKET")
        or os.getenv("OCR_PIPELINE_INPUT_BUCKET")
        or get_default_output_bucket()
        or get_default_artifact_bucket()
    )
    if not bucket:
        raise RuntimeError("OCR pipeline bucket is not configured")
    return bucket


def _get_pipeline_url() -> str | None:
    url = os.getenv("OCR_PIPELINE_URL", "").strip()
    return url or None


def _get_prefix(env_name: str, fallback: str) -> str:
    prefix = os.getenv(env_name, fallback)
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def _get_request_timeout() -> float:
    raw = os.getenv("OCR_PIPELINE_REQUEST_TIMEOUT_SECONDS", "600")
    try:
        return float(raw)
    except ValueError:
        return 600.0


def _parse_gs_uri(uri: str) -> tuple[str, str] | None:
    parsed = urlparse(uri)
    if parsed.scheme != "gs":
        return None
    bucket = parsed.netloc
    name = parsed.path.lstrip("/")
    if not bucket or not name:
        return None
    return bucket, name


def _upload_pdf_bytes(
    *,
    bucket: str,
    object_name: str,
    pdf_bytes: bytes,
    metadata: dict[str, str] | None,
) -> str:
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("google-cloud-storage is required for OCR pipeline uploads") from exc
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_name)
    if metadata:
        blob.metadata = metadata
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")
    blob.reload()
    generation = str(blob.generation or "")
    return generation


def _wait_for_output(
    *,
    bucket: str,
    object_name: str,
    timeout_seconds: int,
    poll_interval: float,
) -> dict[str, Any]:
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("google-cloud-storage is required for OCR pipeline output") from exc
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_name)
    start = time.monotonic()
    deadline = start + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if blob.exists():
                data = blob.download_as_bytes()
                elapsed = time.monotonic() - start
                logger.info(
                    "OCR pipeline output found bucket=%s name=%s elapsed=%.1fs",
                    bucket,
                    object_name,
                    elapsed,
                )
                return json.loads(data.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OCR pipeline output read failed bucket=%s name=%s error=%s",
                bucket,
                object_name,
                str(exc),
            )
        time.sleep(poll_interval)
    elapsed = time.monotonic() - start
    logger.warning(
        "OCR pipeline output timeout bucket=%s name=%s elapsed=%.1fs",
        bucket,
        object_name,
        elapsed,
    )
    raise TimeoutError(f"OCR pipeline output not found: gs://{bucket}/{object_name}")


def run_ocr_pipeline(
    *,
    pdf_bytes: bytes,
    job_id: str,
    facility_id: str | None = None,
    input_reference: str | None = None,
    preferred_template_id: str | None = None,
    force_upload: bool = False,
    wait_for_output: bool = True,
) -> dict[str, Any]:
    bucket = _get_pipeline_bucket()
    input_prefix = _get_prefix("OCR_PIPELINE_INPUT_PREFIX", "input/")
    output_prefix = _get_prefix("OCR_PIPELINE_OUTPUT_PREFIX", "output/")

    generation = ""
    input_name = None
    parsed_input = _parse_gs_uri(input_reference) if input_reference else None
    if not force_upload and parsed_input and parsed_input[0] == bucket and parsed_input[1].startswith(input_prefix):
        input_name = parsed_input[1]
        logger.info(
            "OCR pipeline input reuse bucket=%s name=%s",
            bucket,
            input_name,
        )
    if force_upload or not input_name:
        safe_job_id = _sanitize_object_name(job_id)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        input_name = f"{input_prefix}{safe_job_id}_{stamp}.pdf"
        metadata = {}
        if facility_id:
            metadata["facility_id"] = facility_id
        if preferred_template_id:
            metadata["template_id"] = preferred_template_id
        logger.info(
            "OCR pipeline upload start bucket=%s name=%s bytes=%s",
            bucket,
            input_name,
            len(pdf_bytes),
        )
        generation = _upload_pdf_bytes(
            bucket=bucket,
            object_name=input_name,
            pdf_bytes=pdf_bytes,
            metadata=metadata,
        )
        logger.info(
            "OCR pipeline upload done bucket=%s name=%s generation=%s",
            bucket,
            input_name,
            generation,
        )

    payload = {"bucket": bucket, "name": input_name}
    if generation:
        payload["generation"] = generation

    url = _get_pipeline_url()
    if url and wait_for_output:
        logger.info("OCR pipeline request POST url=%s", url)
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=_get_request_timeout()) as resp:
                resp.read()
            logger.info("OCR pipeline request POST done url=%s", url)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OCR pipeline HTTP {exc.code}: {raw}") from exc
        except URLError as exc:
            raise RuntimeError(f"OCR pipeline request failed: {exc}") from exc
    elif url:
        logger.info("OCR pipeline request skipped (async) url=%s", url)
    else:
        logger.info("OCR pipeline request skipped (OCR_PIPELINE_URL not set)")

    output_name = f"{output_prefix}{os.path.basename(input_name)}.json"
    output_reference = f"gs://{bucket}/{output_name}"
    input_reference = f"gs://{bucket}/{input_name}"
    if not wait_for_output:
        return {
            "status": "running",
            "output_reference": output_reference,
            "input_reference": input_reference,
        }
    timeout_seconds = int(os.getenv("OCR_PIPELINE_TIMEOUT_SECONDS", "600"))
    poll_interval = float(os.getenv("OCR_PIPELINE_POLL_INTERVAL_SECONDS", "2.0"))
    logger.info(
        "OCR pipeline wait output bucket=%s name=%s timeout=%ss interval=%ss",
        bucket,
        output_name,
        timeout_seconds,
        poll_interval,
    )
    return _wait_for_output(
        bucket=bucket,
        object_name=output_name,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )
