import os
from collections import OrderedDict
from threading import RLock
from urllib.parse import quote_plus
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from src.migration_catalog import explicit_database_uri
from src import staging_data

def _build_db_uri() -> str | None:
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    name = os.getenv("DB_NAME")
    host = os.getenv("DB_HOST")
    if not all([user, password, name, host]):
        return None
    driver = os.getenv("DB_DRIVER", "postgresql+psycopg2")
    port = os.getenv("DB_PORT")
    user_enc = quote_plus(user)
    pass_enc = quote_plus(password)
    if host.startswith("/cloudsql/"):
        return f"{driver}://{user_enc}:{pass_enc}@/{name}?host={host}"
    host_part = f"{host}:{port}" if port else host
    return f"{driver}://{user_enc}:{pass_enc}@{host_part}/{name}"


DB_URI = explicit_database_uri(required=False)
if DB_URI is None:
    DB_URI = _build_db_uri()
if not DB_URI or not DB_URI.strip():
    raise RuntimeError("Explicit database configuration is required")
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
_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


_replay_engines = OrderedDict()
_replay_engine_lock = RLock()


def _create_replay_engine(uri):
    from src.migration_catalog import require_runtime_schema
    selected = create_engine(uri, **engine_kwargs)
    try:
        with selected.connect() as connection:
            require_runtime_schema(connection)
    except Exception:
        selected.dispose()
        raise staging_data.DatasetUnavailable("staging_dataset_schema_not_ready") from None
    return selected


def _replay_engine(uri):
    with _replay_engine_lock:
        if uri in _replay_engines:
            _replay_engines.move_to_end(uri)
            return _replay_engines[uri]
        selected = _create_replay_engine(uri)
        _replay_engines[uri] = selected
        while len(_replay_engines) > 4:
            _, retired = _replay_engines.popitem(last=False)
            retired.dispose()
        return selected


def get_engine():
    if staging_data.current_dataset() == "test":
        return engine
    return _replay_engine(staging_data.database_uri(DB_URI, "school-lunch"))


def SessionLocal(**kwargs):
    return _sessions(bind=get_engine(), **kwargs)


@contextmanager
def control_session_scope():
    with staging_data.dataset_context("test"):
        with session_scope() as session:
            yield session


def initialize_database() -> None:
    from src.migration_catalog import require_runtime_schema

    with engine.connect() as connection:
        require_runtime_schema(connection)


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
