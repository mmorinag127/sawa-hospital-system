"""Real isolated PostgreSQL capture; no connection to live staging or production."""
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from live_schema_adoption_support import owned_postgres, target, ROOT
from test_live_schema_adoption import generated_contract
from test_initial_saved_source_service import saved, all_rows, MODELS
from test_canonical_facility_lineage import authored
from src.services import canonical_facility_store as store
from src.services.initial_saved_source_service import capture_saved_source, source_receipt
import prepare_stg_saved_import as transport


@pytest.fixture(autouse=True)
def owned_fixture_database(monkeypatch):
    # The saved fixture selects its own DB_URI/ALEMBIC_DB_URI, not the runner's scratch database.
    monkeypatch.delenv("DATABASE_URL", raising=False)


@contextmanager
def migrated_staging_shape():
    with owned_postgres() as (admin, _root):
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("CREATE ROLE orders_app LOGIN")
            connection.exec_driver_sql("CREATE DATABASE orders OWNER orders_app")
        url = admin.url.set(username="orders_app", database="orders")
        engine = sa.create_engine(url)
        try:
            with target(url), engine.connect() as connection:
                config = Config(str(ROOT / "backend/alembic.ini"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            yield engine
        finally:
            engine.dispose()


@pytest.mark.parametrize("selection", [["O"], ["missing-order"]])
def test_real_postgresql_capture_preserves_every_saved_value_and_closes_transaction(saved, selection):
    fixture = all_rows(saved)
    master = {"facilities": [{"facility_id": "F", "versions": [
        store.initial_facility_version(authored()["facility"], "a" * 64)]}]}
    policy = {"recorded_policy": True}
    with migrated_staging_shape() as engine:
        with engine.begin() as connection:
            for model in MODELS:
                rows = fixture[model.__tablename__]
                if rows:
                    connection.execute(sa.insert(model.__table__), rows)
        with Session(engine) as session:
            before = all_rows(session)
            snapshot = capture_saved_source(session, "O")
        statements = []

        def audit(_connection, _cursor, statement, *_args):
            statements.append(statement)
            assert statement.lstrip().upper().startswith(("SELECT", "SHOW", "SET")), statement

        sa.event.listen(engine, "before_cursor_execute", audit)
        try:
            with engine.connect() as connection:
                if selection == ["O"]:
                    result = transport.capture(connection, master, policy, selection)
                    assert result["initial_saved_imports"]["O"]["receipt"] == source_receipt(snapshot)
                else:
                    with pytest.raises(ValueError, match="saved_import_selected_order_inventory_changed"):
                        transport.capture(connection, master, policy, selection)
                assert not connection.in_transaction()
                assert not connection.invalidated
        finally:
            sa.event.remove(engine, "before_cursor_execute", audit)
        assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        with Session(engine) as session:
            assert all_rows(session) == before
