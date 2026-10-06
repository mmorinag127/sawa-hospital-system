from pathlib import Path

import pytest
import sqlalchemy as sa

import c1_pg_ownership as owned


def uri(major=16, kind="menu"):
    root, port = owned.PROFILES[major]
    return sa.URL.create("postgresql+psycopg2", username="mmorinag", database=owned.DATABASES[kind][major], query={"host": str(root / "socket"), "port": port})


@pytest.mark.parametrize("major", [15, 16])
@pytest.mark.parametrize("kind", ["menu", "migration", "live"])
def test_exact_additional_profile(major, kind, monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda p: owned.OWNER)
    assert owned.additional_profile(uri(major, kind), kind) == major


@pytest.mark.parametrize("change", ["root", "port", "database", "admin", "owner", "tcp", "options", "driver", "user"])
def test_additional_profile_rejects_mismatch(change, monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda p: "wrong-owner" if change == "owner" else owned.OWNER)
    url = uri()
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
def test_historical_profile_remains_separate(kind, port, name):
    url = uri().set(database=name).update_query_dict({"host": "/private/tmp/sawa-c1stg-pg.historical", "port": port})
    assert owned.additional_profile(url, kind) is None


@pytest.mark.parametrize("attribute", ["data_directory", "port", "unix_socket_directories", "listen_addresses", "server_version_num"])
def test_server_attribute_mismatch_stops(attribute):
    root, port = owned.PROFILES[16]
    values = {"data_directory": str(root / "pgdata"), "port": port, "unix_socket_directories": str(root / "socket"), "listen_addresses": "", "server_version_num": "160014"}
    values[attribute] = "150019" if attribute == "server_version_num" else "unexpected"

    class Connection:
        def execute(self, statement):
            value = values[str(statement).removeprefix("SHOW ")]
            class Result:
                def scalar_one(self):
                    return value
            return Result()

    with pytest.raises(AssertionError):
        owned.require_server(Connection(), 16)
