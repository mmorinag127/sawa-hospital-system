"""Staging dataset boundary. Vendored unchanged into each feature's package.

The browser selects an immutable snapshot identity, never a connection string.
Database credentials and authentication stay on the existing staging runtime.
"""
from contextlib import contextmanager
from collections.abc import Mapping
from contextvars import ContextVar
from functools import lru_cache
from hashlib import sha256
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

COOKIE = "sawa_stg_dataset"
HEADER = "X-Sawa-Dataset"
_selected = ContextVar("sawa_stg_dataset", default="test")
_pinned_manifest = ContextVar("sawa_stg_manifest", default=None)
_IDENTITY = re.compile(r"replay-[a-z0-9][a-z0-9-]{0,47}")



# Only non-secret inputs that affect business results; storage/auth/transport stay stg.
RUNTIME_BACKEND_KEYS = (
    'DAILY_OUTPUT_AUDIT_GEMINI_MAX_TOKENS',
    'DAILY_OUTPUT_AUDIT_GEMINI_MODEL',
    'DAILY_OUTPUT_AUDIT_GEMINI_RETRY_MAX_TOKENS',
    'DAILY_OUTPUT_AUDIT_GEMINI_TIMEOUT_SECONDS',
    'DB_EXPORT_MAX_ROWS_PER_TABLE',
    'DB_QUOTA_ALERT_RATIO',
    'DB_QUOTA_CRITICAL_RATIO',
    'DB_QUOTA_LIMIT_BYTES',
    'DB_QUOTA_LIMIT_ROWS',
    'GEMINI_API_BASE',
    'GEMINI_OCR_FULL_TABLE_TIMEOUT_SECONDS',
    'GEMINI_OCR_MAX_TOKENS',
    'GEMINI_OCR_MODEL',
    'GEMINI_OCR_RETRY_MAX_TOKENS',
    'GEMINI_OCR_RETRY_ON_TRUNCATION',
    'GEMINI_OCR_THINKING_BUDGET',
    'GEMINI_OCR_TIMEOUT_SECONDS',
    'INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS',
    'INGEST_JOB_AUTO_RECOVERY_WINDOW_HOURS',
    'INGEST_JOB_STALE_MINUTES',
    'MANUAL_UPLOAD_MAX_FILES',
    'MANUAL_UPLOAD_MAX_MB',
    'MENU_UPLOAD_MAX_MB',
    'OCR_AUTO_LLM_REPARSE_ON_INGEST',
    'OCR_AUTO_LLM_REPARSE_PROVIDER',
    'OCR_BACKLOG_RECENT_HOURS',
    'OCR_ENABLE_ROI_OVERLAY_MERGE',
    'OCR_JOB_AUTO_RECOVERY_INTERVAL_SECONDS',
    'OCR_JOB_AUTO_RECOVERY_MAX_ATTEMPTS',
    'OCR_JOB_STALE_MINUTES',
    'OCR_LLM_REVIEW_OVERWRITE_MIN_CONFIDENCE',
    'OCR_MAIN_PROVIDER',
    'OCR_PIPELINE_ACQUIRE_TIMEOUT_SECONDS',
    'OCR_PIPELINE_MAX_INFLIGHT',
    'OCR_PIPELINE_OUTPUT_READ_TIMEOUT_SECONDS',
    'OCR_PIPELINE_POLL_INTERVAL_SECONDS',
    'OCR_PIPELINE_TIMEOUT_SECONDS',
    'OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST',
    'OCR_QUANTITY_ASSIGNMENT_STRATEGY',
    'OCR_REPARSE_AUDIT_PROVIDER',
    'OCR_REPARSE_COLUMN_MIRROR_MIN_DISTINCT_PAIRS',
    'OCR_REPARSE_COLUMN_MIRROR_MIN_EQUAL_RATIO',
    'OCR_REPARSE_COLUMN_MIRROR_MIN_NONZERO_OVERLAP',
    'OCR_REPARSE_COLUMN_MIRROR_MIN_OVERLAP',
    'OCR_REPARSE_COLUMN_NONEMPTY_MAX_RATIO',
    'OCR_REPARSE_COLUMN_NONEMPTY_MIN_RATIO',
    'OCR_REPARSE_COLUMN_RATIO_MIN_REFERENCE_COUNT',
    'OCR_REPARSE_COLUMN_UNEXPECTED_NONEMPTY_ABS',
    'OCR_REPARSE_COLUMN_UNEXPECTED_NONEMPTY_RATIO',
    'OCR_REPARSE_COST_ENFORCE_HARD_LIMIT',
    'OCR_REPARSE_COST_GEMINI_FLASH_INPUT_USD_PER_1M',
    'OCR_REPARSE_COST_GEMINI_FLASH_OUTPUT_USD_PER_1M',
    'OCR_REPARSE_COST_GEMINI_PRO_INPUT_USD_PER_1M',
    'OCR_REPARSE_COST_GEMINI_PRO_OUTPUT_USD_PER_1M',
    'OCR_REPARSE_COST_HARD_LIMIT_USD',
    'OCR_REPARSE_COST_INPUT_USD_PER_1M',
    'OCR_REPARSE_COST_OPENAI_INPUT_USD_PER_1M',
    'OCR_REPARSE_COST_OPENAI_OUTPUT_USD_PER_1M',
    'OCR_REPARSE_COST_OUTPUT_USD_PER_1M',
    'OCR_REPARSE_COST_SOFT_LIMIT_USD',
    'OCR_REPARSE_ENABLE_COLUMN_ANOMALY_GATE',
    'OCR_REPARSE_ENABLE_COLUMN_MIRROR_GATE',
    'OCR_REPARSE_ENABLE_COLUMN_UNEXPECTED_NONEMPTY_GATE',
    'OCR_REPARSE_ENABLE_GEMINI_PRO_ON_QUALITY_FAIL',
    'OCR_REPARSE_ENABLE_LLM_AUDIT_GATE',
    'OCR_REPARSE_ENABLE_PIPELINE_QUANTITY_MERGE',
    'OCR_REPARSE_ENABLE_REPAIR_PASS',
    'OCR_REPARSE_ENABLE_SOFT_WARNINGS',
    'OCR_REPARSE_GEMINI_AUDIT_MODEL',
    'OCR_REPARSE_GEMINI_REPAIR_MODEL',
    'OCR_REPARSE_HEARTBEAT_SECONDS',
    'OCR_REPARSE_INFERENCE_TIMEOUT_SECONDS',
    'OCR_REPARSE_LINE_COUNT_GUARD_MIN_BEFORE',
    'OCR_REPARSE_LINE_COUNT_MAX_DROP_ABS',
    'OCR_REPARSE_LINE_COUNT_MIN_RATIO',
    'OCR_REPARSE_LLM_AUDIT_BLOCKING_CODES',
    'OCR_REPARSE_LLM_AUDIT_BLOCKING_SEVERITY',
    'OCR_REPARSE_LLM_AUDIT_FAIL_CLOSED',
    'OCR_REPARSE_LLM_AUDIT_MIN_CONFIDENCE',
    'OCR_REPARSE_LLM_AUDIT_TIMEOUT_SECONDS',
    'OCR_REPARSE_LLM_FIRST_PASS_WAIT_SECONDS',
    'OCR_REPARSE_MAX_EXTRA_ROWS',
    'OCR_REPARSE_MAX_MISSING_TAIL_ROWS',
    'OCR_REPARSE_MAX_QTY',
    'OCR_REPARSE_OCR_PIPELINE_TIMEOUT_SECONDS',
    'OCR_REPARSE_OPENAI_AUDIT_MODEL',
    'OCR_REPARSE_OUTPUT_POLL_SECONDS',
    'OCR_REPARSE_OUTPUT_RECOVERY_GRACE_SECONDS',
    'OCR_REPARSE_OUTPUT_WAIT_SECONDS',
    'OCR_REPARSE_PARTIAL_ANCHOR_MAX_ROWS',
    'OCR_REPARSE_PARTIAL_ANCHOR_MIN_GAP_ROWS',
    'OCR_REPARSE_QUALITY_LOOKBACK_HOURS',
    'OCR_REPARSE_QUALITY_MAX_EMPTY_RATE',
    'OCR_REPARSE_QUALITY_MAX_TRUNCATED_RATE',
    'OCR_REPARSE_QUALITY_MAX_VALIDATION_FAILURE_RATE',
    'OCR_REPARSE_QUALITY_MIN_SAMPLES',
    'OCR_REPARSE_QUALITY_MIN_SUCCESS_RATE',
    'OCR_REPARSE_QUALITY_SAMPLE_LIMIT',
    'OCR_REPARSE_REQUIRE_MANUAL_REVIEW_FOR_STRUCTURAL_PROJECTION',
    'OCR_REPARSE_ROW_COVERAGE_MIN_RATIO',
    'OCR_REPARSE_SOFT_WARNING_CODES',
    'OCR_REPARSE_SOURCE_ROW_SPAN_MIN_DENSITY',
    'OCR_REPARSE_STAGE_TIMEOUT_SECONDS',
    'OCR_REPARSE_STRUCTURAL_PROJECTION_REVIEW_MIN_CELLS',
    'OCR_REPARSE_STRUCTURAL_PROJECTION_REVIEW_MIN_ROWS',
    'OCR_REPARSE_WEAK_ANCHOR_MAX_DATES',
    'OCR_REPARSE_WEAK_ANCHOR_MIN_GAP_ROWS',
    'OCR_REPARSE_WEAK_ANCHOR_OBSERVED_DELTA_ROWS',
    'OCR_SHEET_MAX_QTY',
    'OCR_TEMPLATE_MIN_CONFIDENCE',
    'OPENAI_API_BASE',
    'OPENAI_OCR_FULL_TABLE_TIMEOUT_SECONDS',
    'OPENAI_OCR_MAX_TOKENS',
    'OPENAI_OCR_MODEL',
    'OPENAI_OCR_RETRY_MAX_TOKENS',
    'OPENAI_OCR_RETRY_ON_TRUNCATION',
    'OPENAI_OCR_TIMEOUT_SECONDS',
    'SHIPPING_DB_QUOTA_LIMIT_BYTES',
    'SHIPPING_LOG_QUOTA_LIMIT_ROWS',
    'SHIPPING_QUOTA_ALERT_RATIO',
    'SHIPPING_QUOTA_CRITICAL_RATIO',
    'SHIPPING_REFRESH_SCHEDULE_MINUTE',
    'SHIPPING_TEMPLATE_PATH',
    'WORKFLOW_V2_AI_FAX_DPI',
    'WORKFLOW_V2_AI_FAX_MAX_PIXELS',
    'WORKFLOW_V2_AUTO_EDIT_MAX_WORKERS',
    'WORKFLOW_V2_AUTO_EDIT_TARGET_CHUNK_SIZE',
    'WORKFLOW_V2_LLM_ANOMALY_ENABLED',
    'WORKFLOW_V2_LLM_MODEL',
    'WORKFLOW_V2_LLM_TIMEOUT_SECONDS',
    'WORKFLOW_V2_OCR_MAX_CONCURRENT',
    'WORKFLOW_V2_OCR_SLOT_POLL_SECONDS',
    'WORKFLOW_V2_OCR_SLOT_TIMEOUT_SECONDS',
)
RUNTIME_PRODUCER_KEYS = (
    'OCR_ENABLE_PAGE_CORRECTION',
    'OCR_ENABLE_PAGE_CORRECTION_DESKEW',
    'OCR_ENABLE_PAGE_CORRECTION_PERSPECTIVE',
    'OCR_ENABLE_PAGE_CORRECTION_POSITION_NORMALIZE',
    'OCR_ENABLE_PAGE_CORRECTION_TEMPLATE_WARP',
    'OCR_ENABLE_QUANTITY_SUBGRID_SECOND_PASS',
    'OCR_ENABLE_TEMPLATE_MATCH',
    'OCR_ENABLE_TEMPLATE_ROI',
    'OCR_PAGE_CORRECTION_MAX_SKEW_DEG',
    'OCR_PAGE_CORRECTION_MIN_ABS_SKEW_DEG',
    'OCR_PAGE_CORRECTION_MIN_PERSPECTIVE_RATIO_DELTA',
    'OCR_PAGE_CORRECTION_MIN_SKEW_LINES',
    'OCR_PIPELINE_MODE',
    'OCR_QTY_OCR_ENGINE',
    'OCR_QUANTITY_SUBGRID_MAX_PASSES',
    'OCR_TEMPLATE_MATCH_DPI',
    'OCR_TEMPLATE_MIN_CONFIDENCE',
    'OCR_TEMPLATE_ROI_TEXT_OCR_ENGINE',
    'OCR_YOMITOKU_DEVICE',
    'OCR_YOMITOKU_DPI',
    'OCR_YOMITOKU_FIGURE_WIDTH',
    'OCR_YOMITOKU_IGNORE_LINE_BREAK',
    'OCR_YOMITOKU_NO_FIGURE',
    'OCR_YOMITOKU_VIS',
    'OCR_YOMITOKU_VIS_PDF',
)
RUNTIME_SETTING_KEYS = {"order": frozenset(RUNTIME_BACKEND_KEYS), "shift": frozenset(), "school-lunch": frozenset(('GEMINI_API_BASE', 'IMPLEMENTATION_PRICE_OCR_DEVICE', 'IMPLEMENTATION_PRICE_OCR_DPI', 'PROCESS_GEMINI_MODEL'))}

