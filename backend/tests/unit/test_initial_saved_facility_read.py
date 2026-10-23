from contextlib import nullcontext
from copy import deepcopy

import pytest
from sqlalchemy import event, update

from test_initial_saved_source_service import saved, all_rows
from test_canonical_facility_lineage import authored
from src.models.order import Order
from src.services import canonical_facility_store as store, facility_master_service
from src.services import initial_saved_import_service as imports
from src.services import initial_saved_source_service as source
from src.services import order_service


@pytest.fixture
def imported_read(saved, monkeypatch):
    saved.execute(update(Order).where(Order.id == "O").values(
        week_code="2026-01@2026-01-01~2026-01-07"))
    saved.commit()
    snapshot = source.capture_saved_source(saved, "O")
    version = store.initial_facility_version(authored()["facility"], "a" * 64)
    master = imports.prepare_import_master(
        {"facilities": [{"facility_id": "F", "versions": [version]}]}, [snapshot], {})
    monkeypatch.setattr(facility_master_service, "get_snapshot",
                        lambda: store.MasterSnapshot(master, "test", "owned"))
    monkeypatch.setattr(order_service, "session_scope", lambda: nullcontext(saved))
    return saved, master


def test_imported_read_uses_exact_binding_without_materializing_or_writes(imported_read):
    session, _ = imported_read
    before = all_rows(session)
    binding = imports.get_binding(session, "O")
    statements = []
    def audit(_connection, _cursor, statement, *_args):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT")
    event.listen(session.bind, "before_cursor_execute", audit)
    try:
        result = order_service.get_order_facility_config("O")
        assert result == {"order_id": "O", "facility_id": "F", "template_version_id": "T",
                          "week_start": "2026-01-01", "week_end": "2026-01-07",
                          "source": "canonical_json", "config": binding["facility"]}
        assert order_service._read_order_authored_facility_config("O", "F") == binding["facility"]
        assert all_rows(session) == before
    finally:
        event.remove(session.bind, "before_cursor_execute", audit)
    assert statements


@pytest.mark.parametrize("change", ["source", "configuration", "foreign"])
def test_imported_read_never_substitutes_invalid_binding(imported_read, change):
    session, master = imported_read
    if change == "source":
        session.execute(update(Order).where(Order.id == "O").values(week_code="changed"))
    elif change == "configuration":
        configuration = next(iter(master[imports.CONFIGURATIONS].values()))
        configuration["policy"] = {"changed": True}
    else:
        with pytest.raises(ValueError, match="configured_execution_facility_mismatch"):
            order_service._read_order_authored_facility_config("O", "OTHER")
        return
    before = deepcopy(all_rows(session))
    with pytest.raises(ValueError, match="initial_saved_"):
        order_service.get_order_facility_config("O")
    assert all_rows(session) == before
