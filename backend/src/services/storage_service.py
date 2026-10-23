from datetime import datetime, timedelta, timezone
from functools import lru_cache
import os
from pathlib import Path
import threading
from typing import Union
from urllib.parse import urlparse, parse_qs
import json
import re
from src import staging_data as datasets
from src.services.storage_uri import format_gcs_uri, parse_gcs_uri, signing_query, validate_generation, image_references, replace_image_references

StoragePath = Union[str, Path]
_GCS_CLIENT_LOCAL = threading.local()
_ENV_REF_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class StorageService:
    def __init__(self, base_path: StoragePath):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, relative_path: str, data: bytes) -> str:
        target = datasets.local_directory(self.base_path) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    def open(self, relative_path: str) -> bytes:
        target = datasets.local_directory(self.base_path) / relative_path
        return target.read_bytes()


def load_bytes_from_uri(uri: str, *, generation: str | None = None, verification: bool = False) -> bytes:
    def _replace_env_ref(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.getenv(name)
        if not value:
            raise ValueError(f"storage_uri_env_required:{name}")
        return value

    # Production copies bind authored variable references to immutable copied objects.
    # Expanding them with TEST's environment would select a different source bucket.
    production_copy_reference = False
    if _ENV_REF_PATTERN.search(uri) and datasets.current_dataset() != 'test':
        selected = datasets.manifest()
        provenance = selected.get('_component_runtime_provenance', {}).get('order', selected.get('runtime_settings_provenance'))
        if provenance not in {'production_environment', 'legacy_stg_runtime'}:
            raise datasets.DatasetUnavailable('staging_dataset_runtime_settings_provenance_required')
        production_copy_reference = provenance == 'production_environment'
    if not production_copy_reference:
        uri = _ENV_REF_PATTERN.sub(_replace_env_ref, uri)
    uri, generation, copy_digest = datasets.read_object(uri, feature="order", generation=generation)
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        if generation is not None:
            raise ValueError("storage_generation_requires_gcs")
        path = Path(parsed.path if parsed.scheme else uri)
        return datasets.verify_copy(path.read_bytes(), copy_digest)
    if parsed.scheme == "gs":
        source = parse_gcs_uri(uri)
        if generation is not None:
            generation = validate_generation(generation)
            if source.generation is not None and source.generation != generation:
                raise ValueError("storage_generation_mismatch")
        else:
            generation = source.generation
        client = _get_thread_gcs_client()
        options = {"timeout": 30, "retry": None} if verification else {}
        if generation is not None:
            blob = client.bucket(source.bucket).blob(source.object_name, generation=int(generation))
            return datasets.verify_copy(blob.download_as_bytes(if_generation_match=int(generation), **options), copy_digest)
        return datasets.verify_copy(client.bucket(source.bucket).blob(source.object_name).download_as_bytes(**options), copy_digest)
    raise ValueError(f"unsupported storage uri: {uri}")


def verify_selected_pdf_source(receipt):
    try:
        _verify_selected_pdf_source(receipt)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("selected_pdf_verification_failed") from exc


def _verify_selected_pdf_source(receipt):
    """URI correction for the pending H2 receipt reader; retain its source fence."""
    import hashlib

    if not isinstance(receipt, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or "")):
        raise ValueError("selected_pdf_digest_required")
    if receipt.get("parent") is not None:
        verify_selected_pdf_source(receipt["parent"])
    uri = receipt["storage_uri"]
    if datasets.current_dataset() != "test":
        data = load_bytes_from_uri(uri, generation=receipt.get("generation"), verification=True)
        if hashlib.sha256(data).hexdigest() != receipt["sha256"]:
            raise ValueError("selected_pdf_bytes_changed")
        return
    parsed = urlparse(uri)
    if parsed.scheme == "gs":
        source = parse_gcs_uri(uri)
        generation = receipt.get("generation")
        if generation is None:
            if source.generation is not None:
                raise ValueError("selected_pdf_generation_mismatch")
            if hashlib.sha256(load_bytes_from_uri(uri, verification=True)).hexdigest() != receipt["sha256"]:
                raise ValueError("selected_pdf_bytes_changed")
            return
        generation = validate_generation(generation)
        if source.generation != generation:
            raise ValueError("selected_pdf_generation_mismatch")
        blob = _get_thread_gcs_client().bucket(source.bucket).blob(source.object_name)
        blob.reload(timeout=30, retry=None)
        if str(blob.generation) != generation:
            raise ValueError("selected_pdf_generation_changed")
    elif parsed.scheme in {"", "file"}:
        if hashlib.sha256(Path(parsed.path if parsed.scheme else uri).read_bytes()).hexdigest() != receipt["sha256"]:
            raise ValueError("selected_pdf_bytes_changed")
    else:
        raise ValueError("selected_pdf_source_unsupported")


