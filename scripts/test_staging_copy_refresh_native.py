"""Own the complete local native test cluster; never attach to an existing database."""
from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
pg = Path(os.environ["REFRESH_TEST_PG_BIN"])
with tempfile.TemporaryDirectory(prefix="sawa-refresh-native-") as owned:
    directory = Path(owned)
    data = directory / "data"
    subprocess.run([str(pg / "initdb"), "-D", str(data), "-U", "refresh_native_test", "--auth=trust", "--locale=C", "--encoding=UTF8"], check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    started = False
    try:
        subprocess.run([str(pg / "pg_ctl"), "-D", str(data), "-l", str(directory / "postgres.log"), "-o", f"-h 127.0.0.1 -k {directory} -p {port}", "-w", "start"], check=True, capture_output=True)
        started = True
        environment = {**os.environ, "PATH": str(pg) + os.pathsep + os.environ["PATH"], "PYTHONPATH": str(root / "backend"),
                       "STAGING_REFRESH_NATIVE_URI": f"host=127.0.0.1 port={port} user=refresh_native_test dbname=postgres",
                       "DB_URI": "sqlite:///" + str(directory / "bootstrap.sqlite")}
        result = subprocess.run([sys.executable, "-m", "pytest", "backend/tests/unit/test_staging_copy_refresh_native.py", "-q"], cwd=root, env=environment)
    finally:
        if started:
            subprocess.run([str(pg / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], check=True, capture_output=True)
    raise SystemExit(result.returncode)
