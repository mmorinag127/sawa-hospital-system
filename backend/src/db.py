import os
from functools import lru_cache
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from src.maintenance.database_target import explicit_database_uri
from src import staging_data


DB_URI = explicit_database_uri()
connect_args: dict = {}
engine_kwargs: dict = {"echo": False, "future": True, "connect_args": connect_args}
if DB_URI.startswith("sqlite"):
    raw_timeout = os.getenv("DB_SQLITE_TIMEOUT", "30")
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 30.0
    connect_args = {"check_same_thread": False, "timeout": timeout}
    engine_kwargs["connect_args"] = connect_args
else:
    raw_recycle = str(os.getenv("DB_POOL_RECYCLE_SECONDS", "300") or "").strip()
    try:
        pool_recycle = max(int(raw_recycle), 1)
    except ValueError:
        pool_recycle = 300
    raw_pool_size = str(os.getenv("DB_POOL_SIZE", "") or "").strip()
    raw_max_overflow = str(os.getenv("DB_MAX_OVERFLOW", "") or "").strip()
    engine_kwargs.update(
        {
            "pool_pre_ping": True,
            "pool_recycle": pool_recycle,
            "pool_use_lifo": True,
        }
    )
    if raw_pool_size:
        try:
            engine_kwargs["pool_size"] = max(int(raw_pool_size), 1)
        except ValueError:
            pass
    if raw_max_overflow:
        try:
            engine_kwargs["max_overflow"] = max(int(raw_max_overflow), 0)
        except ValueError:
            pass

engine = create_engine(DB_URI, **engine_kwargs)
ControlSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


@lru_cache(maxsize=4)
def _replay_engine(uri):
    selected = create_engine(uri, **engine_kwargs)
    from src.maintenance.formal_migration_schema import require_formal_head
    try:
        with selected.connect() as connection:
            require_formal_head(connection)
    except Exception:
        selected.dispose()
        raise staging_data.DatasetUnavailable("staging_dataset_schema_not_ready") from None
    return selected


def get_engine():
    if staging_data.current_dataset() == "test":
        return engine
    return _replay_engine(staging_data.database_uri(DB_URI, "order"))


def current_database_uri():
    return staging_data.database_uri(DB_URI, "order")


def SessionLocal(**kwargs):
    return ControlSessionLocal(bind=get_engine(), **kwargs)


@contextmanager
def control_session_scope():
    with staging_data.dataset_context("test"):
        with session_scope() as session:
            yield session


@contextmanager
def retain_read_models(session):
    """Keep one operation's loaded rows alive until all read consumers finish."""
    if not isinstance(session, Session):
        yield
        return
    references = {id(row): row for row in session.identity_map.values()}

    def retain(_session, row):
        references[id(row)] = row

    event.listen(session, 'loaded_as_persistent', retain)
    try:
        yield
    finally:
        event.remove(session, 'loaded_as_persistent', retain)
        references.clear()


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
