"""Staging dataset boundary. Vendored unchanged into each feature's package.

The browser selects an immutable snapshot identity, never a connection string.
Database credentials and authentication stay on the existing staging runtime.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

COOKIE = "sawa_stg_dataset"
HEADER = "X-Sawa-Dataset"
_selected = ContextVar("sawa_stg_dataset", default="test")
_IDENTITY = re.compile(r"replay-[a-z0-9][a-z0-9-]{0,47}")


class DatasetUnavailable(ValueError):
    pass


def staging_runtime():
    service = os.environ.get("K_SERVICE", "")
    return service.endswith("-stg") and os.environ.get("APP_ENV", "") not in {"prod", "production"}


def current_dataset():
    return _selected.get()


@contextmanager
def dataset_context(identity):
    if identity != "test":
        if not staging_runtime() or not _IDENTITY.fullmatch(identity):
            raise DatasetUnavailable("staging_dataset_invalid")
        if manifest()["id"] != identity:
            raise DatasetUnavailable("staging_dataset_snapshot_unavailable")
    token = _selected.set(identity)
    try:
        yield
    finally:
        _selected.reset(token)


def _staging_bucket(name):
    return re.search(r"(?:^|-)stg(?:-|$)", name) is not None


def _staging_uri(uri):
    parsed = urlsplit(uri)
    if (parsed.scheme != "gs" or not _staging_bucket(parsed.netloc) or parsed.query
            or parsed.fragment or not parsed.path.startswith("/stg-replay/")
            or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))):
        raise DatasetUnavailable("staging_dataset_storage_invalid")
    return parsed


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatasetUnavailable("staging_dataset_manifest_duplicate_key")
        result[key] = value
    return result


@lru_cache(maxsize=4)
def _read_manifest(uri, digest):
    parsed = _staging_uri(uri)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DatasetUnavailable("staging_dataset_manifest_digest_required")
    from google.cloud import storage
    content = storage.Client().bucket(parsed.netloc).blob(parsed.path.lstrip("/")).download_as_bytes()
    if sha256(content).hexdigest() != digest:
        raise DatasetUnavailable("staging_dataset_manifest_changed")
    value = json.loads(content, object_pairs_hook=_unique_object)
    if (value.get("schema") != 1 or not _IDENTITY.fullmatch(str(value.get("id", "")))
            or value.get("state") != "ready" or not value.get("snapshot_at")
            or set(value.get("components", {})) != {"order", "shift", "school-lunch"}):
        raise DatasetUnavailable("staging_dataset_manifest_incomplete")
    for name, component in value["components"].items():
        if (not re.fullmatch(r"sawa_replay_[a-z0-9_]{1,40}", str(component.get("database", "")))
                or not isinstance(component.get("settings"), dict)
                or not isinstance(component.get("objects"), dict)
                or component.get("verified") is not True
                or not isinstance(component.get("table_digests"), dict) or not component["table_digests"]
                or any(not re.fullmatch(r"[0-9a-f]{64}", str(digest)) for digest in component["table_digests"].values())):
            raise DatasetUnavailable("staging_dataset_component_incomplete:" + name)
        for item in component["objects"].values():
            physical = _staging_uri(item["uri"])
            if not physical.path.startswith("/stg-replay/" + value["id"] + "/"):
                raise DatasetUnavailable("staging_dataset_object_snapshot_mismatch")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                raise DatasetUnavailable("staging_dataset_object_digest_required")
            if not re.fullmatch(r"[1-9][0-9]*", str(item.get("generation", ""))):
                raise DatasetUnavailable("staging_dataset_object_generation_required")
    return value


def manifest():
    if not staging_runtime():
        raise DatasetUnavailable("staging_dataset_disabled")
    uri = os.environ.get("SAWA_STG_REPLAY_MANIFEST_URI", "")
    digest = os.environ.get("SAWA_STG_REPLAY_MANIFEST_SHA256", "")
    if not uri or not digest:
        raise DatasetUnavailable("staging_dataset_copy_not_ready")
    try:
        return _read_manifest(uri, digest)
    except DatasetUnavailable:
        raise
    except Exception as exc:
        raise DatasetUnavailable("staging_dataset_manifest_unavailable") from exc


def component(name):
    value = manifest()
    if current_dataset() != value["id"]:
        raise DatasetUnavailable("staging_dataset_snapshot_unavailable")
    return value["components"][name]


def database_uri(base, feature):
    if current_dataset() == "test":
        return base
    from sqlalchemy.engine import make_url
    url = make_url(base)
    host = url.query.get("host", url.host or "")
    if url.get_backend_name() != "postgresql" or not str(host).endswith("-stg"):
        raise DatasetUnavailable("staging_dataset_database_host_invalid")
    database = component(feature)["database"]
    if database == url.database:
        raise DatasetUnavailable("staging_dataset_database_not_isolated")
    return url.set(database=database).render_as_string(hide_password=False)


def data_setting(name, default=None, *, feature):
    if current_dataset() == "test":
        return os.getenv(name, default)
    settings = component(feature)["settings"]
    if name not in settings:
        raise DatasetUnavailable("staging_dataset_setting_required:" + name)
    value = settings[name]
    if not isinstance(value, str) or not value:
        raise DatasetUnavailable("staging_dataset_setting_invalid:" + name)
    return value


def storage_prefix():
    return "stg-replay/" + current_dataset() + "/"


def write_object(bucket, name):
    if current_dataset() == "test":
        return bucket, name
    if not _staging_bucket(bucket) or not name or name.startswith("/") or ".." in name.split("/"):
        raise DatasetUnavailable("staging_dataset_write_target_invalid")
    prefix = storage_prefix()
    if name.startswith("stg-replay/") and not name.startswith(prefix):
        raise DatasetUnavailable("staging_dataset_cross_snapshot_write")
    return bucket, name if name.startswith(prefix) else prefix + name


def read_object(uri, *, feature, generation=None):
    """Return physical copy and proof without rewriting saved business identity."""
    if current_dataset() == "test":
        return uri, generation, None
    entry = component(feature)
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query)
    pinned = (query.get("generation") or [None])[0]
    if generation is not None and pinned is not None and str(generation) != pinned:
        raise DatasetUnavailable("staging_dataset_generation_mismatch")
    original_generation = generation or (query.get("generation") or [None])[0]
    key = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if original_generation is not None:
        key += "?generation=" + str(original_generation)
    item = entry["objects"].get(key)
    if item is not None:
        _staging_uri(item["uri"])
        return item["uri"], item["generation"], item["sha256"]
    prefixes = (storage_prefix(), "input/" + storage_prefix(), "output/" + storage_prefix())
    if (parsed.scheme == "gs" and _staging_bucket(parsed.netloc)
            and unquote(parsed.path).lstrip("/").startswith(prefixes)):
        return uri, original_generation, None
    if parsed.scheme in {"", "file"}:
        path = Path(unquote(parsed.path) if parsed.scheme else uri)
        if path.is_absolute() and "/stg-replay/" + current_dataset() + "/" in str(path.resolve()):
            return uri, original_generation, None
    raise DatasetUnavailable("staging_dataset_original_copy_missing")


def verify_copy(content, digest):
    if digest is not None and sha256(content).hexdigest() != digest:
        raise DatasetUnavailable("staging_dataset_original_copy_changed")
    return content


def local_directory(base):
    path = Path(base)
    if current_dataset() != "test":
        path = path / "stg-replay" / current_dataset()
    path.mkdir(parents=True, exist_ok=True)
    return path


def delivery_headers():
    return {HEADER: current_dataset()} if current_dataset() != "test" else {}


def delivery_dataset_matches(headers, identity):
    values = [value for key, value in headers.items() if key.lower() == HEADER.lower()]
    return values == [identity] if identity is not None else not values


def delivery_namespace():
    return current_dataset() + "-" if current_dataset() != "test" else ""


class DatasetMiddleware:
    """Pure ASGI context includes background work and never mutates global env."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        request = Request(scope)
        explicit = request.headers.get(HEADER)
        cookie = request.cookies.get(COOKIE)
        identity = explicit if explicit is not None else cookie if cookie is not None else "test"
        # Authentication/session and selection endpoints always use the control DB.
        control = scope["path"].startswith(("/portal/", "/auth/", "/staging-datasets")) or scope["path"] in {"/health", "/healthz"}
        if not staging_runtime() and identity == "test":
            return await self.app(scope, receive, send)
        try:
            if (not control and request.method not in {"GET", "HEAD", "OPTIONS"}
                    and explicit is not None and cookie is not None and explicit != cookie):
                from starlette.responses import JSONResponse
                return await JSONResponse({"detail": "staging_dataset_changed"}, status_code=409)(scope, receive, send)
            started = False
            with dataset_context("test" if control else identity):
                async def selected_send(message):
                    nonlocal started
                    if message["type"] == "http.response.start":
                        started = True
                        message.setdefault("headers", []).extend([
                            (b"x-sawa-dataset", current_dataset().encode()),
                            (b"cache-control", b"no-store"),
                            (b"vary", b"Cookie, X-Sawa-Dataset"),
                        ])
                    await send(message)
                await self.app(scope, receive, selected_send)
        except DatasetUnavailable as exc:
            if started:
                raise
            from starlette.responses import JSONResponse
            await JSONResponse({"detail": str(exc)}, status_code=503)(scope, receive, send)
