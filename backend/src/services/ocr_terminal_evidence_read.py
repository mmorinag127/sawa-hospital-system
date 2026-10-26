"""Read-only terminal lineage; never an editable or publishable evidence body."""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

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


def read_terminal_evidence(session, identities):
    result = {}
    ids = sorted(identities)
    for start in range(0, len(ids), 500):
        for row in session.execute(select(
            OrderOcrEvidenceRun.id, OrderOcrEvidenceRun.order_id,
            OrderOcrEvidenceRun.template_version_id, OrderOcrEvidenceRun.producer_version,
            OrderOcrEvidenceRun.status, OrderOcrEvidenceRun.payload_json["_ocr_execution"],
            OrderOcrEvidenceRun.payload_json["hakodate_recognition_review"],
        ).where(OrderOcrEvidenceRun.id.in_(ids[start:start + 500]))):
            result[row.id] = TerminalEvidenceRead(*row)
    return result
