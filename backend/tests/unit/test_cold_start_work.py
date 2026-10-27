"""Startup work retains projection correctness without per-facility reads."""
from sqlalchemy import event

from src import db
from src.models.facility import Facility
from src.services import config_service, facility_service


def test_facility_projection_reads_existing_rows_in_one_query(monkeypatch):
    rows = [{"facility_id": f"cold-start-{index}", "facility_name": f"Facility {index}"} for index in range(12)]
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {"facilities": rows})
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        assert facility_service.sync_facility_names_from_master() == len(rows)
        assert len(statements) == 1
        statements.clear()
        assert facility_service.sync_facility_names_from_master() == 0
        assert len(statements) == 1
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    with db.session_scope() as session:
        for row in rows:
            assert session.get(Facility, row["facility_id"]).name == row["facility_name"]