def runtime_setting_allowed(name, feature):
    return name in RUNTIME_SETTING_KEYS[feature]


class DatasetUnavailable(ValueError):
    pass


def staging_runtime():
    service = os.environ.get("K_SERVICE", "")
    return service.endswith("-stg") and os.environ.get("APP_ENV", "") not in {"prod", "production"}


def current_dataset(*, feature=None):
    identity = _selected.get()
    if feature is not None and identity != "test":
        retained = manifest().get("retained_components", {}).get(feature)
        if retained is not None:
            return retained["id"]
    return identity


@contextmanager
def dataset_context(identity):
    if identity != "test":
        if not staging_runtime() or not _IDENTITY.fullmatch(identity):
            raise DatasetUnavailable("staging_dataset_invalid")
        selected_manifest = manifest_for(identity)
    else:
        selected_manifest = None
    token = _selected.set(identity)
    pinned = _pinned_manifest.set(selected_manifest)
    try:
        yield
    finally:
        _pinned_manifest.reset(pinned)
        _selected.reset(token)


def _staging_bucket(name):
    return re.search(r"(?:^|-)stg(?:-|$)", name) is not None


def local_source_reference(reference):
    if not isinstance(reference, str):
        return False
    parsed = urlsplit(reference)
    if reference.startswith("/"):
        path = reference
    elif parsed.scheme == "file" and not parsed.netloc and not parsed.query and not parsed.fragment:
        path = unquote(parsed.path)
    else:
        return False
    return path.startswith("/") and not any(part in {".", ".."} for part in path.split("/"))


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


