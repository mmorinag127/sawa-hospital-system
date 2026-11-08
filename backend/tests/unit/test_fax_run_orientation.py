from types import SimpleNamespace

import pytest

from src.api import orders
from src.services import workflow_ocr_delivery_service as delivery


@pytest.mark.parametrize("mode", ["hakodate", "llm"])
def test_step1_api_passes_selected_rotation_and_document_to_guarded_queue(monkeypatch, mode):
    captured = []
    monkeypatch.setattr(orders.order_workflow_v2_service, "ensure_ocr_prerequisites",
                        lambda order_id, expected_revision: ({"revision": expected_revision + 1}, None))
    def enqueue(order_id, **kwargs):
        captured.append((order_id, kwargs))
        return {"accepted": True}
    monkeypatch.setattr(orders, "_enqueue_order_reparse_job", enqueue)
    monkeypatch.setattr(orders, "_enqueue_workflow_v2_evidence_rerun", enqueue)
    orders.run_order_workflow_v2_ocr("fixture-order", orders.WorkflowV2OcrRunBody(
        mode=mode, document_id="fixture-pdf", rotation_degrees=180 if mode == "hakodate" else None), expected_revision=7)
    assert len(captured) == 1
    assert captured[0][1]["rotation_degrees"] == (180 if mode == "hakodate" else None)
    assert captured[0][1]["selected_document_id"] == "fixture-pdf"
    assert captured[0][1]["expected_revision"] == 8


def test_llm_does_not_silently_accept_an_unapplied_hakodate_rotation():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        orders.run_order_workflow_v2_ocr("fixture-order", orders.WorkflowV2OcrRunBody(
            mode="llm", rotation_degrees=180), expected_revision=7)
    assert error.value.status_code == 422
    assert error.value.detail == "fax_rotation_requires_hakodate"


def identity(rotation):
    params = dict.fromkeys(delivery.REQUEST_KEYS)
    params["request_mode"] = "ocr_rerun"
    if rotation is not None: params["fax_rotation_degrees"] = rotation
    row = SimpleNamespace(id="fixture", order_id="fixture-order", job_id="fixture-job",
        workflow_attempt_id="fixture-attempt", workflow_revision=7, delivery_generation=1,
        task_name="projects/fixture/locations/fixture/queues/fixture/tasks/fixture-g1",
        request_mode="ocr_rerun", selected_pdf_receipt={
            "uploaded_pdf_id":"fixture-pdf", "document_id":"fixture-document", "order_id":"fixture-order",
            "message_id":"fixture-message", "storage_uri":"fixture://not-a-business-pdf", "sha256":"a" * 64},
        request_identity={"reparse_read_input":{"order":{"id":"fixture-order"},
            "configured_input":{"facility":"fixture"}, "physical_menu_entries":[]},
            "selected_version_input":None, "selected_document_id":"fixture-document",
            "first_pass_source":None, "request_parameters":params})
    row.task_contract = {"name": row.task_name}
    row.identity_sha256 = delivery._digest(delivery._identity(row))
    return row


@pytest.mark.parametrize("rotation", [None, 0, 180])
def test_rotation_is_source_bound_without_breaking_preexisting_deliveries(rotation):
    row = identity(rotation)
    delivery._validate_identity(row)
    row.request_identity["request_parameters"]["fax_rotation_degrees"] = 0 if rotation == 180 else 180
    with pytest.raises(ValueError, match="identity_digest_mismatch"):
        delivery._validate_identity(row)


@pytest.mark.parametrize("rotation", [90, -180, "180", True])
def test_queue_rejects_invalid_rotation_even_with_a_matching_digest(rotation):
    with pytest.raises(ValueError, match="rotation_invalid"):
        delivery._validate_identity(identity(rotation))