class SelectedPdfBytes(bytes):
    def __new__(cls, value, receipt):
        instance = super().__new__(cls, value)
        instance.selected_pdf_receipt = dict(receipt)
        return instance

    def __reduce__(self):
        return type(self), (bytes(self), self.selected_pdf_receipt)


def verify_selected_pdf_bytes(value, receipt=None):
    import hashlib

    receipt = receipt if receipt is not None else getattr(value, "selected_pdf_receipt", None)
    if receipt is None:
        from src.services.workflow_attempt_guard_service import processing_start_pdf_receipt

        receipt = processing_start_pdf_receipt()
    if not isinstance(receipt, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or "")):
        raise ValueError("selected_pdf_digest_required")
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution, current_workflow_execution, processing_start_pdf_receipt
    owner = current_nonworkflow_execution()
    workflow = current_workflow_execution()
    if owner is not None or (workflow is not None and workflow.get("attempt") is not None):
        expected = processing_start_pdf_receipt()
        original = receipt
        while isinstance(original.get("parent"), dict):
            original = original["parent"]
        if original != expected:
            raise ValueError("selected_pdf_input_mismatch")
    verify_selected_pdf_source(receipt)
    if hashlib.sha256(value).hexdigest() != receipt["sha256"]:
        raise ValueError("selected_pdf_bytes_changed")
    return value


def verify_recognizer_pdf_bytes(value):
    """Independent diagnostics cannot adopt; owned recognition requires proof."""
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution, current_workflow_execution
    if getattr(value, "selected_pdf_receipt", None) is not None or current_nonworkflow_execution() is not None or current_workflow_execution() is not None:
        verify_selected_pdf_bytes(value)
    return value


