"""Versioned HTTP intake mechanics with authored PDFs, not recognition proof."""
import pytest

from tests.contract.test_ingest import (upload_case, _registered_google_operator,
    _database_snapshot, hospital_operator)  # noqa: F401
from tests.contract import test_ingest as contract
from src.db import session_scope
from src.models.ingest_job import IngestJob


@pytest.mark.parametrize("query", ["intake_protocol=", "intake_protocol=v1", "intake_protocol=unknown",
    "intake_protocol=native_ingest_v2&intake_protocol=native_ingest_v2",
    "intake_protocol=unknown&intake_protocol=native_ingest_v2"])
@pytest.mark.parametrize("operation", ["upload", "retry"])
def test_invalid_query_rejected_before_storage_receipt_or_retry_mutation(upload_case, query, operation):
    case = upload_case()
    before = _database_snapshot()
    if operation == "upload":
        response = case.client.post("/ingest/upload?" + query, data=case.form,
            files=[("pdf_file", ("authored.pdf", case.pdf(), "application/pdf"))], headers=contract._operator_header())
    else:
        response = case.client.post("/ingest/uploads/explicitly-absent/retry?" + query,
            headers=contract._operator_header())
    assert response.status_code == 422
    assert response.json()["detail"] == "intake_protocol_invalid"
    assert _database_snapshot() == before


@pytest.mark.parametrize("split", [False, True])
def test_native_query_is_pinned_for_every_multi_or_split_receipt(upload_case, split):
    case = upload_case()
    files = [("pdf_files", ("authored.pdf", case.pdf(pages=2 if split else 1), "application/pdf"))]
    if not split:
        files.append(("pdf_files", ("other.pdf", case.pdf(label="other"), "application/pdf")))
    response = case.client.post("/ingest/upload?intake_protocol=native_ingest_v2",
        data={**case.form, "split_pages": str(split).lower()}, files=files, headers=contract._operator_header())
    assert response.status_code == 202, response.text
    items = response.json()["items"]
    assert len(items) == 2
    with session_scope() as session:
        for item in items:
            job = session.get(IngestJob, item["ingest_job_id"])
            assert job.payload["intake_protocol"] == "native_ingest_v2"
            assert job.payload["facility_selection"]["facility_id"] == case.form["facility_hint"]
