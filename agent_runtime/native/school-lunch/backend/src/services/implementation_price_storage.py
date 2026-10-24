from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs, quote
from src import staging_data as datasets


class StorageConfigurationError(RuntimeError):
    pass


class StoredPdfNotFoundError(FileNotFoundError):
    pass


def parse_stored_reference(reference):
    if not isinstance(reference, str):
        raise StoredPdfNotFoundError("Stored source URI must be a string")
    parsed = urlparse(reference)
    if parsed.scheme == "file" and not parsed.netloc and not parsed.query and not parsed.fragment:
        path = Path(unquote(parsed.path, errors="strict"))
        if path.is_absolute() and path.as_uri() == reference:
            return {"mode": "local", "path": path}
    if parsed.scheme == "gs" and parsed.netloc and not parsed.fragment:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        generation = query.get("generation")
        object_name = unquote(parsed.path.lstrip("/"), errors="strict")
        if (set(query) == {"generation"} and isinstance(generation, list) and len(generation) == 1
            and generation[0].isdigit() and int(generation[0]) > 0 and object_name
            and str(int(generation[0])) == generation[0]
            and reference == f"gs://{parsed.netloc}/{quote(object_name, safe='/')}?generation={generation[0]}"):
            return {"mode": "gcs", "bucket": parsed.netloc, "object": object_name, "generation": int(generation[0])}
    raise StoredPdfNotFoundError("Original PDF URI is ambiguous or noncanonical")


def _sanitize_filename(filename: str) -> str:
    name = Path(str(filename or "").strip() or "upload.pdf").name
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


class LocalPdfStorage:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).expanduser()

    def save_pdf(self, *, job_id: str, filename: str, pdf_bytes: bytes) -> str:
        target = datasets.local_directory(self.root_dir, feature="school-lunch") / f"{job_id}-{_sanitize_filename(filename)}"
        with target.open("xb") as output:
            output.write(pdf_bytes)
        return target.resolve().as_uri()

    def load_pdf(self, reference: str) -> bytes:
        identity = parse_stored_reference(reference)
        if identity["mode"] != "local":
            raise StoredPdfNotFoundError("Local storage requires canonical file URI")
        target = identity["path"]
        if not target.exists():
            raise StoredPdfNotFoundError(f"stored pdf not found: {reference}")
        return target.read_bytes()


class GcsPdfStorage:
    def __init__(self, *, bucket_name: str, prefix: str):
        self.bucket_name = bucket_name
        self.prefix = prefix.strip().strip("/")
        self._client = None

    def _storage_client(self):
        if self._client is not None:
            return self._client
        try:
            from google.cloud import storage  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            raise StorageConfigurationError(
                "google-cloud-storage is required when IMPLEMENTATION_PRICE_OCR_GCS_BUCKET is configured"
            ) from exc
        self._client = storage.Client()
        return self._client

    def _object_name(self, *, job_id: str, filename: str) -> str:
        base = f"{job_id}/{_sanitize_filename(filename)}"
        return f"{self.prefix}/{base}" if self.prefix else base

    def save_pdf(self, *, job_id: str, filename: str, pdf_bytes: bytes) -> str:
        object_name = self._object_name(job_id=job_id, filename=filename)
        bucket_name, object_name = datasets.write_object(self.bucket_name, object_name, feature="school-lunch")
        bucket = self._storage_client().bucket(bucket_name)
        blob = bucket.blob(object_name)
        try:
            blob.upload_from_string(pdf_bytes, content_type="application/pdf", if_generation_match=0)
        except Exception as exc:  # noqa: BLE001
            raise StorageConfigurationError(
                f"GCS storage failed for bucket {self.bucket_name}: {exc}"
            ) from exc
        if blob.generation is None:
            raise StorageConfigurationError("GCS saved source generation is unavailable")
        return f"gs://{bucket_name}/{quote(object_name, safe='/')}?generation={int(blob.generation)}"

    def load_pdf(self, reference: str) -> bytes:
        identity = parse_stored_reference(reference)
        if identity["mode"] != "gcs":
            raise StoredPdfNotFoundError("GCS storage requires canonical generation URI")
        bucket_name, object_name = identity["bucket"], identity["object"]
        bucket = self._storage_client().bucket(bucket_name)
        generation = identity["generation"]
        blob = bucket.blob(object_name, generation=generation)
        try:
            return blob.download_as_bytes(if_generation_match=generation)
        except Exception as exc:  # noqa: BLE001
            raise StorageConfigurationError(
                f"GCS storage read failed for bucket {bucket_name}: {exc}"
            ) from exc


def get_write_storage():
    bucket_name = str(os.getenv("IMPLEMENTATION_PRICE_OCR_GCS_BUCKET", "") or "").strip()
    if bucket_name:
        prefix = str(os.getenv("IMPLEMENTATION_PRICE_OCR_GCS_PREFIX", "implementation-price-ocr-jobs") or "")
        return GcsPdfStorage(bucket_name=bucket_name, prefix=prefix)
    local_dir = str(
        os.getenv(
            "IMPLEMENTATION_PRICE_OCR_STORAGE_DIR",
            "/tmp/hospital_order_outputs/implementation_price_ocr_jobs",
        )
    )
    return LocalPdfStorage(root_dir=local_dir)


def load_pdf(reference: str) -> bytes:
    if datasets.current_dataset(feature="school-lunch") != "test":
        uri, generation, digest = datasets.read_object(reference, feature="school-lunch")
        parsed = urlparse(uri)
        if parsed.scheme != "gs":
            raise StoredPdfNotFoundError("Staging replay source must be a copied GCS object")
        from google.cloud import storage
        blob = storage.Client().bucket(parsed.netloc).blob(unquote(parsed.path.lstrip("/")), generation=int(generation) if generation else None)
        return datasets.verify_copy(blob.download_as_bytes(if_generation_match=int(generation) if generation else None), digest)
    parsed = urlparse(reference)
    if parsed.scheme == "gs":
        return GcsPdfStorage(bucket_name=parsed.netloc, prefix="").load_pdf(reference)
    if parsed.scheme == "file":
        return LocalPdfStorage(root_dir="/").load_pdf(reference)
    raise StoredPdfNotFoundError(f"unsupported stored pdf reference: {reference}")


def load_original(source):
    if storage_identity(source["pdf_reference"]) != source["storage_version"]:
        raise StoredPdfNotFoundError("Original PDF storage version changed")
    content = load_pdf(source["pdf_reference"])
    if sha256(content).hexdigest() != source["pdf_sha256"]:
        raise StoredPdfNotFoundError("Original PDF digest changed")
    return content


def load_job_source(job):
    if job.get("source_contract_error") is not None or not isinstance(job.get("source_contract"), dict):
        raise StoredPdfNotFoundError("Original saved OCR source proof is unavailable")
    return load_original(job["source_contract"])


def storage_identity(reference):
    identity = parse_stored_reference(reference)
    if identity["mode"] == "local":
        if datasets.current_dataset(feature="school-lunch") != "test":
            entry = datasets.component("school-lunch")["objects"].get(reference)
            proof = entry.get("source_identity") if isinstance(entry, dict) else None
            required = {"mode", "device", "inode", "size", "mtime_ns"}
            if not isinstance(proof, dict) or set(proof) != required or proof["mode"] != "local":
                raise StoredPdfNotFoundError("Original local storage proof is unavailable in this snapshot")
            return dict(proof)
        stat = identity["path"].stat()
        return {"mode": "local", "device": stat.st_dev, "inode": stat.st_ino,
                "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return identity
