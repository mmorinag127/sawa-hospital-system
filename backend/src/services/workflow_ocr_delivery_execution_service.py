"""Cross-process runner exclusion, held beyond a process lease until exit."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import tempfile

from sqlalchemy import text
from src.db import get_engine


@contextmanager
def execution_mutex(delivery_id):
    key = int.from_bytes(sha256(delivery_id.encode()).digest()[:8], "big", signed=True)
    if get_engine().dialect.name == "postgresql":
        # A dedicated session advisory lock survives runner result transactions.
        with get_engine().connect() as connection:
            acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one())
            connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    connection.commit()
    elif get_engine().dialect.name == "sqlite":
        import fcntl
        if not get_engine().url.database or get_engine().url.database == ":memory:":
            raise ValueError("workflow_ocr_delivery_file_backed_sqlite_required")
        database = str(Path(get_engine().url.database).resolve())
        lock_id = sha256((database + ":" + delivery_id).encode()).hexdigest()
        with open(Path(tempfile.gettempdir()) / ("sawa-workflow-ocr-" + lock_id + ".lock"), "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                try:
                    yield True
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
    else:
        raise ValueError("workflow_ocr_delivery_dialect_unsupported")
