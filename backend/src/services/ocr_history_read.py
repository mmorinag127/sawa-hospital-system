"""Stream read-only history without retaining diagnostic archives or ORM rows."""

from types import SimpleNamespace

from sqlalchemy import select

from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun


def history_statement(order_id: str):
    table = OrderOcrEvidenceRun.__table__
    return select(*(column for column in table.c if column.computed is None)).where(table.c.order_id == order_id).order_by(
        table.c.created_at.desc(), table.c.id.desc(),
    ).execution_options(yield_per=4)


def read_history_summary(session, order_id: str) -> list[SimpleNamespace]:
    table = OrderOcrEvidenceRun.__table__
    columns = (
        "id", "order_id", "template_version_id", "schema_version",
        "producer_version", "source", "status", "artifact_digest", "created_at",
    )
    statement = select(*(table.c[name] for name in columns)).where(
        table.c.order_id == order_id,
    ).order_by(table.c.created_at.desc(), table.c.id.desc())
    result = session.execute(statement)
    try:
        return [SimpleNamespace(**dict(row)) for row in result.mappings()]
    finally:
        result.close()


def read_history_detail(session, order_id: str, evidence_id: str) -> SimpleNamespace | None:
    rows = _read_rows(
        session, history_statement(order_id).where(OrderOcrEvidenceRun.__table__.c.id == evidence_id),
    )
    return rows[0] if rows else None


def read_history(session, order_id: str) -> list[SimpleNamespace]:
    return _read_rows(session, history_statement(order_id))


def history_row_view(row: OrderOcrEvidenceRun) -> SimpleNamespace:
    values = {column.name: getattr(row, column.name) for column in OrderOcrEvidenceRun.__table__.columns if column.computed is None}
    return _history_view(values)


def _history_view(values):
    payload = values["payload_json"]
    if isinstance(payload, dict):
        values["payload_json"] = {key: value for key, value in payload.items() if key != "diagnostic_pipeline_output"}
    return SimpleNamespace(**values)


def _read_rows(session, statement) -> list[SimpleNamespace]:
    rows = []
    result = session.execute(statement)
    try:
        for source in result.mappings():
            rows.append(_history_view(dict(source)))
    finally:
        result.close()
    return rows