def retained_snapshot(entry):
    required = {"id", "uri", "sha256", "snapshot_at"}
    if (not isinstance(entry, dict) or not required <= set(entry)
            or set(entry) - required - {"runtime_settings_provenance"}
            or not _IDENTITY.fullmatch(str(entry.get("id", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
            or not entry.get("snapshot_at")
            or ("runtime_settings_provenance" in entry and entry["runtime_settings_provenance"] != "legacy_stg_runtime")):
        raise DatasetUnavailable("staging_dataset_retained_snapshot_invalid")
    if not _staging_uri(entry["uri"]).path.startswith("/stg-replay/" + entry["id"] + "/"):
        raise DatasetUnavailable("staging_dataset_retained_snapshot_invalid")
    return entry


class _ManifestObjects(Mapping):
    """Immutable, file-backed object index owned by one cached manifest."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="sawa-manifest-")
        self.path = Path(self.directory.name) / "objects.sqlite"
        with self._connection() as connection:
            connection.execute("CREATE TABLE objects (reference TEXT PRIMARY KEY, proof TEXT NOT NULL)")

    def _connection(self):
        from contextlib import closing
        return closing(sqlite3.connect(self.path))

    def __getitem__(self, key):
        with self._connection() as connection:
            row = connection.execute("SELECT proof FROM objects WHERE reference=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def __iter__(self):
        with self._connection() as connection:
            for row in connection.execute("SELECT reference FROM objects ORDER BY reference"):
                yield row[0]

    def __len__(self):
        with self._connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0]

    def items(self):
        with self._connection() as connection:
            for reference, proof in connection.execute("SELECT reference, proof FROM objects ORDER BY reference"):
                yield reference, json.loads(proof)

    def values(self):
        for _, proof in self.items():
            yield proof

    def __eq__(self, other):
        if not isinstance(other, Mapping) or len(self) != len(other):
            return False
        if isinstance(other, _ManifestObjects):
            return all(left == right for left, right in zip_longest(self.items(), other.items()))
        return all(key in other and other[key] == proof for key, proof in self.items())

    def __deepcopy__(self, memo):
        # Index files are immutable after parsing and owned for the object's lifetime.
        return self


def _parse_manifest(source):
    import ijson
    events = iter(ijson.basic_parse(source))
    indexes = []

    def read(event, value, path):
        if event == "start_map":
            indexed = len(path) == 3 and path[0] == "components" and path[2] == "objects"
            result = _ManifestObjects() if indexed else {}
            if indexed:
                indexes.append(result)
                connection = sqlite3.connect(result.path)
            try:
                while True:
                    kind, key = next(events)
                    if kind == "end_map":
                        break
                    if kind != "map_key":
                        raise ValueError("Expected manifest object key")
                    if not indexed and key in result:
                        raise DatasetUnavailable("staging_dataset_manifest_duplicate_key")
                    item = read(*next(events), (*path, key))
                    if indexed:
                        try:
                            connection.execute("INSERT INTO objects VALUES (?,?)", (key, json.dumps(item, ensure_ascii=False)))
                        except sqlite3.IntegrityError as exc:
                            raise DatasetUnavailable("staging_dataset_manifest_duplicate_key") from exc
                    else:
                        result[key] = item
                if indexed:
                    connection.commit()
                return result
            finally:
                if indexed:
                    connection.close()
        if event == "start_array":
            result = []
            while True:
                kind, item = next(events)
                if kind == "end_array":
                    return result
                result.append(read(kind, item, (*path, len(result))))
        if event in {"null", "boolean", "number", "string"}:
            return float(value) if isinstance(value, Decimal) else value
        raise ValueError("Invalid manifest JSON event")

    try:
        result = read(*next(events), ())
        if next(events, None) is not None or not isinstance(result, dict):
            raise ValueError("Expected one manifest object")
        return _validate_manifest(result, object_map_type=(dict, _ManifestObjects))
    except Exception:
        for index in indexes:
            index.directory.cleanup()
        raise


@lru_cache(maxsize=4)
def _read_manifest(uri, digest):
    parsed = _staging_uri(uri)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DatasetUnavailable("staging_dataset_manifest_digest_required")
    from google.cloud import storage
    blob = storage.Client().bucket(parsed.netloc).blob(parsed.path.lstrip("/"))
    blob.reload()
    with tempfile.TemporaryFile() as source:
        blob.download_to_file(source, if_generation_match=int(blob.generation))
        source.seek(0)
        hasher = sha256()
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise DatasetUnavailable("staging_dataset_manifest_changed")
        source.seek(0)
        return _parse_manifest(source)


def _validate_manifest(value, *, object_map_type=dict):
    if (value.get("schema") != 1 or not _IDENTITY.fullmatch(str(value.get("id", "")))
            or value.get("state") != "ready" or not value.get("snapshot_at")
            or set(value.get("components", {})) != {"order", "shift", "school-lunch"}):
        raise DatasetUnavailable("staging_dataset_manifest_incomplete")
    retained = value.get("retained_components", {})
    if not isinstance(retained, dict) or set(retained) - set(value["components"]):
        raise DatasetUnavailable("staging_dataset_retained_snapshot_invalid")
    for name, component in value["components"].items():
        object_identity = retained_snapshot(retained[name])["id"] if name in retained else value["id"]
        if name in retained and object_identity == value["id"]:
            raise DatasetUnavailable("staging_dataset_retained_snapshot_invalid")
        if (not re.fullmatch(r"sawa_replay_[a-z0-9_]{1,40}", str(component.get("database", "")))
                or not isinstance(component.get("settings"), dict)
                or not isinstance(component.get("objects"), object_map_type)
                or component.get("verified") is not True
                or not isinstance(component.get("table_digests"), dict) or not component["table_digests"]
                or any(not re.fullmatch(r"[0-9a-f]{64}", str(digest)) for digest in component["table_digests"].values())):
            raise DatasetUnavailable("staging_dataset_component_incomplete:" + name)
        for item in component["objects"].values():
            physical = _staging_uri(item["uri"])
            if not physical.path.startswith("/stg-replay/" + object_identity + "/"):
                raise DatasetUnavailable("staging_dataset_object_snapshot_mismatch")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                raise DatasetUnavailable("staging_dataset_object_digest_required")
            if not re.fullmatch(r"[1-9][0-9]*", str(item.get("generation", ""))):
                raise DatasetUnavailable("staging_dataset_object_generation_required")
    return value


def legacy_manifest():
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


def refresh_control_uri():
    # A single commit decision is shared; full manifests/PDFs stay in each own bucket.
    # The immutable initial manifest fixes the coordinator's location.
    parsed = _staging_uri(legacy_manifest()["components"]["order"]["settings"].get("FACILITY_MASTER_URI", ""))
    return "gs://" + parsed.netloc + "/stg-replay/control/"


def read_registry():
    from google.api_core.exceptions import NotFound
    from google.cloud import storage
    parsed = _staging_uri(refresh_control_uri() + "registry.json")
    blob = storage.Client().bucket(parsed.netloc).blob(parsed.path.lstrip("/"))
    try:
        blob.reload()
    except NotFound:
        return initial_registry(), 0
    except Exception as exc:
        raise DatasetUnavailable("staging_dataset_registry_unavailable") from exc
    try:
        value = json.loads(blob.download_as_bytes(if_generation_match=int(blob.generation)), object_pairs_hook=_unique_object)
        validate_registry(value)
        return value, int(blob.generation)
    except DatasetUnavailable:
        raise
    except Exception as exc:
        raise DatasetUnavailable("staging_dataset_registry_unavailable") from exc


def initial_registry():
    original = legacy_manifest()
    return {"schema": 1, "active": original["id"], "snapshots": {original["id"]: {
        "uri": os.environ["SAWA_STG_REPLAY_MANIFEST_URI"],
        "sha256": os.environ["SAWA_STG_REPLAY_MANIFEST_SHA256"],
        "snapshot_at": original["snapshot_at"],
        "runtime_settings_provenance": "legacy_stg_runtime",
    }}, "refresh": None}


def validate_registry(value):
    if (not isinstance(value, dict) or value.get("schema") != 1
            or not isinstance(value.get("snapshots"), dict)
            or value.get("active") not in value["snapshots"]):
        raise DatasetUnavailable("staging_dataset_registry_invalid")
    for identity, entry in value["snapshots"].items():
        if (not _IDENTITY.fullmatch(identity) or not isinstance(entry, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
                or not entry.get("snapshot_at")):
            raise DatasetUnavailable("staging_dataset_registry_invalid")
        parsed = _staging_uri(entry.get("uri", ""))
        if not parsed.path.startswith("/stg-replay/" + identity + "/"):
            raise DatasetUnavailable("staging_dataset_registry_identity_mismatch")


def _runtime_manifest(entry, _seen=frozenset()):
    # Publisher mirrors the exact same immutable bytes to each existing stg bucket.
    # Only registry.json requires shared read access; data/credentials never do.
    bucket = _staging_uri(os.environ["SAWA_STG_REPLAY_MANIFEST_URI"]).netloc
    source = _staging_uri(entry["uri"])
    key = (source.path, entry["sha256"])
    if key in _seen:
        raise DatasetUnavailable("staging_dataset_retained_snapshot_cycle")
    seen = _seen | {key}
    value = _read_manifest("gs://" + bucket + source.path, entry["sha256"])
    legacy = (entry.get("runtime_settings_provenance") == "legacy_stg_runtime"
              and value["id"] == legacy_manifest()["id"]
              and entry["sha256"] == os.environ["SAWA_STG_REPLAY_MANIFEST_SHA256"])
    if legacy:
        return {**value, "runtime_settings_provenance": "legacy_stg_runtime",
                "_component_runtime_provenance": {name: "legacy_stg_runtime" for name in value["components"]}}
    if value.get("runtime_settings_provenance") != "production_environment":
        raise DatasetUnavailable("staging_dataset_runtime_settings_provenance_required")
    provenance = {}
    for feature, component in value["components"].items():
        retained = value.get("retained_components", {}).get(feature)
        if retained is not None:
            retained_snapshot(retained)
            parent = _runtime_manifest(retained, seen)
            if (parent["id"] != retained["id"] or parent["snapshot_at"] != retained["snapshot_at"]
                    or component != parent["components"][feature]):
                raise DatasetUnavailable("staging_dataset_retained_component_changed:" + feature)
            provenance[feature] = parent["_component_runtime_provenance"][feature]
            continue
        provenance[feature] = "production_environment"
        settings = component.get("runtime_settings")
        if (not isinstance(settings, dict) or not RUNTIME_SETTING_KEYS[feature] <= set(settings)
                or any(not runtime_setting_allowed(key, feature) or (item is not None and not isinstance(item, str)) for key, item in settings.items())):
            raise DatasetUnavailable("staging_dataset_runtime_settings_incomplete:" + feature)
    settings = value["components"]["order"].get("producer_runtime_settings")
    if ("order" not in value.get("retained_components", {}) and (not isinstance(settings, dict) or set(settings) != set(RUNTIME_PRODUCER_KEYS)
            or any(item is not None and not isinstance(item, str) for item in settings.values()))):
        raise DatasetUnavailable("staging_dataset_runtime_settings_incomplete:producer")
    return {**value, "_component_runtime_provenance": provenance}


def manifest_for(identity):
    if not staging_runtime() or not _IDENTITY.fullmatch(identity):
        raise DatasetUnavailable("staging_dataset_invalid")
    registry, _ = read_registry()
    entry = registry["snapshots"].get(identity)
    if entry is None:
        raise DatasetUnavailable("staging_dataset_snapshot_unavailable")
    result = _runtime_manifest(entry)
    if result["id"] != identity:
        raise DatasetUnavailable("staging_dataset_registry_identity_mismatch")
    return result


def manifest():
    pinned = _pinned_manifest.get()
    if pinned is not None:
        return pinned
    registry, _ = read_registry()
    entry = registry["snapshots"][registry["active"]]
    result = _runtime_manifest(entry)
    if result["id"] != registry["active"]:
        raise DatasetUnavailable("staging_dataset_registry_identity_mismatch")
    return result


def snapshot_choices():
    registry, _ = read_registry()
    return [{"id": identity, "label": "本番再現用" + ("（最新）" if identity == registry["active"] else "（以前のコピー）"),
             "available": True, "snapshot_at": entry["snapshot_at"]}
            for identity, entry in reversed(list(registry["snapshots"].items()))]


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


def runtime_setting(name, default=None, *, feature, producer=False):
    if current_dataset() == "test":
        return os.getenv(name, default)
    if not (name in RUNTIME_PRODUCER_KEYS if producer else runtime_setting_allowed(name, feature)):
        raise DatasetUnavailable("staging_dataset_runtime_setting_not_allowed:" + name)
    value = manifest()
    if value.get("_component_runtime_provenance", {}).get(feature, value.get("runtime_settings_provenance")) == "legacy_stg_runtime":
        return os.getenv(name, default)
    settings = component(feature).get("producer_runtime_settings" if producer else "runtime_settings")
    if not isinstance(settings, dict):
        raise DatasetUnavailable("staging_dataset_runtime_settings_required:" + feature)
    if name not in settings:
        raise DatasetUnavailable("staging_dataset_runtime_setting_required:" + name)
    # Explicit null records source env absence. Never consult Test's env here.
    item = settings.get(name)
    if item is not None and not isinstance(item, str):
        raise DatasetUnavailable("staging_dataset_runtime_setting_invalid:" + name)
    return default if item is None else item


def order_runtime_setting(name, default=None):
    return runtime_setting(name, default, feature="order")


def producer_runtime_setting(name, default=None):
    return runtime_setting(name, default, feature="order", producer=True)


def school_runtime_setting(name, default=None):
    return runtime_setting(name, default, feature="school-lunch")


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


def storage_prefix(*, feature=None):
    return "stg-replay/" + current_dataset(feature=feature) + "/"


def write_object(bucket, name, *, feature=None):
    if current_dataset() == "test":
        return bucket, name
    if not _staging_bucket(bucket) or not name or name.startswith("/") or ".." in name.split("/"):
        raise DatasetUnavailable("staging_dataset_write_target_invalid")
    prefix = storage_prefix(feature=feature)
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
    prefix = storage_prefix(feature=feature)
    prefixes = (prefix, "input/" + prefix, "output/" + prefix)
    if (parsed.scheme == "gs" and _staging_bucket(parsed.netloc)
            and unquote(parsed.path).lstrip("/").startswith(prefixes)):
        return uri, original_generation, None
    if parsed.scheme in {"", "file"}:
        path = Path(unquote(parsed.path) if parsed.scheme else uri)
        if path.is_absolute() and "/stg-replay/" + current_dataset(feature=feature) + "/" in str(path.resolve()):
            return uri, original_generation, None
    raise DatasetUnavailable("staging_dataset_original_copy_missing")


def verify_copy(content, digest):
    if digest is not None and sha256(content).hexdigest() != digest:
        raise DatasetUnavailable("staging_dataset_original_copy_changed")
    return content


def local_directory(base, *, feature=None):
    path = Path(base)
    if current_dataset() != "test":
        path = path / "stg-replay" / current_dataset(feature=feature)
    path.mkdir(parents=True, exist_ok=True)
    return path


def delivery_headers(*, feature=None):
    return {HEADER: current_dataset(feature=feature)} if current_dataset() != "test" else {}


def delivery_dataset_matches(headers, identity):
    values = [value for key, value in headers.items() if key.lower() == HEADER.lower()]
    return values == [identity] if identity is not None else not values


def delivery_namespace(*, feature=None):
    return current_dataset(feature=feature) + "-" if current_dataset() != "test" else ""


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
