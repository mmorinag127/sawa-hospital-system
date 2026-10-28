import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from src.services import order_workflow_v2_service as workflow


@pytest.mark.parametrize("fails", [False, True])
def test_inspection_sql_timing_preserves_result_or_exception_without_logging_sql(monkeypatch, fails):
    recorded = []
    monkeypatch.setattr(workflow.logger, "info", lambda _format, value: recorded.append(json.loads(value)))
    engine = create_engine("sqlite://")

    @workflow._measure_inspection
    def read():
        with engine.connect() as connection:
            return connection.exec_driver_sql("SELECT absent_private_value" if fails else "SELECT 7").scalar_one()

    try:
        if fails:
            with pytest.raises(OperationalError, match="absent_private_value"):
                read()
        else:
            assert read() == 7
        assert workflow._inspection_timing.get() is None
        assert len(recorded) == 1
        metric = recorded[0]["database_execute"]
        assert metric["count"] == 1
        assert metric["seconds"] >= 0
        assert "SELECT" not in json.dumps(recorded)
        assert "private" not in json.dumps(recorded)
    finally:
        engine.dispose()
