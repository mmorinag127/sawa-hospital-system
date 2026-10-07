"""Observe standard Alembic states in explicitly owned, initially empty databases.

The new postflight attributes intentionally do not match the older contract yet.
Snapshots are collected before that rejection; the database transaction rolls back.
This tool never substitutes a guard or stamps/adopts an existing database.
"""

import argparse
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

from src.maintenance.database_target import explicit_database_uri
from src.maintenance.formal_migration_schema import formal_revision_chain, read_catalog


def require_observation_role(connection, required_role, required_database):
    if not required_role or required_role != required_role.strip():
        raise RuntimeError("observation blocked: explicit required role is missing")
    user, database, owner = connection.exec_driver_sql(
        "SELECT current_user, current_database(), pg_get_userbyid(datdba) "
        "FROM pg_database WHERE datname=current_database()"
    ).one()
    if user != required_role:
        raise RuntimeError("observation blocked: connected role mismatch")
    if database != required_database:
        raise RuntimeError("observation blocked: connected database mismatch")
    if owner != required_role:
        raise RuntimeError("observation blocked: database owner mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owned-root", type=Path, required=True)
    parser.add_argument("--required-role", required=True)
    args = parser.parse_args()
    root = args.owned_root.resolve()
    assert args.output.resolve().is_relative_to(root)
    revisions = formal_revision_chain()
    target = sa.engine.make_url(explicit_database_uri(migration=True))
    if target.get_backend_name() == "sqlite":
        assert Path(target.database).resolve().is_relative_to(root)
    else:
        assert target.username == args.required_role
        assert target.drivername == "postgresql+psycopg2"
        assert target.host is None and target.port is None
        assert set(target.query) == {"host", "port"}
        assert target.database.startswith("c2_hospital_observe_")
        port = target.query["port"]
        major, directory = {"55482": (15, root / "pg15"), "55483": (16, root / "pg16")}[port]
        assert Path(target.query["host"]).resolve() == directory / "socket"
    engine = sa.create_engine(target)
    observed = {}
    try:
        with engine.connect() as connection:
            assert sa.inspect(connection).get_table_names() == []
            assert sa.inspect(connection).get_view_names() == []
            if engine.dialect.name == "postgresql":
                require_observation_role(connection, args.required_role, target.database)
                assert Path(connection.exec_driver_sql("SHOW data_directory").scalar_one()).resolve() == directory / "pgdata"
                assert connection.exec_driver_sql("SHOW port").scalar_one() == port
                assert connection.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
                assert Path(connection.exec_driver_sql("SHOW unix_socket_directories").scalar_one()).resolve() == directory / "socket"
                assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == major

        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith(("INSERT INTO alembic_version", "UPDATE alembic_version")):
                revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                catalog = read_catalog(connection)
                observed[revision] = catalog
                path = args.output / f"{engine.dialect.name}-{revision}.json"
                if path.exists():
                    raise RuntimeError(f"catalog blocked: observation already exists: {path.name}")
                args.output.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(catalog, sort_keys=True, default=str) + "\n")

        sa.event.listen(sa.engine.Engine, "after_cursor_execute", capture)
        try:
            try:
                command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
            except RuntimeError as exc:
                if not str(exc).startswith(("catalog blocked:", "catalog is partial")):
                    raise
                print("postflight rejected older contract:", str(exc))
        finally:
            sa.event.remove(sa.engine.Engine, "after_cursor_execute", capture)
        assert tuple(observed) == revisions[:len(observed)]
        if tuple(observed) != revisions:
            missing = revisions[len(observed)]
            print(f"stopped_at_uncontracted_revision={missing}")
        print(f"observed={len(observed)} bytes={sum(path.stat().st_size for path in args.output.glob('*.json'))}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
