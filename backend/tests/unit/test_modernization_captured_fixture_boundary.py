"""Owned JSON/projection capture component checks; no recognition-success proof."""
from datetime import datetime
from copy import deepcopy

import pytest
from sqlalchemy import event

from src.db import session_scope
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.services.workflow_attempt_guard_service import capture_reparse_read_input
from tests.helpers.captured_input_fixture import authored_input, PERIOD


@pytest.mark.parametrize("missing", [False, True])
def test_real_capture_keeps_source_or_stops_without_rebinding(isolated_formal_schema, monkeypatch, tmp_path, missing):
    configured = {"facility": {"fax_template": {"template_id": "own", "columns": []}, "zero": 0, "unknown": None},
                  "policy": {"quantity_rules": {"minimum": 0}}}
    projection_id, _ = authored_input(monkeypatch, tmp_path, configured, "fixture")
    with session_scope() as session:
        session.add(Order(id="own", facility_code="fixture", template_version_id=None if missing else projection_id,
                          week_code=PERIOD, document_uri=str(tmp_path / "own.pdf"), message_id="own-message", received_at=datetime(2026, 10, 7)))
        session.add(OrderWorkflowState(order_id="own", template_version_id=projection_id, state="context_confirmed"))
    writes = []
    def observe(_conn, _cursor, statement, _params, context, _many):
        if context.isinsert or context.isupdate or context.isdelete:
            writes.append(statement)
    event.listen(isolated_formal_schema, "before_cursor_execute", observe)
    try:
        with session_scope() as session:
            order = session.get(Order, "own")
            if missing:
                with pytest.raises(ValueError, match="original_template_projection_missing"):
                    capture_reparse_read_input(session, order)
                assert order.template_version_id is None
            else:
                actual = capture_reparse_read_input(session, order)
                assert actual["configured_input"] == deepcopy(configured)
                assert actual["configured_input"]["facility"]["unknown"] is None
                assert actual["configured_input"]["facility"]["zero"] == 0
        assert writes == []
    finally:
        event.remove(isolated_formal_schema, "before_cursor_execute", observe)
