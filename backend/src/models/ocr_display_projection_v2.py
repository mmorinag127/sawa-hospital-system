"""Parse execution JSON once when maintaining the small display projection."""
from sqlalchemy import DDL, Text, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from src.models.ocr_display_projection_v1 import JOB_KEYS, VERSION_KEYS, expression
from src.models.ocr_metrics_v1 import PROGRESS_KEYS

FUNCTION = "sawa_ocr_display_projection_v2"


def function_ddl():
    body = expression("postgresql", progress_keys=PROGRESS_KEYS)
    def keys(values):
        return "ARRAY[" + ",".join("'" + value + "'" for value in values) + "]::text[]"
    version_keys = keys(VERSION_KEYS)
    native_keys = keys(("execution_id", "ingest_ocr_mode", "intake_protocol", "job_id", "order_id",
                        "selected_pdf_receipt", "selected_version_input", "ingest_lease"))
    top_keys = keys((*JOB_KEYS, "selected_version_input", "nonworkflow_execution"))
    return f"""CREATE OR REPLACE FUNCTION sawa_json_keys_v1(raw json, keys text[])
RETURNS json LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $keys$
    SELECT CASE WHEN json_typeof(raw) = 'object' THEN
        (SELECT COALESCE(json_object_agg(key, value), '{{}}'::json)
         FROM json_each(raw) WHERE key = ANY(keys)) ELSE raw END
$keys$;
CREATE OR REPLACE FUNCTION {FUNCTION}(raw_metrics json, raw_progress json)
RETURNS text LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE
    metrics json;
    progress_metrics json := raw_progress;
BEGIN
    IF json_typeof(raw_metrics) = 'object' THEN
        SELECT COALESCE(json_object_agg(e.key, CASE
            WHEN e.key = 'selected_version_input' THEN sawa_json_keys_v1(e.value, {version_keys})
            WHEN e.key = 'nonworkflow_execution' AND json_typeof(e.value) = 'object' THEN
                (SELECT COALESCE(json_object_agg(n.key, CASE
                    WHEN n.key = 'selected_version_input' THEN sawa_json_keys_v1(n.value, {version_keys})
                    ELSE n.value END), '{{}}'::json)
                 FROM json_each(e.value) n WHERE n.key = ANY({native_keys}))
            ELSE e.value END), '{{}}'::json)
        INTO metrics FROM json_each(raw_metrics) e WHERE e.key = ANY({top_keys});
    ELSE
        metrics := raw_metrics;
    END IF;
    RETURN {body};
END;
$function$"""


def install_for_metadata(metadata):
    event.listen(metadata, "before_create", DDL(function_ddl()).execute_if(dialect="postgresql"))


class DisplayProjection(FunctionElement):
    type = Text()
    inherit_cache = True


@compiles(DisplayProjection)
@compiles(DisplayProjection, "postgresql")
def _postgres(_element, _compiler, **_kwargs):
    return f"{FUNCTION}(metrics, progress_metrics)"


@compiles(DisplayProjection, "sqlite")
def _sqlite(_element, _compiler, **_kwargs):
    return expression("sqlite", progress_keys=PROGRESS_KEYS)
