"""Validate the physical PostgreSQL profile created by the current local run."""
import os
from pathlib import Path
import re

import sqlalchemy as sa

OWNER = str(Path(__file__).resolve().parents[3])
DATABASES = {
    "menu": "c1_menu_master", "migration": "c2_hospital_c1_migration_pg16",
    "live": "c2_hospital_c1_live_pg16", "bootstrap": "c2_hospital_c1_bootstrap_pg16",
    "identity": "postgres",
}


def _profile():
    value = os.environ.get("SAWA_OWNED_PG_ROOT")
    port = os.environ.get("SAWA_OWNED_PG_PORT", "")
    pid = os.environ.get("SAWA_OWNED_PG_PID", "")
    assert value and value == value.strip(), "explicit owned PG root required"
    assert os.environ.get("SAWA_OWNED_PG_SOURCE") == OWNER, "owned PG source mismatch"
    assert re.fullmatch(r"[1-9][0-9]{0,4}", port) and int(port) <= 65535
    assert re.fullmatch(r"[1-9][0-9]*", pid)
    root = Path(value)
    assert root.is_absolute() and root.is_dir() and not root.is_symlink()
    assert root.resolve() == root and root.stat().st_uid == os.getuid()
    socket, data = root / "socket", root / "pgdata"
    for directory in (socket, data):
        assert directory.is_dir() and not directory.is_symlink() and directory.resolve() == directory
        assert directory.stat().st_uid == os.getuid()
    for marker in (root / "OWNER", root.parent / "OWNER"):
        assert marker.is_file() and not marker.is_symlink()
        assert marker.read_text().strip() == OWNER, "owned PG OWNER mismatch"
    for marker in (data / "PG_VERSION", data / "postmaster.pid"):
        assert marker.is_file() and not marker.is_symlink()
        assert marker.stat().st_uid == os.getuid()
    assert (data / "PG_VERSION").read_text().strip() == "16"
    recorded = (data / "postmaster.pid").read_text().splitlines()
    assert len(recorded) >= 5 and recorded[0] == pid
    assert recorded[1] == str(data) and recorded[3] == port and recorded[4] == str(socket)
    return root, port


def additional_profile(url, kind):
    root, port = _profile()
    assert kind in DATABASES
    assert url.drivername == "postgresql+psycopg2" and url.host is None and url.port is None
    assert url.username == "mmorinag" and url.password is None
    assert set(url.query) == {"host", "port"}
    assert url.query["host"] == str(root / "socket") and url.query["port"] == port
    assert url.database == DATABASES[kind], "exact owned database required"
    return 16


def require_server(connection, major):
    root, port = _profile()
    assert major == 16
    actual = {key: connection.execute(sa.text(f"SHOW {key}")).scalar_one() for key in ("data_directory", "port", "unix_socket_directories", "listen_addresses", "server_version_num")}
    assert Path(actual["data_directory"]).resolve() == root / "pgdata"
    assert actual["port"] == port
    assert actual["unix_socket_directories"] == str(root / "socket")
    assert actual["listen_addresses"] == ""
    assert int(actual["server_version_num"]) // 10000 == major
    assert connection.execute(sa.text("SELECT current_user")).scalar_one() == "mmorinag"
