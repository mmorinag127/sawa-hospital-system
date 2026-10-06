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
from src.maintenance.formal_migration_schema import read_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path("/private/tmp/sawa-c2-hospital-catalog-20261006").resolve()
    assert args.output.resolve().is_relative_to(root)
    target = sa.engine.make_url(explicit_database_uri(migration=True))
    if target.get_backend_name() == "sqlite":
        assert Path(target.database).resolve().is_relative_to(root)
    else:
        assert target.drivername == "postgresql+psycopg2"
        assert target.host is None and target.port is None
        assert set(target.query) == {"host", "port"}
        assert target.database.startswith("c2_hospital_observe_")
        port = target.query["port"]
        major, directory = {"55469": (15, root / "pg15"), "55470": (16, root / "pg16")}[port]
        assert Path(target.query["host"]).resolve() == directory / "socket"
    engine = sa.create_engine(target)
    observed = {}
    try:
        with engine.connect() as connection:
            assert sa.inspect(connection).get_table_names() == []
            assert sa.inspect(connection).get_view_names() == []
            if engine.dialect.name == "postgresql":
                assert Path(connection.exec_driver_sql("SHOW data_directory").scalar_one()).resolve() == directory / "pgdata"
                assert connection.exec_driver_sql("SHOW port").scalar_one() == port
                assert connection.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
                assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == major

        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith(("INSERT INTO alembic_version", "UPDATE alembic_version")):
                revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                observed[revision] = read_catalog(connection)

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
        assert set(observed) == {f"{number:04d}" for number in range(1, 29)}
        args.output.mkdir(parents=True, exist_ok=True)
        for revision, catalog in observed.items():
            path = args.output / f"{engine.dialect.name}-{revision}.json"
            assert not path.exists(), "existing observations must not be overwritten"
            path.write_text(json.dumps(catalog, sort_keys=True, default=str) + "\n")
        print(f"observed={len(observed)} bytes={sum(path.stat().st_size for path in args.output.glob('*.json'))}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
