"""Retain exact inputs while avoiding recursive archives and repeated large parses."""
from copy import deepcopy
import hashlib
import json
import os
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import workflow_ocr_delivery_service as delivery


def test_archived_input_is_bounded_and_bound_to_immutable_delivery():
    previous = None
    for index in range(8):
        captured = {"order": {"id": "O", "week_code": "week"},
                    "configured_input": {"facility": {"facility_id": "F"}},
                    "physical_menu_entries": [{"name": "menu"}],
                    "evidence": {"id": f"E{index}", "payload_json": {
                        "raw": "7 / - / １２", "previous_input": previous}},
                    "cache": {"payload": {"previous_input": previous}},
                    "draft": {"id": "D", "rows": [["operator value"]]}, "workflow": {"revision": index}}
        claim = {"delivery_id": f"L{index}", "job_id": "J", "order_id": "O",
                 "workflow_attempt_id": f"A{index}", "workflow_revision": index + 1,
                 "request_identity": {"reparse_read_input": captured}}
        original = deepcopy(captured)
        with delivery.claim_context(claim):
            archived = delivery.archived_processing_input(captured, job_id="J", attempt=f"A{index}", revision=index + 1)
        owner = SimpleNamespace(id=claim["delivery_id"], **{key: claim[key] for key in (
            "order_id", "job_id", "workflow_attempt_id", "workflow_revision", "request_identity")})
        assert delivery.archived_processing_input_matches(archived, owner)
        assert delivery.archived_processing_input_matches(captured, owner)
        assert len(json.dumps(archived)) < 1500
        assert captured == original
        assert captured["evidence"]["payload_json"]["raw"] == "7 / - / １２"
        for key in ("delivery_id", "order_id", "input_sha256"):
            altered = deepcopy(archived)
            altered["archived_input_reference"][key] = "changed"
            assert not delivery.archived_processing_input_matches(altered, owner)
        altered = deepcopy(archived)
        altered["physical_menu_entries"] = []
        assert not delivery.archived_processing_input_matches(altered, owner)
        previous = archived


def test_archive_rejects_foreign_claim_and_preserves_legacy_without_a_claim():
    captured = {"order": {"id": "O"}, "evidence": {"id": "E"}}
    assert delivery.archived_processing_input(captured, job_id="J", attempt="A", revision=1) == captured
    with delivery.claim_context({"job_id": "other"}):
        with pytest.raises(ValueError, match="captured_source_changed"):
            delivery.archived_processing_input(captured, job_id="J", attempt="A", revision=1)


def test_postgres_projection_preserves_types_and_large_execution_inputs():
    dsn = os.environ.get("SAWA_LOCAL_PG_TEST_DSN")
    if not dsn:
        pytest.skip("Dedicated local PostgreSQL is required")
    import psycopg2
    from src.models.ocr_display_projection_v2 import FUNCTION, function_ddl
    from src.models.ocr_display_projection_v1 import expression
    from src.models.ocr_metrics_v1 import PROGRESS_KEYS
    from src.services.snapshot_serialization import snapshots_equal
    with psycopg2.connect(dsn) as conn, conn.cursor() as cursor:
        cursor.execute(function_ddl())
        cursor.execute("CREATE TEMP TABLE projection_probe(metrics json, progress_metrics json, "
                       f"display text GENERATED ALWAYS AS ({FUNCTION}(metrics, progress_metrics)) STORED)")
        cases = [None, {}, {"workflow_attempt_id": "A", "ocr_elapsed_seconds": 1e20,
            "selected_version_input": {"id": "V", "is_current": True, "version_no": 1.0}},
            {"nonworkflow_execution": {"job_id": "J", "selected_version_input": {
                "id": "V", "version_no": 2, "is_current": False}, "orders": {"large": "ignored"}}},
            {"selected_version_input": [], "error_detail": "raw / -"}]
        old = expression("postgresql", progress_keys=PROGRESS_KEYS)
        for metrics in cases:
            cursor.execute("DELETE FROM projection_probe")
            cursor.execute("INSERT INTO projection_probe(metrics, progress_metrics) VALUES (%s,%s)",
                           (json.dumps(metrics), json.dumps({"ocr_elapsed_seconds": None})))
            cursor.execute(f"SELECT {old}, display FROM projection_probe")
            expected, actual = cursor.fetchone()
            assert snapshots_equal(json.loads(expected), json.loads(actual))
        raw = json.dumps({"workflow_attempt_id": "large", "selected_version_input": {"id": "V"},
            "reparse_read_input": {"evidence": "a" * 13_000_000, "cache": "b" * 12_000_000},
            "processing_stage": "queued"})
        cursor.execute("DELETE FROM projection_probe")
        cursor.execute("INSERT INTO projection_probe(metrics) VALUES (%s)", (raw,))
        cursor.execute("UPDATE projection_probe SET progress_metrics=%s", ('{"processing_stage":"running"}',))
        cursor.execute("SELECT md5(metrics::text), display FROM projection_probe")
        digest, projected = cursor.fetchone()
        assert digest == hashlib.md5(raw.encode()).hexdigest()
        assert json.loads(projected)["processing_stage"] == "running"
        assert len(projected) < 3000


def test_postgres_formal_migration_and_downgrade():
    dsn = os.environ.get("SAWA_LOCAL_PG_TEST_DSN")
    if not dsn:
        pytest.skip("Dedicated local PostgreSQL is required")
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.models.ocr_display_projection_v1 import expression
    from src.models.ocr_metrics_v1 import PROGRESS_KEYS
    path = Path(__file__).parents[2] / "migrations/0039_bounded_ocr_display_json.py"
    spec = importlib.util.spec_from_file_location("bounded_ocr_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(dsn)
    try:
        with engine.begin() as connection:
            old = expression("postgresql", progress_keys=PROGRESS_KEYS)
            connection.exec_driver_sql("CREATE TEMP TABLE ocr_jobs(metrics json, progress_metrics json, "
                                       f"display_read_json text GENERATED ALWAYS AS ({old}) STORED)")
            connection.exec_driver_sql("INSERT INTO ocr_jobs(metrics) VALUES ('{\"workflow_attempt_id\":\"A\"}')")
            before = connection.exec_driver_sql("SELECT display_read_json FROM ocr_jobs").scalar_one()
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert connection.exec_driver_sql("SELECT display_read_json FROM ocr_jobs").scalar_one() == before
                migration.downgrade()
                assert connection.exec_driver_sql("SELECT display_read_json FROM ocr_jobs").scalar_one() == before
    finally:
        engine.dispose()
