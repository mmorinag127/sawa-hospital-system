"""Real isolated CLI/SQLite boundary; not PostgreSQL or deployment proof."""

from datetime import datetime
from pathlib import Path
import subprocess
import sys

import sqlalchemy as sa

from src.models.menu import MenuMaster


ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "scripts" / "apply_menu_master_revision_migration.py"


def _invoke(tmp_path, *arguments, stdin=""):
    cwd = tmp_path / "foreign-cwd"
    home = tmp_path / "home"
    scratch = tmp_path / "scratch"
    for directory in (cwd, home, scratch):
        directory.mkdir(exist_ok=True)
    # No inherited PYTHONPATH, database configuration, credentials or proxy env.
    environment = {
        "HOME": str(home),
        "TMPDIR": str(scratch),
        "LANG": "C",
        "LC_ALL": "C",
    }
    return subprocess.run(
        [str(Path(sys.executable).absolute()), "-I", "-B", str(CLI), *arguments],
        cwd=cwd,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _sqlite_engine(tmp_path):
    path = tmp_path / "owned-menu.sqlite3"
    return sa.create_engine(sa.URL.create("sqlite", database=str(path)))


def test_direct_isolated_cli_help_imports_without_database(tmp_path):
    result = _invoke(tmp_path, "--help")
    assert result.returncode == 0, result.stderr
    assert "--db-uri-stdin" in result.stdout
    assert result.stderr == ""
    assert not (tmp_path / "owned-menu.sqlite3").exists()
    assert list((tmp_path / "foreign-cwd").iterdir()) == []


def test_owned_legacy_sqlite_first_and_second_migration_preserve_business_rows(tmp_path):
    engine = _sqlite_engine(tmp_path)
    try:
        metadata = sa.MetaData()
        columns = [
            sa.Column(
                column.name,
                column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
                unique=column.unique,
                index=column.index,
                default=column.default.arg if column.default is not None else None,
                onupdate=column.onupdate.arg if column.onupdate is not None else None,
                server_default=column.server_default,
            )
            for column in MenuMaster.__table__.columns
            if column.name != "revision"
        ]
        legacy = sa.Table("menu_masters", metadata, *columns)
        metadata.create_all(engine)
        rows = [
            {
                "id": "owned-zero-count",
                "name": " Authored count menu ",
                "normalized_name": "authored-count-menu",
                "unit_type": "count",
                "qty_per_serving": 0,
                "bag_max_qty": 7,
                "bag_max_unit": "count",
                "temp_type": "cold",
                "daypart": "lunch",
                "category": "Authored category",
                "condiments": [{"name": " Authored sauce ", "quantity": 0}, None],
                "updated_at": datetime(2020, 1, 2, 3, 4, 5, 123456),
            },
            {
                "id": "owned-null-gram",
                "name": "Authored gram menu",
                "normalized_name": "authored-gram-menu",
                "unit_type": "g",
                "qty_per_serving": None,
                "bag_max_qty": None,
                "bag_max_unit": None,
                "temp_type": None,
                "daypart": None,
                "category": None,
                "condiments": [],
                "updated_at": datetime(2021, 2, 3, 4, 5, 6),
            },
        ]
        with engine.begin() as connection:
            connection.execute(legacy.insert(), rows)
        with engine.connect() as connection:
            before = connection.execute(
                sa.select(legacy).order_by(legacy.c.id)
            ).mappings().all()
        assert len(before) == 2
        assert set(legacy.c.keys()) == set(MenuMaster.__table__.c.keys()) - {"revision"}
        assert "revision" not in {column["name"] for column in sa.inspect(engine).get_columns("menu_masters")}

        for attempt in (1, 2):
            result = _invoke(
                tmp_path, "--db-uri-stdin",
                stdin=engine.url.render_as_string(hide_password=False),
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == "0027 complete: revision schema applied or already valid\n"
            assert result.stderr == ""
            with engine.connect() as connection:
                after = connection.execute(
                    sa.select(legacy).order_by(legacy.c.id)
                ).mappings().all()
                revisions = connection.execute(
                    sa.text("SELECT revision FROM menu_masters ORDER BY id")
                ).scalars().all()
            assert after == before
            assert revisions == ([1, 1] if attempt == 1 else [1, 7])
            revision = next(
                column for column in sa.inspect(engine).get_columns("menu_masters")
                if column["name"] == "revision"
            )
            assert isinstance(revision["type"], sa.Integer)
            assert revision["nullable"] is False
            assert revision["default"] in ("1", "'1'")
            if attempt == 1:
                with engine.begin() as connection:
                    connection.execute(sa.text(
                        "UPDATE menu_masters SET revision=7 WHERE id='owned-zero-count'"
                    ))
        assert sa.inspect(engine).get_table_names() == ["menu_masters"]
    finally:
        engine.dispose()


def test_missing_owned_table_stops_without_creating_schema(tmp_path):
    engine = _sqlite_engine(tmp_path)
    try:
        with engine.connect() as connection:
            assert sa.inspect(connection).get_table_names() == []
        result = _invoke(
            tmp_path, "--db-uri-stdin",
            stdin=engine.url.render_as_string(hide_password=False),
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == "0027 blocked: menu_masters is missing; apply prior migrations first\n"
        assert sa.inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_malformed_stdin_uri_redacts_synthetic_secret_and_traceback(tmp_path):
    secret = "C0-SYNTHETIC-SECRET-SENTINEL"
    result = _invoke(tmp_path, "--db-uri-stdin", stdin="not-a-uri:" + secret)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "0027 failed: connection or migration error; URI and driver details suppressed\n"
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert list((tmp_path / "foreign-cwd").iterdir()) == []
    assert not (tmp_path / "owned-menu.sqlite3").exists()