def load_selected_pdf_bytes(uri, receipt=None):
    import hashlib
    if receipt is None:
        from src.services.workflow_attempt_guard_service import processing_start_pdf_receipt

        receipt = processing_start_pdf_receipt()
    if not isinstance(receipt, dict) or uri != receipt.get("storage_uri"):
        raise ValueError("selected_pdf_input_mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or "")):
        raise ValueError("selected_pdf_digest_required")
    if urlparse(uri).scheme == "gs" and parse_qs(urlparse(uri).query).get("generation") and receipt.get("generation") is None:
        raise ValueError("selected_pdf_generation_required")
    if isinstance(receipt.get("parent"), dict):
        verify_selected_pdf_source(receipt["parent"])
    if urlparse(uri).scheme == "gs" and receipt.get("generation") is not None:
        verify_selected_pdf_source(receipt)
    try:
        data = load_bytes_from_uri(uri, generation=receipt.get("generation"), verification=True)
    except Exception as exc:
        raise ValueError("selected_pdf_verification_failed") from exc
    if hashlib.sha256(data).hexdigest() != receipt["sha256"]:
        raise ValueError("selected_pdf_bytes_changed")
    if urlparse(uri).scheme == "gs" and receipt.get("generation") is not None:
        verify_selected_pdf_source(receipt)
    return SelectedPdfBytes(data, receipt)


@lru_cache(maxsize=1)
def _import_google_cloud_storage():
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValueError("google-cloud-storage is not installed") from exc
    return storage


def _get_thread_gcs_client():
    client = getattr(_GCS_CLIENT_LOCAL, "client", None)
    if client is not None:
        return client
    storage = _import_google_cloud_storage()
    client = storage.Client()
    _GCS_CLIENT_LOCAL.client = client
    return client


def _thread_local_signing_service_account_email(credentials) -> str | None:
    service_account_email = getattr(_GCS_CLIENT_LOCAL, "signing_service_account_email", None)
    if service_account_email:
        return service_account_email
    service_account_email = getattr(credentials, "service_account_email", None)
    if not service_account_email:
        service_account_email = (
            os.getenv("GCP_SERVICE_ACCOUNT_EMAIL")
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
        )
    _GCS_CLIENT_LOCAL.signing_service_account_email = service_account_email
    return service_account_email


def _credentials_need_refresh(credentials) -> bool:
    if credentials is None:
        return True
    token = getattr(credentials, "token", None)
    if not token:
        return True
    if not getattr(credentials, "valid", False):
        return True
    expiry = getattr(credentials, "expiry", None)
    if expiry is None:
        return False
    try:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= (datetime.now(timezone.utc) + timedelta(minutes=5))
    except Exception:
        return False


def _get_thread_gcs_signing_credentials():
    credentials = getattr(_GCS_CLIENT_LOCAL, "signing_credentials", None)
    if credentials is None:
        import google.auth

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        _GCS_CLIENT_LOCAL.signing_credentials = credentials
    if _credentials_need_refresh(credentials):
        from google.auth.transport.requests import Request as AuthRequest

        credentials.refresh(AuthRequest())
    service_account_email = _thread_local_signing_service_account_email(credentials)
    if not service_account_email or not getattr(credentials, "token", None):
        raise ValueError("missing service account email or access token for signed URL")
    return credentials, service_account_email


def save_bytes_to_gcs(
    bucket: str,
    object_path: str,
    data: bytes,
    content_type: str | None = None,
    *,
    client=None,
    pin_generation: bool = False,
) -> str:
    if not bucket or not object_path:
        raise ValueError("bucket and object_path are required")
    bucket, object_path = datasets.write_object(bucket, object_path)
    format_gcs_uri(bucket, object_path)
    client = client or _get_thread_gcs_client()
    blob = client.bucket(bucket).blob(object_path)
    blob.upload_from_string(data, content_type=content_type)
    generation = validate_generation(blob.generation) if pin_generation else None
    return format_gcs_uri(bucket, object_path, generation)


def generate_signed_url(bucket: str, object_path: str, expires_in_seconds: int = 3600, *, generation: str | None = None, query_parameters: dict[str, str] | None = None) -> str:
    if datasets.current_dataset() != "test":
        uri, generation, _digest = datasets.read_object(format_gcs_uri(bucket, object_path, generation), feature="order", generation=generation)
        physical = parse_gcs_uri(uri)
        bucket, object_path = physical.bucket, physical.object_name
    if generation is not None:
        generation = validate_generation(generation)
    format_gcs_uri(bucket, object_path, generation)
    try:
        _import_google_cloud_storage()
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValueError("google-cloud-storage is not installed") from exc
    client = _get_thread_gcs_client()
    blob = client.bucket(bucket).blob(object_path)
    credentials, service_account_email = _get_thread_gcs_signing_credentials()
    return blob.generate_signed_url(
        generation=generation,
        query_parameters=query_parameters,
        version="v4",
        expiration=timedelta(seconds=expires_in_seconds),
        method="GET",
        service_account_email=service_account_email,
        access_token=credentials.token,
    )


def generate_signed_url_from_uri(uri: str, expires_in_seconds: int = 3600) -> str:
    source = parse_gcs_uri(uri)
    url = generate_signed_url(source.bucket, source.object_name, expires_in_seconds,
                              generation=source.generation, query_parameters=signing_query(source))
    return url + ("#" + source.fragment if source.fragment else "")


def sign_published_figure_references(markdown: str, signed_assets: dict[str, str]) -> str:
    replacements = {}
    for ref in image_references(markdown):
        if urlparse(ref).scheme != "gs":
            continue
        parse_gcs_uri(ref)
        signed = signed_assets.get(ref)
        if not isinstance(signed, str) or urlparse(signed).scheme != "https":
            raise ValueError("published_figure_signed_receipt_missing")
        replacements[ref] = signed
    return replace_image_references(markdown, replacements)


def _sanitize_job_id(job_id: str) -> str:
    return (
        job_id.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )


def build_artifact_path(job_id: str, name: str) -> str:
    safe_job_id = _sanitize_job_id(job_id)
    return f"artifacts/{safe_job_id}/{name}"


def build_output_path(job_id: str, name: str) -> str:
    safe_job_id = _sanitize_job_id(job_id)
    return f"output/{safe_job_id}/{name}"


def save_artifact_json(storage: StorageService, job_id: str, name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return storage.save_bytes(build_artifact_path(job_id, name), data)


def save_artifact_bytes(storage: StorageService, job_id: str, name: str, data: bytes) -> str:
    return storage.save_bytes(build_artifact_path(job_id, name), data)


def save_output_json(storage: StorageService, job_id: str, name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return storage.save_bytes(build_output_path(job_id, name), data)


def get_default_output_bucket() -> str | None:
    return (os.getenv("OCR_OUTPUT_BUCKET") or "").strip() or None


def save_canonical_artifact_bytes(job_id: str, name: str, data: bytes, content_type: str) -> str:
    local_dir = (os.getenv("OCR_ARTIFACT_DIR") or "").strip()
    bucket = (os.getenv("OCR_OUTPUT_BUCKET") or "").strip()
    if local_dir and bucket:
        raise ValueError("canonical_artifact_storage_ambiguous")
    if not local_dir and not bucket:
        raise ValueError("canonical_artifact_storage_required")
    object_path = build_artifact_path(job_id, name)
    if local_dir:
        root = Path(local_dir)
        if not root.is_absolute():
            raise ValueError("canonical_artifact_directory_must_be_absolute")
        return StorageService(root).save_bytes(object_path, data)
    return save_bytes_to_gcs(bucket, object_path, data, content_type=content_type, pin_generation=True)


def save_artifact_bytes_to_gcs(
    bucket: str, job_id: str, name: str, data: bytes, content_type: str | None = None
) -> str:
    object_path = build_artifact_path(job_id, name)
    return save_bytes_to_gcs(bucket, object_path, data, content_type=content_type)


def save_output_bytes_to_gcs(
    bucket: str, job_id: str, name: str, data: bytes, content_type: str | None = None
) -> str:
    object_path = build_output_path(job_id, name)
    return save_bytes_to_gcs(bucket, object_path, data, content_type=content_type)
