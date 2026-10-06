"""Exact additional C1 owned profiles; historical profiles remain in fixtures."""
from pathlib import Path

import sqlalchemy as sa

OWNER = str(Path(__file__).resolve().parents[3])
ROOT = Path("/private/tmp/sawa-c2-hospital-catalog-20261006")
PROFILES = {15: (ROOT / "pg15", "55469"), 16: (ROOT / "pg16", "55470")}
DATABASES = {kind: {major: f"c2_hospital_c1_{kind}_pg{major}" for major in PROFILES} for kind in ("menu", "migration", "live")}


def additional_profile(url, kind):
    host = url.query.get("host")
    if url.query.get("port") not in {p for _, p in PROFILES.values()} and host not in {str(root / "socket") for root, _ in PROFILES.values()}:
        return None
    assert kind in DATABASES
    assert url.drivername == "postgresql+psycopg2" and url.host is None and url.port is None
    assert url.username == "mmorinag" and url.password is None
    assert set(url.query) == {"host", "port"}
    major = next((major for major, (root, port) in PROFILES.items() if host == str(root / "socket") and url.query["port"] == port), None)
    assert major is not None, "C1 owned socket/port mismatch"
    root, _ = PROFILES[major]
    assert root.resolve() == root and Path(host).resolve() == root / "socket"
    assert (root / "OWNER").read_text().strip() == OWNER
    assert url.database == DATABASES[kind][major], "C1 exact owned database required"
    return major


def require_server(connection, major):
    root, port = PROFILES[major]
    actual = {key: connection.execute(sa.text(f"SHOW {key}")).scalar_one() for key in ("data_directory", "port", "unix_socket_directories", "listen_addresses", "server_version_num")}
    assert Path(actual["data_directory"]).resolve() == root / "pgdata"
    assert actual["port"] == port
    assert actual["unix_socket_directories"] == str(root / "socket")
    assert actual["listen_addresses"] == ""
    assert int(actual["server_version_num"]) // 10000 == major
