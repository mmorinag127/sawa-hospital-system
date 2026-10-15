"""Bounded observation only; never serialize OCR inputs or exception messages."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from importlib.metadata import version
from itertools import count
import json
import logging
import os
import re
import sys
import threading
import time


logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)
logger.propagate = False
STACK_DELAY_SECONDS = 60
MAX_STACK_SNAPSHOTS = 3
MAX_STACK_FRAMES = 24
MAX_PHASE_PAIRS = 16
_ids = count(1)
_current = ContextVar("recognizer_diagnostics", default=None)
_PHASES = {"initialize", "recognize", "preprocess", "inference", "postprocess", "candidates"}


def _metadata():
    result = {}
    for package in ("yomitoku", "torch", "huggingface-hub"):
        try:
            value = version(package)
            result[package] = value if re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", value) else "unavailable"
        except Exception:
            result[package] = "unavailable"
    torch = sys.modules.get("torch")
    for name in ("get_num_threads", "get_num_interop_threads"):
        try:
            value = getattr(torch, name)()
            result[name] = value if type(value) is int else None
        except Exception:
            result[name] = None
    return result


class _Observation:
    def __init__(self, phase):
        self.identifier = next(_ids)
        self.thread = threading.get_ident()
        self.pid = os.getpid()
        self.started = time.monotonic()
        self.phase = phase
        self.phase_started = self.started
        self.pairs = 0
        self.closed = False
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.timer = threading.Timer(STACK_DELAY_SECONDS, self.observe)
        self.timer.daemon = True

    def observe(self):
        for index in range(MAX_STACK_SNAPSHOTS):
            if self.stopped.is_set():
                return
            self.snapshot()
            if index + 1 < MAX_STACK_SNAPSHOTS and self.stopped.wait(STACK_DELAY_SECONDS):
                return

    def emit(self, event, **fields):
        try:
            logger.info("recognizer_diagnostic %s", json.dumps({
                "event": event, "observation": self.identifier, "pid": self.pid,
                "thread": self.thread, "phase": self.phase,
                "elapsed_seconds": round(time.monotonic() - self.started, 3), **fields,
            }, sort_keys=True))
        except Exception:
            # Diagnostics must not replace the original return value or exception.
            pass

    def snapshot(self):
        with self.lock:
            if self.closed:
                return
            frame = None
            try:
                frame = sys._current_frames().get(self.thread)
                frames = []
                while frame is not None and len(frames) < MAX_STACK_FRAMES:
                    frames.append({"filename": os.path.basename(frame.f_code.co_filename),
                                   "function": frame.f_code.co_name, "line": frame.f_lineno})
                    frame = frame.f_back
                self.emit("waiting_stack", frames=frames,
                          phase_elapsed_seconds=round(time.monotonic() - self.phase_started, 3))
            except Exception:
                self.emit("stack_unavailable")
            finally:
                del frame


def diagnosed(operation):
    if operation not in {"initialize", "recognize"}:
        raise ValueError("Unknown diagnostic operation")

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            observation = _Observation(operation)
            token = _current.set(observation)
            outcome = "error"
            try:
                observation.emit("begin", metadata=_metadata())
                try:
                    observation.timer.start()
                except Exception:
                    observation.emit("timer_unavailable")
                result = function(*args, **kwargs)
                outcome = "return"
                return result
            finally:
                with observation.lock:
                    observation.closed = True
                    observation.stopped.set()
                    observation.timer.cancel()
                    observation.phase = operation
                    observation.emit("end", outcome=outcome)
                _current.reset(token)
        return wrapped
    return decorate


@contextmanager
def phase(name):
    if name not in _PHASES:
        raise ValueError("Unknown diagnostic phase")
    observation = _current.get()
    if observation is None:
        yield
        return
    with observation.lock:
        previous = observation.phase, observation.phase_started
        observation.phase, observation.phase_started = name, time.monotonic()
        emit = observation.pairs < MAX_PHASE_PAIRS
        observation.pairs += 1
        if emit:
            observation.emit("phase_begin")
    outcome = "error"
    try:
        yield
        outcome = "return"
    finally:
        with observation.lock:
            if emit:
                observation.emit("phase_end", outcome=outcome,
                                 phase_elapsed_seconds=round(time.monotonic() - observation.phase_started, 3))
            observation.phase, observation.phase_started = previous
