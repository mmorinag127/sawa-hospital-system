"""PG16 transaction boundary only, not full order-flow or live acceptance."""

import os
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from c1_pg_ownership import additional_profile, require_server


@pytest.fixture(scope="module")
def pg():
    uri = os.environ["MODERNIZATION_PG_TEST_URI"]
    url = make_url(uri)
    major = additional_profile(url, "output")
    assert all(os.environ[key] == uri for key in ("DB_URI", "DATABASE_URL", "ALEMBIC_DB_URI"))
    # Import production only after rejecting any non-test target.
    from src import db
    from src.services import order_service
    from src.models.order import Order, OrderLine
    from src.maintenance.formal_migration_schema import require_formal_head

    assert db.engine.url == url
    independent = create_engine(url)
    with independent.connect() as connection:
        require_server(connection, major)
        assert connection.exec_driver_sql("SELECT current_database(), current_user").one() == (
            "sawa_modernization_output_test", "mmorinag")
        assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 16
        require_formal_head(connection)
    try:
        yield independent, order_service, Order, OrderLine
    finally:
        independent.dispose()


def order_values(identity):
    return dict(id=identity, document_uri="owned-component://pg-output-boundary",
                message_id=identity, received_at=datetime(2026, 10, 11))


@pytest.mark.parametrize("membership", ["order", "line"])
def test_writer_excludes_membership_insert_until_release(pg, membership):
    engine, service, Order, OrderLine = pg
    identity = "pg-boundary-" + uuid4().hex
    parent = "pg-parent-" + uuid4().hex
    if membership == "line":
        with engine.begin() as connection:
            connection.execute(insert(Order), order_values(parent))
        statement = insert(OrderLine).values(id=identity, order_id=parent, quantity_original=7)
        model = OrderLine
    else:
        statement = insert(Order).values(**order_values(identity))
        model = Order
    try:
        with service._daily_override_transaction() as writer:
            writer_pid = writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
            with engine.connect() as contender:
                assert contender.exec_driver_sql("SELECT pg_backend_pid()").scalar_one() != writer_pid
                contender.exec_driver_sql("SET LOCAL lock_timeout = '250ms'")
                with pytest.raises(DBAPIError) as caught:
                    contender.execute(statement)
                assert caught.value.orig.pgcode == "55P03"
                contender.rollback()
            assert writer.execute(select(model.id).where(model.id == identity)).first() is None
        with engine.begin() as contender:
            contender.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            contender.execute(statement)
        with engine.connect() as connection:
            assert connection.execute(select(model.id).where(model.id == identity)).scalar_one() == identity
    finally:
        with engine.begin() as connection:
            connection.execute(model.__table__.delete().where(model.id == identity))
            if membership == "line":
                connection.execute(Order.__table__.delete().where(Order.id == parent))


def test_read_snapshot_allows_writer_commit_and_rejects_write(pg):
    engine, service, Order, _ = pg
    identity = "pg-reader-" + uuid4().hex
    forbidden = "pg-readonly-" + uuid4().hex
    try:
        with service._daily_override_read_transaction() as reader:
            assert reader.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
            assert reader.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            pid = reader.execute(text("SELECT pg_backend_pid()")).scalar_one()
            query = select(Order.id).where(Order.id == identity)
            assert reader.execute(query).first() is None
            with engine.begin() as writer:
                assert writer.exec_driver_sql("SELECT pg_backend_pid()").scalar_one() != pid
                writer.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
                writer.exec_driver_sql("SET LOCAL statement_timeout = '3s'")
                writer.execute(insert(Order), order_values(identity))
            assert reader.execute(query).first() is None
        with engine.connect() as connection:
            assert connection.execute(query).scalar_one() == identity
        # Let the real context manager roll back the rejected transaction.
        with pytest.raises(DBAPIError) as caught:
            with service._daily_override_read_transaction() as reader:
                reader.execute(insert(Order), order_values(forbidden))
        assert caught.value.orig.pgcode == "25006"
        with engine.connect() as connection:
            assert connection.execute(select(Order.id).where(Order.id == forbidden)).first() is None
    finally:
        with engine.begin() as connection:
            connection.execute(Order.__table__.delete().where(Order.id.in_([identity, forbidden])))
