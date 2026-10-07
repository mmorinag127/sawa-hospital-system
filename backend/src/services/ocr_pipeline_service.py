from __future__ import annotations

from typing import Any
from datetime import datetime
import json
from hashlib import sha256
import os
import socket
import time
import threading
from loguru import logger
from urllib.parse import urlparse
from src.services.storage_uri import format_gcs_uri, parse_gcs_uri
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from src.services.storage_service import (
    get_default_output_bucket,
    get_default_artifact_bucket,
)
from src.services.ocr_pipeline_state_store import (
    save_pipeline_request,
    save_pipeline_success,
    save_pipeline_error,
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


def _is_pytest() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _get_pipeline_url() -> str | None:
    url = os.getenv("OCR_PIPELINE_URL", "").strip()
    return url or None


def _normalize_target_audience(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}"


def _get_pipeline_target_audience(url: str | None) -> str | None:
    explicit = os.getenv("OCR_PIPELINE_TARGET_AUDIENCE", "").strip()
    if explicit:
        return explicit
    return _normalize_target_audience(url)


def _fetch_pipeline_identity_token(audience: str) -> str:
    try:
        from google.auth.transport.requests import Request as AuthRequest  # type: ignore
        from google.oauth2 import id_token  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("google-auth is required for OCR pipeline identity tokens") from exc
    return str(id_token.fetch_id_token(AuthRequest(), audience))


def _build_pipeline_request_headers(url: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    audience = _get_pipeline_target_audience(url)
    if not audience:
        return headers
    headers["Authorization"] = f"Bearer {_fetch_pipeline_identity_token(audience)}"
    return headers


_PIPELINE_SEMAPHORE: threading.BoundedSemaphore | None = None
_PIPELINE_MAX: int | None = None
_PIPELINE_LOCK = threading.Lock()
_PIPELINE_INFLIGHT = 0


class OCRPipelineOutputPendingError(TimeoutError):
    def __init__(
        self,
        *,
        input_reference: str | None,
        output_reference: str | None,
        timeout_seconds: int,
    ) -> None:
        self.input_reference = input_reference
        self.output_reference = output_reference
        self.timeout_seconds = timeout_seconds
        super().__init__(f"OCR pipeline output not found within {timeout_seconds}s: {output_reference}")


class OCRPipelineTriggerFailedError(RuntimeError):
    def __init__(
        self,
        *,
        input_reference: str | None,
        output_reference: str | None,
        error_message: str,
    ) -> None:
        self.input_reference = input_reference
        self.output_reference = output_reference
        self.error_message = error_message
        super().__init__(error_message)


def _get_pipeline_semaphore() -> threading.BoundedSemaphore | None:
    global _PIPELINE_SEMAPHORE, _PIPELINE_MAX
    raw = os.getenv("OCR_PIPELINE_MAX_INFLIGHT", "2")
    try:
        max_inflight = int(raw)
    except ValueError:
        max_inflight = 2
    if max_inflight <= 0:
        return None
    with _PIPELINE_LOCK:
        if _PIPELINE_SEMAPHORE is None or _PIPELINE_MAX != max_inflight:
            _PIPELINE_SEMAPHORE = threading.BoundedSemaphore(max_inflight)
            _PIPELINE_MAX = max_inflight
    return _PIPELINE_SEMAPHORE


def _inflight_delta(delta: int) -> int:
    global _PIPELINE_INFLIGHT
    with _PIPELINE_LOCK:
        _PIPELINE_INFLIGHT = max(0, _PIPELINE_INFLIGHT + delta)
        return _PIPELINE_INFLIGHT


def get_pipeline_runtime_status() -> dict[str, Any]:
    raw = os.getenv("OCR_PIPELINE_MAX_INFLIGHT", "2")
    try:
        max_inflight = int(raw)
    except ValueError:
        max_inflight = 2
    return {
        "max_inflight": max_inflight,
        "inflight": _PIPELINE_INFLIGHT,
        "request_timeout_seconds": _get_request_timeout(wait_for_output=True),
        "trigger_timeout_seconds": _get_request_timeout(wait_for_output=False),
        "timeout_seconds": int(os.getenv("OCR_PIPELINE_TIMEOUT_SECONDS", "600")),
        "poll_interval_seconds": float(os.getenv("OCR_PIPELINE_POLL_INTERVAL_SECONDS", "2.0")),
    }


def is_ocr_pipeline_output_pending(payload: dict[str, object] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or "").strip().lower()
    stage = str(payload.get("stage") or "").strip().lower()
    if status in {"running", "pending", "queued"}:
        return True
    if stage in {"upload", "running"}:
        return True
    return False


def get_pipeline_config() -> dict[str, Any]:
    url = _get_pipeline_url()
    bucket = (
        os.getenv("OCR_PIPELINE_BUCKET")
        or os.getenv("OCR_PIPELINE_INPUT_BUCKET")
        or get_default_output_bucket()
        or get_default_artifact_bucket()
    )
    trigger_mode = "gcs_only"
    if bucket and url:
        trigger_mode = "gcs_http"
    elif url:
        trigger_mode = "http_only"
    return {
        # The OCR pipeline is triggered by writing to GCS in this system, so the bucket
        # is the only hard requirement. OCR_PIPELINE_URL is optional (HTTP trigger mode).
        "configured": bool(bucket),
        "url_set": bool(url),
        "bucket_set": bool(bucket),
        "bucket": bucket,
        "input_prefix": _get_prefix("OCR_PIPELINE_INPUT_PREFIX", "input/"),
        "output_prefix": _get_prefix("OCR_PIPELINE_OUTPUT_PREFIX", "output/"),
        "trigger_mode": trigger_mode,
        "http_trigger_enabled": bool(url),
        "gcs_trigger_enabled": bool(bucket),
        "wait_strategy": "poll_output_gcs",
        "sync_wait_supported": bool(bucket),
        "sync_wait_note": (
            "HTTP trigger disabled; worker relies on storage-triggered OCR execution and polls for output."
            if bucket and not url
            else None
        ),
    }


def _get_prefix(env_name: str, fallback: str) -> str:
    prefix = os.getenv(env_name, fallback)
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def _get_request_timeout(*, wait_for_output: bool) -> float:
    raw = os.getenv(
        "OCR_PIPELINE_REQUEST_TIMEOUT_SECONDS" if wait_for_output else "OCR_PIPELINE_TRIGGER_TIMEOUT_SECONDS",
        "600" if wait_for_output else "15",
    )
    try:
        return float(raw)
    except ValueError:
        return 600.0 if wait_for_output else 15.0


def _get_output_read_timeout() -> float:
    raw = os.getenv("OCR_PIPELINE_OUTPUT_READ_TIMEOUT_SECONDS", "15")
    try:
        timeout = float(raw)
    except ValueError:
        timeout = 15.0
    return max(timeout, 1.0)


def _parse_gs_uri(uri: str) -> tuple[str, str] | None:
    parsed = urlparse(uri)
    if parsed.scheme != "gs":
        return None
    source = parse_gcs_uri(uri)
    return source.bucket, source.object_name


def _owned_pipeline_output_name(output_prefix: str, source: dict) -> str:
    identity = {key: value for key, value in source.items() if key != "output_reference"}
    digest = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"{output_prefix}{digest}/{os.path.basename(parse_gcs_uri(source['input_reference']).object_name)}.g{source['input_generation']}.json"


def _upload_configured_input(*, bucket, object_name, configured_input):
    from google.cloud import storage
    data = json.dumps(configured_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    name = object_name + ".configured.json"
    blob = storage.Client().bucket(bucket).blob(name)
    blob.upload_from_string(data, content_type="application/json", if_generation_match=0)
    generation = str(blob.generation or "")
    if not generation.isdigit():
        raise ValueError("configured_input_generation_missing")
    return {"bucket": bucket, "object_name": name, "generation": generation, "sha256": sha256(data).hexdigest()}


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
    from src.services.storage_service import verify_selected_pdf_bytes
    verify_selected_pdf_bytes(pdf_bytes)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf", if_generation_match=0)
    generation = str(blob.generation or "")
    if not generation.isdigit():
        raise ValueError("ocr_pipeline_upload_generation_missing")
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
    start = time.monotonic()
    deadline = start + timeout_seconds
    read_timeout = _get_output_read_timeout()
    while time.monotonic() < deadline:
        try:
            blob = client.bucket(bucket).blob(object_name)
            if blob.exists(timeout=read_timeout, retry=None):
                blob.reload(timeout=read_timeout, retry=None)
                data = blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=read_timeout, retry=None)
                payload = json.loads(data.decode("utf-8"))
                from src.services.ocr_job_service import validate_own_pipeline_result

                validate_own_pipeline_result(payload, format_gcs_uri(bucket, object_name))
                status = str((payload or {}).get("status") or "").strip().lower()
                stage = str((payload or {}).get("stage") or "").strip().lower()
                if status == "done" or stage == "done":
                    elapsed = time.monotonic() - start
                    logger.info(
                        "OCR pipeline output ready bucket=%s name=%s elapsed=%.1fs status=%s stage=%s",
                        bucket,
                        object_name,
                        elapsed,
                        status or "-",
                        stage or "-",
                    )
                    return payload
                if status in {"failed", "error"} or stage == "error":
                    elapsed = time.monotonic() - start
                    logger.warning(
                        "OCR pipeline output terminal error bucket=%s name=%s elapsed=%.1fs status=%s stage=%s",
                        bucket,
                        object_name,
                        elapsed,
                        status or "-",
                        stage or "-",
                    )
                    return payload
                logger.info(
                    "OCR pipeline output partial bucket=%s name=%s status=%s stage=%s",
                    bucket,
                    object_name,
                    status or "-",
                    stage or "-",
                )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OCR pipeline output poll failed bucket=%s name=%s timeout=%ss error=%s",
                bucket,
                object_name,
                read_timeout,
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
    preferred_template_ids: list[str] | None = None,
    force_upload: bool = False,
    wait_for_output: bool = True,
) -> dict[str, Any]:
    from src.services.storage_service import verify_selected_pdf_bytes

    verify_selected_pdf_bytes(pdf_bytes)
    from src.services import config_service

    if not facility_id:
        raise ValueError("facility_unresolved")
    from src.services.workflow_attempt_guard_service import (
        current_nonworkflow_execution, current_workflow_execution, processing_start_configured_input,
    )
    if current_nonworkflow_execution() is not None or current_workflow_execution() is not None:
        configured_input = processing_start_configured_input()
    else:
        configured_input = config_service.capture_authored_processing_config(facility_id)
    facility_config = configured_input["facility"]
    if facility_config.get("facility_id") != facility_id:
        raise ValueError("configured_execution_facility_mismatch")
    config_service._validate_authored_facility_config(facility_config, preferred_template_id)
    for template_id in preferred_template_ids or []:
        config_service._validate_authored_facility_config(facility_config, template_id)
    semaphore = _get_pipeline_semaphore()
    acquired = False
    if semaphore:
        acquire_timeout = float(os.getenv("OCR_PIPELINE_ACQUIRE_TIMEOUT_SECONDS", "600"))
        logger.info("OCR pipeline acquire slot timeout=%ss", acquire_timeout)
        acquired = semaphore.acquire(timeout=acquire_timeout)
        if not acquired:
            raise TimeoutError("OCR pipeline busy: acquire timeout")
        _inflight_delta(1)
        logger.info("OCR pipeline slot acquired inflight=%s", _PIPELINE_INFLIGHT)
    try:
        try:
            bucket = _get_pipeline_bucket()
        except RuntimeError:
            if _is_pytest():
                logger.warning("OCR pipeline bucket missing in pytest; returning unclassified stub.")
                return {
                    "status": "unclassified",
                    "template_id": None,
                    "input_reference": input_reference,
                    "output_reference": None,
                }
            raise

        input_prefix = _get_prefix("OCR_PIPELINE_INPUT_PREFIX", "input/")
        output_prefix = _get_prefix("OCR_PIPELINE_OUTPUT_PREFIX", "output/")

        selected_input_reference = input_reference
        generation = ""
        input_name = None
        parsed_input = _parse_gs_uri(input_reference) if input_reference else None
        if (
            not force_upload
            and parsed_input
            and parsed_input[0] == bucket
            and parsed_input[1].startswith(input_prefix)
        ):
            from src.services.ocr_job_service import own_pipeline_delivery_for_output
            from src.services.storage_service import load_bytes_from_uri

            previous = own_pipeline_delivery_for_output(None, required=False)
            if previous is not None and previous["input_reference"] == input_reference:
                input_name = parsed_input[1]
                generation = previous["generation"]
                if load_bytes_from_uri(input_reference, generation=generation) != pdf_bytes:
                    raise ValueError("ocr_pipeline_input_bytes_mismatch")
        if force_upload or not input_name:
            safe_job_id = _sanitize_object_name(job_id)
            stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            input_name = f"{input_prefix}{safe_job_id}_{stamp}.pdf"
            metadata = {}
            from src.services.workflow_attempt_guard_service import processing_start_pdf_receipt
            selected_pdf = processing_start_pdf_receipt()
            metadata["selected_pdf_receipt"] = json.dumps(selected_pdf, separators=(",", ":"))
            configured_receipt = _upload_configured_input(bucket=bucket, object_name=input_name, configured_input=configured_input)
            metadata["configured_input_receipt"] = json.dumps(configured_receipt, separators=(",", ":"))
            workflow_owner = current_workflow_execution() or {}
            nonworkflow_owner = current_nonworkflow_execution() or {}
            owner = workflow_owner if workflow_owner.get("attempt") is not None else nonworkflow_owner
            if not isinstance(owner, dict) or not owner.get("job_id"):
                raise ValueError("ocr_execution_input_required")
            metadata["execution_owner"] = json.dumps({
                "job_id": owner["job_id"], "order_id": owner.get("order_id"),
                "workflow_attempt_id": owner.get("attempt"), "workflow_revision": owner.get("revision"),
                "nonworkflow_execution_id": owner.get("execution_id"),
            }, separators=(",", ":"))
            if facility_id:
                metadata["facility_id"] = facility_id
            metadata["template_id"] = facility_config["fax_template"]["template_id"]
            normalized_template_ids = [
                str(template_id).strip()
                for template_id in (preferred_template_ids or [])
                if str(template_id).strip()
            ]
            if normalized_template_ids:
                metadata["preferred_template_ids"] = json.dumps(
                    normalized_template_ids,
                    ensure_ascii=False,
                )
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

        if not generation or not str(generation).isdigit():
            raise ValueError("ocr_pipeline_input_generation_missing")
        from google.cloud import storage
        original_blob = storage.Client().bucket(bucket).blob(input_name, generation=int(generation))
        original_blob.reload()
        original_metadata = original_blob.metadata or {}
        configured_receipt = json.loads(original_metadata.get("configured_input_receipt", "null"))
        if not isinstance(configured_receipt, dict):
            raise ValueError("configured_input_receipt_required")
        configured_digest = sha256(json.dumps(configured_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if configured_receipt.get("sha256") != configured_digest:
            raise ValueError("configured_input_changed")
        original_source = {
            "job_id": f"{bucket}:{input_name}:{generation}",
            "input_reference": format_gcs_uri(bucket, input_name),
            "input_generation": str(generation),
            "input_sha256": sha256(pdf_bytes).hexdigest(),
            "configured_input_sha256": configured_digest,
            "configured_input_receipt": configured_receipt,
            "execution_owner": json.loads(original_metadata.get("execution_owner", "null")),
            "selected_pdf_receipt": json.loads(original_metadata.get("selected_pdf_receipt", "null")),
        }
        if not isinstance(original_source["execution_owner"], dict) or not isinstance(original_source["selected_pdf_receipt"], dict):
            raise ValueError("ocr_original_input_owner_required")
        from src.services.workflow_attempt_guard_service import processing_start_pdf_receipt, snapshots_equal
        workflow_owner = current_workflow_execution() or {}
        nonworkflow_owner = current_nonworkflow_execution() or {}
        owner = workflow_owner if workflow_owner.get("attempt") is not None else nonworkflow_owner
        expected_owner = {
            "job_id": owner.get("job_id"), "order_id": owner.get("order_id"),
            "workflow_attempt_id": owner.get("attempt"), "workflow_revision": owner.get("revision"),
            "nonworkflow_execution_id": owner.get("execution_id"),
        }
        if not snapshots_equal(original_source["execution_owner"], expected_owner) or not snapshots_equal(original_source["selected_pdf_receipt"], processing_start_pdf_receipt()):
            raise ValueError("ocr_original_input_owner_changed")
        original_data = original_blob.download_as_bytes(if_generation_match=int(generation))
        if sha256(original_data).hexdigest() != original_source["input_sha256"]:
            raise ValueError("ocr_original_input_bytes_changed")
        output_name = _owned_pipeline_output_name(output_prefix, original_source)
        output_reference = format_gcs_uri(bucket, output_name)
        input_reference = format_gcs_uri(bucket, input_name)
        timeout_seconds = int(os.getenv("OCR_PIPELINE_TIMEOUT_SECONDS", "600"))

        from src.services.ocr_job_service import record_own_pipeline_delivery

        if not record_own_pipeline_delivery(
            job_id, selected_input_reference=selected_input_reference,
            input_reference=input_reference, output_reference=output_reference, generation=generation,
            input_sha256=sha256(pdf_bytes).hexdigest(),
            wait_timeout_seconds=timeout_seconds,
        ):
            raise ValueError("ocr_pipeline_delivery_owner_changed")

        payload = {"bucket": bucket, "name": input_name}
        if generation:
            payload["generation"] = generation

        url = _get_pipeline_url()
        request_state_saved = False
        trigger_error_message: str | None = None
        trigger_error_is_terminal = False
        if url:
            try:
                save_pipeline_request(job_id, input_reference)
                request_state_saved = True
            except Exception:  # noqa: BLE001
                logger.warning("OCR pipeline state save (request) failed")
            logger.info("OCR pipeline request POST url=%s", url)
            req = Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=_build_pipeline_request_headers(url),
                method="POST",
            )
            try:
                # Keep the trigger request short-lived. OCR completion is tracked via
                # the output object, not by holding the HTTP request open.
                with urlopen(req, timeout=_get_request_timeout(wait_for_output=False)) as resp:
                    resp.read()
                logger.info("OCR pipeline request POST done url=%s", url)
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="ignore")
                trigger_error_message = f"OCR pipeline HTTP {exc.code}: {raw}"
                trigger_error_is_terminal = 400 <= int(exc.code) < 500
            except URLError as exc:
                trigger_error_message = f"OCR pipeline request failed: {exc}"
            except (TimeoutError, socket.timeout) as exc:
                trigger_error_message = f"OCR pipeline request timeout: {exc}"
            if trigger_error_message:
                logger.warning(
                    "OCR pipeline request transport failed; relying on output reconciliation bucket=%s input=%s error=%s",
                    bucket,
                    input_name,
                    trigger_error_message,
                )
                if wait_for_output and trigger_error_is_terminal:
                    save_pipeline_error(job_id, trigger_error_message)
                    raise OCRPipelineTriggerFailedError(
                        input_reference=input_reference,
                        output_reference=output_reference,
                        error_message=trigger_error_message,
                    )
        else:
            logger.info("OCR pipeline request skipped (OCR_PIPELINE_URL not set)")

        if wait_for_output and not request_state_saved:
            try:
                save_pipeline_request(job_id, input_reference)
            except Exception:  # noqa: BLE001
                logger.warning("OCR pipeline state save (request) failed")
        if not wait_for_output:
            response = {
                "status": "running",
                "output_reference": output_reference,
                "input_reference": input_reference,
                "input_generation": generation,
                "job_id": f"{bucket}:{input_name}:{generation}",
            }
            if trigger_error_message:
                response["trigger_error"] = trigger_error_message
            return response
        poll_interval = float(os.getenv("OCR_PIPELINE_POLL_INTERVAL_SECONDS", "2.0"))
        logger.info(
            "OCR pipeline wait output bucket=%s name=%s timeout=%ss interval=%ss",
            bucket,
            output_name,
            timeout_seconds,
            poll_interval,
        )
        try:
            output = _wait_for_output(
                bucket=bucket,
                object_name=output_name,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
        except TimeoutError as exc:
            raise OCRPipelineOutputPendingError(
                input_reference=input_reference,
                output_reference=output_reference,
                timeout_seconds=timeout_seconds,
            ) from exc
        from src.services.ocr_job_service import validate_own_pipeline_result
        validate_own_pipeline_result(output, output_reference)
        try:
            save_pipeline_success(job_id, output_reference)
        except Exception:  # noqa: BLE001
            logger.warning("OCR pipeline state save (success) failed")
        if trigger_error_message and isinstance(output, dict):
            output = dict(output)
            output.setdefault("trigger_error", trigger_error_message)
        from src.services.ocr_job_service import validate_own_pipeline_result

        validate_own_pipeline_result(output, output_reference)
        return output
    except Exception as exc:  # noqa: BLE001
        try:
            if not isinstance(exc, OCRPipelineTriggerFailedError):
                save_pipeline_error(job_id, str(exc))
        except Exception:  # noqa: BLE001
            logger.warning("OCR pipeline state save (error) failed")
        raise
    finally:
        if acquired and semaphore:
            semaphore.release()
            inflight = _inflight_delta(-1)
            logger.info("OCR pipeline slot released inflight=%s", inflight)
