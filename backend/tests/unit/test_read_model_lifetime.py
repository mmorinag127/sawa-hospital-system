"""Operation-local ORM references; not a cache or a transaction override."""
import gc
import weakref

import pytest
from sqlalchemy import Column, Integer, JSON, create_engine, event, update
from sqlalchemy.orm import Session, declarative_base

from src.db import retain_read_models

Base = declarative_base()


class Payload(Base):
    __tablename__ = 'read_lifetime_payload'
    id = Column(Integer, primary_key=True)
    value = Column(JSON)


@pytest.fixture
def engine():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(Payload.__table__.insert(), {'id': 1, 'value': {'original': 'x' * 10000}})
    yield engine
    engine.dispose()


def test_read_consumers_reuse_loaded_row_and_release_it_at_operation_end(engine):
    statements = []
    event.listen(engine, 'before_cursor_execute', lambda conn, cursor, statement, *args: statements.append(statement))
    with Session(engine) as session:
        with retain_read_models(session):
            row = session.get(Payload, 1)
            original = weakref.ref(row)
            del row
            gc.collect()
            assert original() is not None
            assert session.get(Payload, 1) is original()
            assert sum(sql.lstrip().upper().startswith('SELECT') for sql in statements) == 1
        gc.collect()
        assert original() is None
        session.get(Payload, 1)
        assert sum(sql.lstrip().upper().startswith('SELECT') for sql in statements) == 2


def test_nested_scope_and_exception_cleanup_do_not_leave_read_references(engine):
    with Session(engine) as session:
        with pytest.raises(ValueError, match='original failure'):
            with retain_read_models(session):
                with retain_read_models(session):
                    row = session.get(Payload, 1)
                    original = weakref.ref(row)
                    del row
                gc.collect()
                assert original() is not None
                raise ValueError('original failure')
        gc.collect()
        assert original() is None
        assert 'refs' not in session.info


def test_explicit_fresh_command_read_is_not_suppressed(engine):
    with Session(engine) as session, retain_read_models(session):
        row = session.get(Payload, 1)
        session.execute(update(Payload).where(Payload.id == 1).values(value={'changed': True})
                        .execution_options(synchronize_session=False))
        assert 'original' in row.value
        assert session.get(Payload, 1, populate_existing=True).value == {'changed': True}


def test_non_orm_test_adapter_does_not_gain_a_persistent_cache():
    adapter = object()
    with retain_read_models(adapter):
        assert type(adapter) is object
