"""Versioned display projection; keep historical migration expressions immutable."""
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy import Text

JOB_KEYS = (
    "workflow_attempt_id", "workflow_completion_revision", "ocr_started_at", "run_started_at", "ocr_finished_at",
    "run_finished_at", "stage_updated_at", "ocr_elapsed_seconds", "elapsed_seconds", "processing_stage",
    "result_state", "progress_step", "progress_total", "progress_label", "error", "error_detail",
    "error_user_message", "recovery_action", "evidence_run_id", "rerun_terminal_receipt",
    "auto_recovery_count", "next_recovery_at", "selected_pdf_receipt",
)
VERSION_KEYS = (
    "id", "order_id", "version_no", "document_id", "message_id", "storage_uri", "facility_code",
    "week_code", "received_at", "is_current", "created_at",
)


def expression(dialect, *, progress_keys=()):
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("Unsupported OCR display projection dialect")
    postgres = dialect == "postgresql"
    kind = "json_typeof" if postgres else "json_type"

    def path(*keys):
        value = "metrics"
        if keys and keys[0] in progress_keys:
            key = keys[0]
            value = "COALESCE((progress_metrics -> '" + key + "'), (metrics -> '" + key + "'))"
            keys = keys[1:]
        for key in keys:
            value = "(" + value + " -> '" + key + "')"
        return value

    def obj(entries):
        if postgres:
            fields = ["'\"" + key + "\":' || COALESCE((" + value + ")::text, 'null')" for key, value in entries]
            return "('{' || " + " || ',' || ".join(fields) + " || '}')"
        return "json_object(" + ", ".join("'" + key + "', json(" + value + ")" for key, value in entries) + ")"

    def version(*prefix):
        raw = path(*prefix)
        values = obj([(key, path(*prefix, key)) for key in VERSION_KEYS])
        return "CASE WHEN " + kind + "(" + raw + ") = 'object' THEN " + values + " ELSE NULL END"

    native_prefix = ("nonworkflow_execution",)
    native = obj([(key, path(*native_prefix, key)) for key in (
        "execution_id", "ingest_ocr_mode", "intake_protocol", "job_id", "order_id", "selected_pdf_receipt",
    )] + [("selected_version_input", version(*native_prefix, "selected_version_input")),
          ("ingest_lease", obj([("generation", path(*native_prefix, "ingest_lease", "generation"))]))])
    native = "CASE WHEN " + kind + "(" + path(*native_prefix) + ") = 'object' THEN " + native + " ELSE NULL END"
    selected = "CASE WHEN (metrics ->> 'workflow_attempt_id') IS NULL THEN " + path(
        *native_prefix, "selected_version_input") + " ELSE " + path("selected_version_input") + " END"
    kinds = ("object", "array", "string", "number", "boolean", "null") if postgres else (
        "object", "array", "text", "integer", "real", "true", "false", "null")
    selected_kind = "CASE " + kind + "(" + selected + ") " + " ".join(
        "WHEN '" + value + "' THEN '\"" + value + "\"'" for value in kinds) + " ELSE NULL END"
    return obj([(key, path(key)) for key in JOB_KEYS] + [
        ("nonworkflow_execution", native),
        ("selected_version_input", version("selected_version_input")),
        ("selected_version_kind", selected_kind),
    ])


class DisplayProjection(FunctionElement):
    type = Text()
    inherit_cache = True


@compiles(DisplayProjection)
@compiles(DisplayProjection, "postgresql")
def _postgres(_element, _compiler, **_kwargs):
    from src.models.ocr_metrics_v1 import PROGRESS_KEYS
    return expression("postgresql", progress_keys=PROGRESS_KEYS)


@compiles(DisplayProjection, "sqlite")
def _sqlite(_element, _compiler, **_kwargs):
    from src.models.ocr_metrics_v1 import PROGRESS_KEYS
    return expression("sqlite", progress_keys=PROGRESS_KEYS)
