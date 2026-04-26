import os
from urllib.parse import quote_plus
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

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


DB_URI = os.getenv("DB_URI") or _build_db_uri() or "sqlite:///./dev.db"
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
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


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
