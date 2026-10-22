from pathlib import Path

import pytest

from sqlite_fixture_template import migrated_sqlite_template


@pytest.fixture(scope="session")
def formal_sqlite_template(tmp_path_factory):
    return migrated_sqlite_template(
        tmp_path_factory,
        alembic_ini=Path(__file__).resolve().parents[2] / "alembic.ini",
        name="formal-sqlite-schema",
    )
