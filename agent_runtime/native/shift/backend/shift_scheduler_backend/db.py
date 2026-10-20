from collections.abc import Generator
from dataclasses import dataclass
import os
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _database_url_from_env() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must explicitly select the database target")
    return database_url


def _engine_connect_args(database_url: str) -> dict[str, Any]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


@dataclass
class _DatabaseRuntime:
    database_url: str
    engine: Engine
    session_factory: sessionmaker[Session]


_runtime: _DatabaseRuntime | None = None


def configure_database(database_url: str | None = None) -> str:
    global _runtime

    target_url = database_url or _database_url_from_env()
    if _runtime is not None and _runtime.database_url == target_url:
        return _runtime.database_url

    next_engine = create_engine(
        target_url,
        connect_args=_engine_connect_args(target_url),
        pool_pre_ping=not target_url.startswith("sqlite"),
    )
    next_session_factory = sessionmaker(bind=next_engine, autoflush=False, autocommit=False)
    previous_runtime = _runtime
    _runtime = _DatabaseRuntime(
        database_url=target_url,
        engine=next_engine,
        session_factory=next_session_factory,
    )
    if previous_runtime is not None:
        previous_runtime.engine.dispose()
    return _runtime.database_url


def get_database_url() -> str:
    return configure_database()


def get_engine() -> Engine:
    configure_database()
    assert _runtime is not None
    return _runtime.engine


def get_session_factory() -> sessionmaker[Session]:
    configure_database()
    assert _runtime is not None
    return _runtime.session_factory


class _EngineProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_engine(), name)


class _SessionLocalProxy:
    def __call__(self, *args: Any, **kwargs: Any) -> Session:
        return get_session_factory()(*args, **kwargs)


engine = _EngineProxy()
SessionLocal = _SessionLocalProxy()


class Base(DeclarativeBase):
    pass


def get_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
