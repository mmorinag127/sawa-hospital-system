"""Read-only terminal lineage; never an editable or publishable evidence body."""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, case, column, func, literal, select, true

from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun


@dataclass(frozen=True)
class TerminalEvidenceRead:
    id: str
    order_id: str
    template_version_id: str | None
    producer_version: str | None
    status: str
    execution: Any
    recognition_review: Any


def terminal_evidence_statement(dialect_name):
    table = OrderOcrEvidenceRun
    fields = (table.id, table.order_id, table.template_version_id, table.producer_version, table.status)
    if dialect_name != "postgresql":
        return select(*fields, table.payload_json["_ocr_execution"],
                      table.payload_json["hakodate_recognition_review"])
    payload = case((func.json_typeof(table.payload_json) == "object", table.payload_json),
                   else_=literal({}, type_=JSON))
    projection = func.json_to_record(payload).table_valued(
        column("_ocr_execution", JSON), column("hakodate_recognition_review", JSON),
    ).render_derived(with_types=True).lateral("terminal_fields")
    return select(*fields, projection.c._ocr_execution, projection.c.hakodate_recognition_review).select_from(
        table,
    ).join(projection, true())


def read_terminal_evidence(session, identities):
    result = {}
    ids = sorted(identities)
    for start in range(0, len(ids), 500):
        for row in session.execute(terminal_evidence_statement(session.get_bind().dialect.name).where(
                OrderOcrEvidenceRun.id.in_(ids[start:start + 500]))):
            result[row.id] = TerminalEvidenceRead(*row)
    return result
