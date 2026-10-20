"""Validate received output bytes against the current accepted order.

Workbook ZIP timestamps are not business content. Cell coordinates, values,
formulae and sheet identities are; CSV records are compared without coercion.
"""
import csv
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path

from openpyxl import load_workbook

from src.services import output_builder, order_workflow_v2_service


KINDS = {"labels", "delivery", "aggregate"}


def business_content(content: bytes, kind: str):
    if kind not in KINDS or not content:
        raise ValueError("output_file_invalid")
    try:
        if kind == "aggregate":
            return list(csv.reader(StringIO(content.decode("cp932"))))
        workbook = load_workbook(BytesIO(content), data_only=False)
        try:
            result = []
            for sheet in workbook:
                cells = []
                for row in sheet:
                    for cell in row:
                        if cell.value is not None:
                            value = cell.value
                            if isinstance(value, (datetime, date)):
                                value = value.isoformat()
                            cells.append((cell.coordinate, cell.data_type, value))
                result.append((sheet.title, cells))
            return result
        finally:
            workbook.close()
    except Exception as exc:
        raise ValueError("output_file_unreadable") from exc


def confirmed_identity(order_id: str):
    workflow, error = order_workflow_v2_service.get_workflow_readonly(order_id)
    if error or not workflow or workflow.get("state") != "confirmed" or not workflow.get("confirmed_snapshot_id"):
        raise ValueError("output_confirmed_order_required")
    return {"order_id": order_id, "revision": workflow["revision"],
            "confirmed_snapshot_id": workflow["confirmed_snapshot_id"]}


def validate_received_output(order_id: str, kind: str, content: bytes):
    identity = confirmed_identity(order_id)
    received = business_content(content, kind)
    if kind == "labels":
        path, _ = output_builder.build_label_workbook_for_order(order_id)
    else:
        paths = output_builder.build_output_preview(order_id, kind)
        path = paths["delivery_note" if kind == "delivery" else "aggregate"]
    expected = business_content(Path(path).read_bytes(), kind)
    if received != expected:
        raise ValueError("output_business_content_mismatch:" + kind)
    if confirmed_identity(order_id) != identity:
        raise ValueError("output_order_changed_during_validation")
    return {"valid": True, "kind": kind, "sha256": sha256(content).hexdigest(), **identity}
