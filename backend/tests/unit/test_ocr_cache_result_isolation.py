"""Real owned producers and operator saves; external recognition only is doubled."""
from copy import deepcopy

import pytest

from src.db import get_engine, session_scope
from src.models.order_ocr_cache import OrderOcrCache
from src.services import order_service, order_workflow_v2_service as workflow
from tests.contract.auth_support import registered_hospital_operator
from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase
from tests.helpers.native_selection import produce_authored_native_result
from tests.unit.test_disabled_facility_columns import _snapshot


@pytest.fixture
def owned_cache_case(isolated_formal_schema, tmp_path, monkeypatch):
    with registered_hospital_operator(monkeypatch) as actor:
        case = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor)
        sheet = case.produce(cell_values="2")["sheet"]
        sheet["rows"][0][sheet["fields"].index("qty.regular_2f")] = "9"
        sheet["rows"][0][sheet["fields"].index("remarks")] = "operator-owned"
        archived, error = order_service.save_ocr_sheet_exact(case.id,
            fields=sheet["fields"], header=sheet["header"], rows=sheet["rows"],
            row_ids=sheet["row_ids"], ui_mode="sheet")
        assert error is None and archived is not None, error
        current, error = workflow.get_workflow_readonly(case.id)
        assert error is None and current is not None
        saved, error = workflow.save_sheet(order_id=case.id, sheet=sheet,
            edited_by=actor.user_id, expected_revision=current["revision"])
        assert error is None and saved is not None, error
        case.operator_sheet = deepcopy(sheet)
        yield case


@pytest.mark.parametrize("partial", [
    {"pages": [{"page_index": 1, "ocr_overlay_uri": "stored:unowned-overlay"}]},
    {"table_rows": [["01/08", "昼", "Menu A", "99", "unowned"]]},
    {"combined": {"old": "unowned-page"}, "engine": "retired-engine"},
    {"hakodate_target_cell_map": [{"sheet_cell": "E11", "value": "99"}]},
])
def test_partial_synchronous_cache_write_preserves_complete_source_and_operator_work(owned_cache_case, partial):
    case = owned_cache_case
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert captured is not None
    before = _snapshot(get_engine())
    pdf_bytes = case.pdf.read_bytes()
    def publish():
        return order_service._save_order_ocr_cache(case.id, deepcopy(partial),
            synchronous_write_input=captured, augment_hakodate_artifacts=False,
            persist_evidence=False, refresh_workflow=False)
    if "hakodate_target_cell_map" in partial:
        with pytest.raises(ValueError, match="hakodate_canonical_representation_missing"):
            publish()
    else:
        assert publish() is None
    assert _snapshot(get_engine()) == before
    assert case.pdf.read_bytes() == pdf_bytes
    sheet, error = order_service.get_ocr_sheet(case.id)
    assert error is None
    for key in ("fields", "header", "rows", "row_ids"):
        assert sheet[key] == case.operator_sheet[key]


def test_successor_producer_does_not_inherit_old_images_identity_or_failure_diagnostics(
    owned_cache_case, monkeypatch, tmp_path,
):
    case = owned_cache_case
    old_markers = {
        "pages": [{"page_index": 1, "ocr_overlay_uri": "stored:old-job-overlay"}],
        "combined": {"old_job": "stored:old-combined-image"},
        "engine": "retired-engine", "template_id": "unrelated-old-template",
        "facility_id": "unrelated-old-facility", "_reparse_debug": {"error": "old-job-failure"},
    }
    with session_scope() as session:
        cache = session.get(OrderOcrCache, case.id)
        cache.payload = {**deepcopy(cache.payload), **deepcopy(old_markers)}
        archive = deepcopy(cache.payload["_edited_ocr"])
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert captured is not None
    result = produce_authored_native_result(monkeypatch, order_id=case.id,
        table_rows=[["01/08", "昼", "Menu A", "7", ""]],
        columns=captured["configured_input"]["facility"]["fax_template"]["columns"],
        unlisted_cells="", artifact_root=tmp_path / "successor-artifacts")
    assert result["id"] != case.evidence
    with session_scope() as session:
        payload = deepcopy(session.get(OrderOcrCache, case.id).payload)
    for key, marker in old_markers.items():
        assert payload.get(key) != marker
    assert payload["_edited_ocr"] == archive
    assert payload["_ocr_execution"]["evidence_run_id"] == result["id"]
    current, error = workflow.get_workflow_readonly(case.id)
    assert error is None and current["selected_ocr_result_id"] == case.evidence
    sheet, error = order_service.get_ocr_sheet(case.id)
    assert error is None
    for key in ("fields", "header", "rows", "row_ids"):
        assert sheet[key] == case.operator_sheet[key]


