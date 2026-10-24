import pytest
import sqlalchemy as sa

import c1_pg_ownership as owned


@pytest.fixture
def profile(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "pg"
    data, socket = root / "pgdata", root / "socket"
    data.mkdir(parents=True)
    socket.mkdir()
    for marker in (root / "OWNER", root.parent / "OWNER"):
        marker.write_text(owned.OWNER)
    (data / "PG_VERSION").write_text("16")
    (data / "postmaster.pid").write_text(f"12345\n{data}\n1\n55471\n{socket}\n")
    for key, value in {"ROOT": str(root), "PORT": "55471", "PID": "12345", "SOURCE": owned.OWNER}.items():
        monkeypatch.setenv("SAWA_OWNED_PG_" + key, value)
    return root, "55471"


def uri(profile, kind="menu"):
    root, port = profile
    return sa.URL.create("postgresql+psycopg2", username="mmorinag", database=owned.DATABASES[kind], query={"host": str(root / "socket"), "port": port})


@pytest.mark.parametrize("major", [15, 16])
@pytest.mark.parametrize("kind", ["menu", "migration", "live"])
def test_exact_additional_profile(profile, major, kind):
    (profile[0] / "pgdata/PG_VERSION").write_text(str(major))
    if major == 16:
        assert owned.additional_profile(uri(profile, kind), kind) == major
    else:
        with pytest.raises(AssertionError):
            owned.additional_profile(uri(profile, kind), kind)


@pytest.mark.parametrize("change", ["root", "port", "database", "admin", "owner", "tcp", "options", "driver", "user"])
def test_additional_profile_rejects_mismatch(profile, change):
    if change == "owner":
        (profile[0] / "OWNER").write_text("wrong-owner")
    url = uri(profile)
    if change == "root":
        url = url.update_query_dict({"host": "/private/tmp/not-owned/socket"})
    elif change == "port":
        url = url.update_query_dict({"port": "55469"})
    elif change in {"database", "admin"}:
        url = url.set(database="postgres" if change == "admin" else "c2_hospital_c1_menu_pg16_extra")
    elif change == "tcp":
        url = url.set(host="localhost")
    elif change == "options":
        url = url.update_query_dict({"options": "-csearch_path=public"})
    elif change == "driver":
        url = url.set(drivername="postgresql")
    elif change == "user":
        url = url.set(username="unowned")
    with pytest.raises(AssertionError):
        owned.additional_profile(url, "menu")


@pytest.mark.parametrize("kind,port,name", [("menu", "55437", "c1_menu_master"), ("migration", "55447", "c1_stg_migration"), ("live", "55447", "c1_live")])
def test_historical_profile_remains_separate(profile, kind, port, name):
    url = uri(profile).set(database=name).update_query_dict({"host": "/private/tmp/sawa-c1stg-pg.historical", "port": port})
    with pytest.raises(AssertionError):
        owned.additional_profile(url, kind)


@pytest.mark.parametrize("field", ["ROOT", "PORT", "PID", "SOURCE"])
def test_current_run_requires_explicit_ownership_identity(profile, monkeypatch, field):
    monkeypatch.delenv("SAWA_OWNED_PG_" + field)
    with pytest.raises(AssertionError):
        owned.additional_profile(uri(profile), "menu")


@pytest.mark.parametrize("field", [0, 1, 3, 4])
def test_current_run_refuses_postmaster_identity_change(profile, field):
    marker = profile[0] / "pgdata/postmaster.pid"
    values = marker.read_text().splitlines()
    values[field] = "67890" if field == 0 else "/foreign"
    marker.write_text("\n".join(values))
    with pytest.raises(AssertionError):
        owned.additional_profile(uri(profile), "menu")


@pytest.mark.parametrize("marker", ["OWNER", "pgdata/PG_VERSION", "pgdata/postmaster.pid"])
def test_current_run_refuses_aliased_ownership_markers(profile, marker):
    path = profile[0] / marker
    original = path.read_text()
    target = path.with_name(path.name + ".foreign")
    target.write_text(original)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(AssertionError):
        owned.additional_profile(uri(profile), "menu")


@pytest.mark.parametrize("attribute", ["data_directory", "port", "unix_socket_directories", "listen_addresses", "server_version_num", "current_user"])
def test_server_attribute_mismatch_stops(profile, attribute):
    root, port = profile
    values = {"data_directory": str(root / "pgdata"), "port": port, "unix_socket_directories": str(root / "socket"), "listen_addresses": "", "server_version_num": "160014", "current_user": "mmorinag"}
    values[attribute] = "150019" if attribute == "server_version_num" else "unexpected"

    class Connection:
        def execute(self, statement):
            value = values[str(statement).removeprefix("SHOW ").removeprefix("SELECT ")]
            class Result:
                def scalar_one(self):
                    return value
            return Result()

    with pytest.raises(AssertionError):
        owned.require_server(Connection(), 16)
