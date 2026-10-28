"""Separate frequently written progress from immutable execution evidence."""
from sqlalchemy import JSON
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


PROGRESS_KEYS = frozenset({
    "processing_stage", "result_state", "stage_updated_at", "progress_step",
    "progress_total", "progress_label", "next_recovery_at", "auto_recovery_count",
    "ocr_slot_wait_started_at", "ocr_slot_acquired_at", "ocr_slot_wait_seconds",
    "awaiting_output_since", "ocr_started_at", "ocr_finished_at", "ocr_elapsed_seconds",
    "elapsed_seconds", "run_started_at", "run_finished_at",
})


def split_metrics(value):
    if not isinstance(value, dict):
        return value, None
    return ({key: item for key, item in value.items() if key not in PROGRESS_KEYS},
            {key: item for key, item in value.items() if key in PROGRESS_KEYS})


class MergeMetrics(FunctionElement):
    type = JSON()
    inherit_cache = True

    def __getitem__(self, key):
        if isinstance(key, str) and key not in PROGRESS_KEYS:
            return list(self.clauses)[0][key]
        return super().__getitem__(key)


@compiles(MergeMetrics)
@compiles(MergeMetrics, "postgresql")
def _postgres(element, compiler, **kwargs):
    base, progress = (compiler.process(arg, **kwargs) for arg in element.clauses)
    return (f"(CASE WHEN {progress} IS NULL OR {progress}::jsonb = '{{}}'::jsonb "
            f"OR json_typeof({progress}) <> 'object' THEN {base} "
            f"ELSE (COALESCE({base}::jsonb, '{{}}'::jsonb) || {progress}::jsonb)::json END)")


@compiles(MergeMetrics, "sqlite")
def _sqlite(element, compiler, **kwargs):
    base, progress = (compiler.process(arg, **kwargs) for arg in element.clauses)
    # json_patch removes explicit nulls. Union JSON fragments instead to retain them.
    return (f"(CASE WHEN {progress} IS NULL OR json_type({progress}) <> 'object' "
            f"OR {progress} = '{{}}' THEN {base} ELSE "
            f"(SELECT json_group_object(k, json(v)) FROM ("
            f"SELECT key AS k, {base} -> fullkey AS v FROM json_each({base}) "
            f"WHERE key NOT IN (SELECT key FROM json_each({progress})) UNION ALL "
            f"SELECT key AS k, {progress} -> fullkey AS v FROM json_each({progress}))) END)")