def test_async_cache_cannot_publish_a_legacy_result_or_mirror_evidence(owned_cache_case, monkeypatch):
    case = owned_cache_case
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    before = _snapshot(get_engine())

    def forbidden(*args, **kwargs):
        pytest.fail("Incomplete output must be refused before publication or the write fence")

    from src.services import workflow_attempt_guard_service
    monkeypatch.setattr(workflow_attempt_guard_service, "processing_start_configured_input",
        lambda: deepcopy(captured["configured_input"]))
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", forbidden)
    monkeypatch.setattr(order_service, "persist_ocr_evidence_run", forbidden)
    for publish in (False, True):
        result = order_service._save_order_ocr_cache(case.id,
            {"table_rows": [["01/08", "昼", "Menu A", "99", ""]]},
            job_id="unowned-legacy-job", augment_hakodate_artifacts=False,
            persist_evidence=publish, refresh_workflow=False)
        assert result is None
        assert _snapshot(get_engine()) == before


def test_synchronous_owned_result_cannot_inject_an_operator_edit_archive(owned_cache_case):
    case = owned_cache_case
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert captured is not None
    existing = deepcopy(order_service._load_order_ocr_cache(case.id))
    original_archive = deepcopy(existing["_edited_ocr"])
    proposed = deepcopy(existing)
    proposed["_edited_ocr"] = {"latest": {"rows": [["provider-injected-99"]]}}
    before = _snapshot(get_engine())
    result = order_service._save_order_ocr_cache(case.id, proposed,
        synchronous_write_input=captured, augment_hakodate_artifacts=False,
        persist_evidence=False, refresh_workflow=False)
    assert result is None
    assert _snapshot(get_engine()) == before
    saved = order_service._load_order_ocr_cache(case.id)
    assert saved["_edited_ocr"] == original_archive
    for key, value in existing.items():
        if key != "_edited_ocr":
            assert saved[key] == value
    visible, error = order_service.get_ocr_sheet(case.id)
    assert error is None
    for key in ("fields", "header", "rows", "row_ids"):
        assert visible[key] == case.operator_sheet[key]


def test_valid_owned_cache_write_drops_injected_archive_and_retains_operator_nine(owned_cache_case):
    from tests.unit.test_h2_operator_repair_revision import (
        _owned_artifact_fixture, _attach_provider_artifact_packet,
    )
    case = owned_cache_case
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert captured is not None
    source = order_service._hakodate_owned_source(captured["configured_input"],
        captured["selected_pdf_receipt"], captured)
    targets, records = _owned_artifact_fixture(source)
    payload = {"hakodate_repair_source_receipt": source,
        "hakodate_canonical_pipeline": {"status": "ready",
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "expected_sheet_cells": source["expected_sheet_cells"]},
        "hakodate_preprocessing": {"target_cell_map": targets},
        "hakodate_ocr_evidence": {"records": records},
        "hakodate_ocr_evidence_records": deepcopy(records)}
    _attach_provider_artifact_packet(payload)
    order_service._validate_hakodate_owned_artifacts(payload, source)
    existing = deepcopy(order_service._load_order_ocr_cache(case.id))
    payload["_edited_ocr"] = {"latest": {"rows": [["provider-injected-99"]]}}
    original = deepcopy(payload)
    before = _snapshot(get_engine())
    assert order_service._save_order_ocr_cache(case.id, payload,
        synchronous_write_input=captured, augment_hakodate_artifacts=False,
        persist_evidence=False, refresh_workflow=False) == {}
    saved = order_service._load_order_ocr_cache(case.id)
    assert saved["_edited_ocr"] == existing["_edited_ocr"]
    assert saved["hakodate_ocr_evidence_records"] == records
    assert payload == original
    after = _snapshot(get_engine())
    assert all(after[table] == before[table] for table in before if table != "order_ocr_cache")
    visible, error = order_service.get_ocr_sheet(case.id)
    assert error is None
    for key in ("fields", "header", "rows", "row_ids"):
        assert visible[key] == case.operator_sheet[key]


def test_owned_low_confidence_numeric_one_still_has_a_native_page_preview(owned_cache_case, monkeypatch, tmp_path):
    case = owned_cache_case
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    result = produce_authored_native_result(monkeypatch, order_id=case.id,
        table_rows=[["01/08", "昼", "Menu A", "1", ""]],
        columns=captured["configured_input"]["facility"]["fax_template"]["columns"],
        unlisted_cells="", artifact_root=tmp_path / "low-confidence-native", recognizer_score=0.09)
    current, error = workflow.get_workflow_readonly(case.id)
    assert error is None
    selected, error = workflow.select_ocr_result(case.id, result["id"], expected_revision=current["revision"])
    assert error is None and selected is not None
    pages, error = order_service.get_ocr_pages(case.id, preview_only=True)
    assert error is None and pages["hakodate_overlay_status"] == "ready"
    assert pages["pages"][0]["hakodate_overlay_url"]
    sheet, error = order_service.get_ocr_sheet(case.id)
    assert error is None
    fields = sheet["fields"]
    row = next(row for row in sheet["rows"] if row[fields.index("menu")] == "Menu A")
    assert row[fields.index("qty.regular_2f")] == "1"
