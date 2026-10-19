"""Stream read-only history without retaining diagnostic archives or ORM rows."""

from types import SimpleNamespace

from sqlalchemy import select

from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun


def history_statement(order_id: str):
    table = OrderOcrEvidenceRun.__table__
    return select(*table.c).where(table.c.order_id == order_id).order_by(
        table.c.created_at.desc(), table.c.id.desc(),
    ).execution_options(yield_per=4)


def read_history(session, order_id: str) -> list[SimpleNamespace]:
    rows = []
    result = session.execute(history_statement(order_id))
    try:
        for source in result.mappings():
            values = dict(source)
            payload = values["payload_json"]
            if isinstance(payload, dict):
                values["payload_json"] = {
                    key: value for key, value in payload.items()
                    if key != "diagnostic_pipeline_output"
                }
            rows.append(SimpleNamespace(**values))
    finally:
        result.close()
    return rows
