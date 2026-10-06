from copy import deepcopy

import pytest
import sqlalchemy as sa

from src.db import session_scope
from src.models.facility import Facility
from src.services import facility_template_version_service as versions
from src.maintenance.formal_migration_schema import read_catalog


def _snapshot(engine):
    with engine.connect() as connection:
        rows = {}
        for name in sa.inspect(connection).get_table_names():
            table = sa.Table(name, sa.MetaData(), autoload_with=connection)
            rows[name] = sorted(repr(tuple(row)) for row in connection.execute(sa.select(table)))
        return read_catalog(connection), rows


@pytest.mark.parametrize("repetition", range(3))
def test_each_case_owns_empty_template_history(isolated_formal_schema, repetition):
    with session_scope() as session:
        assert session.execute(sa.text("SELECT count(*) FROM facility_template_versions")).scalar_one() == 0
        session.add(Facility(id="isolation-facility", name="Isolation Facility"))
    with session_scope() as session:
        assert session.get(Facility, "isolation-facility").name == "Isolation Facility"


def test_reachable_template_writer_history_collision_preserves_rows(isolated_formal_schema):
    config = {
        "fax_template_id": "explicit-template-A",
        "fax_template": {"columns": [
            {"index": 0, "role": "date", "header": "date", "source_index": 0},
            {"index": 1, "role": "daypart", "header": "daypart", "source_index": 1},
            {"index": 2, "role": "menu_name", "header": "menu", "source_index": 2},
            {"index": 3, "role": "quantity", "header": "quantity", "source_index": 3,
             "diet_type": "regular", "area_id": "X"},
        ]},
    }
    with session_scope() as session:
        session.add(Facility(id="history-facility", name="History Facility"))
    with session_scope() as session:
        assert versions.ensure_active_template_version_from_resolved_config(
            session, facility_id="history-facility", facility_config=config
        ) is not None
    changed = deepcopy(config)
    changed["fax_template_id"] = "explicit-template-B"
    with session_scope() as session:
        assert versions.ensure_active_template_version_from_resolved_config(
            session, facility_id="history-facility", facility_config=changed
        ) is not None
    before = _snapshot(isolated_formal_schema)
    # Characterize the reachable product defect, not a successful reactivation.
    with pytest.raises(sa.exc.IntegrityError):
        with session_scope() as session:
            versions.ensure_active_template_version_from_resolved_config(
                session, facility_id="history-facility", facility_config=config
            )
    assert _snapshot(isolated_formal_schema) == before
