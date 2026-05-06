from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from loguru import logger
from sqlalchemy import text

from src.db import engine


class OcrExecutionSlotTimeout(RuntimeError):
    pass


_LOCK_KEY_BASE = 860_426_001
_LOCAL_LOCK = threading.Lock()
_LOCAL_SEMAPHORE: threading.BoundedSemaphore | None = None
_LOCAL_SEMAPHORE_SLOTS: int | None = None


def _read_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)


def _read_float_env(name: str, default: float, *, minimum: float = 0.1) -> float:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(value, minimum)


def _max_slots() -> int:
    return _read_int_env("WORKFLOW_V2_OCR_MAX_CONCURRENT", 1, minimum=1)


def _poll_seconds() -> float:
    return _read_float_env("WORKFLOW_V2_OCR_SLOT_POLL_SECONDS", 5.0, minimum=0.5)


def _timeout_seconds() -> float:
    return _read_float_env("WORKFLOW_V2_OCR_SLOT_TIMEOUT_SECONDS", 2700.0, minimum=1.0)


def _local_semaphore(max_slots: int) -> threading.BoundedSemaphore:
    global _LOCAL_SEMAPHORE, _LOCAL_SEMAPHORE_SLOTS
    with _LOCAL_LOCK:
        if _LOCAL_SEMAPHORE is None or _LOCAL_SEMAPHORE_SLOTS != max_slots:
            _LOCAL_SEMAPHORE = threading.BoundedSemaphore(max_slots)
            _LOCAL_SEMAPHORE_SLOTS = max_slots
        return _LOCAL_SEMAPHORE


@contextmanager
def acquire_ocr_execution_slot(*, order_id: str | None = None, job_id: str | None = None) -> Iterator[dict]:
    max_slots = _max_slots()
    timeout_seconds = _timeout_seconds()
    poll_seconds = _poll_seconds()
    deadline = time.monotonic() + timeout_seconds

    if engine.dialect.name != "postgresql":
        semaphore = _local_semaphore(max_slots)
        while not semaphore.acquire(blocking=False):
            if time.monotonic() >= deadline:
                raise OcrExecutionSlotTimeout("ocr_execution_slot_timeout")
            time.sleep(poll_seconds)
        try:
            yield {"backend": "local_semaphore", "slot": 0, "max_slots": max_slots}
        finally:
            semaphore.release()
        return

    connection = engine.connect()
    acquired_key: int | None = None
    acquired_slot: int | None = None
    try:
        while acquired_key is None:
            for slot in range(max_slots):
                lock_key = _LOCK_KEY_BASE + slot
                acquired = bool(
                    connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": lock_key},
                    ).scalar()
                )
                if acquired:
                    acquired_key = lock_key
                    acquired_slot = slot
                    break
            if acquired_key is not None:
                break
            if time.monotonic() >= deadline:
                raise OcrExecutionSlotTimeout("ocr_execution_slot_timeout")
            logger.info(
                "OCR execution slot wait order_id={} job_id={} max_slots={}",
                order_id,
                job_id,
                max_slots,
            )
            time.sleep(poll_seconds)
        yield {
            "backend": "postgresql_advisory_lock",
            "slot": acquired_slot,
            "lock_key": acquired_key,
            "max_slots": max_slots,
        }
    finally:
        if acquired_key is not None:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": acquired_key},
            )
        connection.close()
