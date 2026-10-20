"""Boot an unchanged feature API with strict local trial storage confinement."""
import importlib
import os
from pathlib import Path
import sys
import threading
import time
import signal

import uvicorn
from fastapi.responses import JSONResponse

data = Path(os.environ["SAWA_TRIAL_DATA"]).resolve()
parent_pid = os.getppid()


def stop_with_parent():
    while True:
        time.sleep(.2)
        if os.getppid() != parent_pid:
            os.killpg(os.getpgrp(), signal.SIGTERM)
            return


threading.Thread(target=stop_with_parent, daemon=True).start()
os.environ["TMPDIR"] = str(data / "tmp")
Path(os.environ["TMPDIR"]).mkdir(exist_ok=True)
sys.dont_write_bytecode = True


def confined_writes(event, args):
    paths = []
    if event == "open":
        path, mode, flags = args
        if (mode and any(c in mode for c in "wax+")) or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
            paths = [path]
    elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate"}:
        paths = [args[0]]
    elif event in {"os.rename", "os.link", "os.symlink"}:
        paths = list(args[:2])
    for path in paths:
        if isinstance(path, (str, bytes, os.PathLike)):
            resolved = Path(os.fsdecode(path)).resolve()
            if not resolved.is_relative_to(data) and resolved != Path("/dev/null"):
                raise PermissionError("Trial writes outside request sandbox are forbidden")


sys.addaudithook(confined_writes)
sys.path.insert(0, os.environ["SAWA_TRIAL_SOURCE"])
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def sqlite_foreign_keys(connection, _record):
    import sqlite3
    if isinstance(connection, sqlite3.Connection):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Configure the owned SQLite file before starting concurrent business workers.
# Rollback journaling makes status readers block OCR commits; WAL keeps readers
# on a consistent snapshot while the single native writer commits its receipt.
import json
import sqlite3
if (os.environ.get("DB_URI") != "sqlite:///"+str(data / "database.sqlite3")
        or json.loads((data / "trial-manifest.json").read_text()).get("purpose") != "sawa-agent-isolated-trial"):
    raise RuntimeError("Owned trial database identity required")
with sqlite3.connect("file:"+str(data / "database.sqlite3")+"?mode=rw",uri=True,timeout=30) as database:
    if database.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
        raise RuntimeError("Trial SQLite WAL could not be enabled")

module_name, name = os.environ["SAWA_TRIAL_MODULE"].split(":")
app = getattr(importlib.import_module(module_name), name)


@app.middleware("http")
async def trial_identity_boundary(request, call_next):
    import hmac
    is_local_worker = os.environ["SAWA_TRIAL_MODULE"] == "service.main:app"
    worker_authorized = is_local_worker and hmac.compare_digest(request.headers.get("authorization", ""), "Bearer "+os.environ["SAWA_TRIAL_TOKEN"])
    if not worker_authorized and not hmac.compare_digest(request.headers.get("x-trial-token", ""), os.environ["SAWA_TRIAL_TOKEN"]):
        return JSONResponse({"detail": "Trial gateway required"}, status_code=401)
    if request.url.path == "/__trial_identity":
        return JSONResponse({"data": str(data)})
    return await call_next(request)


if __name__ == "__main__" and sys.argv[1] == "--initialize":
    # Same native imports and write confinement, but no business job is dispatched.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from experiments.agent_assistant.initialize_trial import initialize
    initialize(data)
    raise SystemExit(0)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), workers=1)
