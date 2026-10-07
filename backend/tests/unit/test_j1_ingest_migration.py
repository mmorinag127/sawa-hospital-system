"""Formal 0032 data/ownership cases; source definitions, not catalog evidence."""

import importlib.util
from pathlib import Path
from datetime import datetime

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

from src.services.ingest_job_service import payload_digest


def migration(connection, filename):
    path = Path(__file__).resolve().parents[2] / "migrations" / filename
    spec = importlib.util.spec_from_file_location(filename.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def test_0032_preserves_raw_rows_and_never_mints_legacy_execution_claims(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    try:
        with engine.begin() as connection:
            migration(connection, "0004_ingest_jobs.py").upgrade()
            migration(connection, "0011_uploaded_pdfs.py").upgrade()
            old = sa.Table("ingest_jobs", sa.MetaData(), autoload_with=connection)
            source = {"message_id": "authored", "pdf_uri": "fixture://authored", "week_hint": None, "raw": {"zero": 0, "null": None}}
            for state in ("pending", "processing", "done", "error"):
                connection.execute(old.insert().values(id=state, status=state, payload=source, attempts=2,
                    last_error="retained error" if state == "error" else None, created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
            formal = migration(connection, "0032_ingest_attempt_ownership.py")
            formal.upgrade()
            current = sa.Table("ingest_jobs", sa.MetaData(), autoload_with=connection)
            for row in connection.execute(sa.select(current)).mappings():
                assert row["status"] == row["id"]
                assert row["payload"] == source
                assert row["attempts"] == 2
                assert row["generation_attempts"] == (2 if row["status"] == "pending" else None)
                assert row["input_digest"] == payload_digest(source)
                assert row["claim_id"] is None
                assert row["recovery_claim_id"] is None
                assert bool(row["generation"]) == (row["status"] == "pending")
                assert row["failure_kind"] == ("terminal_unclassified" if row["status"] == "error" else None)
            with pytest.raises(RuntimeError, match="unknown existing ownership"):
                formal.upgrade()
            with pytest.raises(RuntimeError, match="cannot be discarded"):
                formal.downgrade()
    finally:
        engine.dispose()
