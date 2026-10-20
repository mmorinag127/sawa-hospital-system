"""Borrow only within validation; ordinary extractor results remain detached."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.services import order_service as orders
from src.services import recognition_acceptance_service as acceptance
from src.services.canonical_ocr_artifact_service import _EvidenceAnalysis
from test_ocr_read_projection_ownership import packet_row  # noqa: F401
from test_recognition_review_acceptance import ten_payload


def outcome(call):
    try:
        return ("value", call())
    except Exception as exc:
        return ("error", type(exc), str(exc))


def test_default_ownership_and_explicit_borrow():
    payload = ten_payload()
    original = deepcopy(payload)
    borrowed = orders._extract_hakodate_ocr_evidence_records_from_payload(
        payload, order_id="", copy_records=False)
    assert borrowed is payload["hakodate_ocr_evidence"]["records"]
    first = orders._extract_hakodate_ocr_evidence_records_from_payload(payload, order_id="")
    second = _EvidenceAnalysis().evidence_records(payload, order_id="")
    first[0]["engine_metadata"]["recognizer_output"]["raw_text"] = "changed"
    assert second == original["hakodate_ocr_evidence"]["records"]
    assert payload == original


@pytest.mark.parametrize("fault", [None, "representation", "source", "identity", "review", "values", "blockers", "multiple"])
def test_review_parity_mutation_and_error_order(monkeypatch, fault):
    payload = ten_payload()
    values = {cell["sheet_cell"]: "12" for cell in payload["hakodate_recognition_review"]["cells"]}
    records = payload["hakodate_ocr_evidence"]["records"]
    if fault in ("representation", "multiple"):
        payload["hakodate_ocr_evidence_records"] = []
    if fault in ("source", "multiple"):
        records[0]["source_scope"] = "invalid"
    if fault in ("identity", "multiple"):
        records[0]["evidence_id"] = ""
    if fault in ("review", "multiple"):
        payload["hakodate_recognition_review"]["count"] = -1
    if fault in ("values", "multiple"):
        values = {}
    if fault in ("blockers", "multiple"):
        payload["hakodate_canonical_pipeline"]["blockers"].append("invalid")
    before = deepcopy(payload)
    real = orders._first_hakodate_payload_list
    calls = []

    def tracked(*args, **kwargs):
        calls.append(kwargs.get("copy_records", True))
        return real(*args, **kwargs)

    monkeypatch.setattr(orders, "_first_hakodate_payload_list", tracked)
    actual = outcome(lambda: acceptance.review_values(payload, values))
    assert calls == [False]
    assert payload == before

    def copying(*args, **kwargs):
        kwargs["copy_records"] = True
        return real(*args, **kwargs)

    with patch.object(orders, "_first_hakodate_payload_list", copying):
        expected = outcome(lambda: acceptance.review_values(payload, values))
    assert actual == expected
    assert payload == before
    if fault is None:
        actual[1]["cell-0"] = "changed"
        assert acceptance.review_values(payload, values)["cell-0"] == "12"
        assert payload == before
    else:
        assert actual[0] == "error"
    if fault == "multiple":
        assert actual[2] == "hakodate_own_artifact_representations_conflict"


@pytest.mark.parametrize("analysis_enabled", [False, True])
@pytest.mark.parametrize("fault", [None, "identity", "representations_and_identity"])
def test_owned_raw_borrow_keeps_checks_and_input(packet_row, monkeypatch, analysis_enabled, fault):  # noqa: F811
    row = packet_row("ready")
    payload = row.payload_json
    source = deepcopy(payload["hakodate_repair_source_receipt"])
    if fault:
        payload["hakodate_ocr_evidence"]["records"][0]["evidence_id"] = ""
        payload["hakodate_ocr_evidence_records"] = deepcopy(payload["hakodate_ocr_evidence"]["records"])
        if fault == "representations_and_identity":
            payload["hakodate_ocr_evidence_records"] = []
    before = deepcopy(payload)
    real = orders._first_hakodate_payload_list
    borrow_calls = []

    def tracked(*args, **kwargs):
        result = real(*args, **kwargs)
        if kwargs.get("copy_records") is False:
            assert result is payload["hakodate_ocr_evidence"]["records"]
            borrow_calls.append(result)
        return result

    monkeypatch.setattr(orders, "_first_hakodate_payload_list", tracked)

    def run():
        return orders._validate_hakodate_owned_artifacts(
            payload, source, _analysis=_EvidenceAnalysis() if analysis_enabled else None)

    actual = outcome(run)
    assert payload == before

    def copying(*args, **kwargs):
        kwargs["copy_records"] = True
        return real(*args, **kwargs)

    with patch.object(orders, "_first_hakodate_payload_list", copying):
        expected = outcome(run)
    assert actual == expected
    assert payload == before
    if fault is None:
        assert actual == ("value", None)
        assert len(borrow_calls) == 1
    else:
        assert actual[0] == "error"
        assert actual[2] == ("hakodate_own_record_identity_missing" if fault == "identity"
                             else "hakodate_own_artifact_representations_conflict")
