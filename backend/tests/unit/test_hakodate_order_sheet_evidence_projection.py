from types import SimpleNamespace

import pytest

from src.services import order_service
from tests.helpers.repository_layout_source import repository_layout_source  # noqa: F401


def _component_projection_config():
    """Explicit pure projection schema; not an owned OCR receipt or live source."""
    return {"facility_id": "FAC_TEST", "facility_name": "Synthetic projection component",
        "areas": [{"area_id": "X", "name": "Explicit component area"}],
        "fax_template_id": "tpl-1", "fax_template": {"template_id": "tpl-1", "columns": [
            {"index": 0, "source_index": 0, "name": "date_mmdd", "role": "date"},
            {"index": 1, "source_index": 1, "name": "daypart", "role": "daypart"},
            {"index": 2, "source_index": 2, "name": "menu", "role": "menu_name"},
            {"index": 3, "source_index": 3, "name": "qty.regular_x", "role": "quantity",
             "diet_type": "regular", "area_id": "X"}]}}


def test_sheet_header_from_template_keeps_header_group_for_area_quantity_columns() -> None:
    template = {
        "columns": [
            {"index": 0, "role": "date", "header": "日付", "name": "date_mmdd"},
            {"index": 1, "role": "daypart", "header": "区分", "name": "daypart"},
            {"index": 2, "role": "menu", "header": "メニュー", "name": "menu"},
            {
                "index": 3,
                "role": "quantity",
                "header_group": "常食",
                "header": "月",
                "name": "qty.regular_3f",
                "diet_type": "regular",
                "area_id": "3F",
            },
            {
                "index": 4,
                "role": "quantity",
                "header_group": "軟菜",
                "header": "花",
                "name": "qty.soft_2f",
                "diet_type": "soft",
                "area_id": "2F",
            },
            {
                "index": 5,
                "role": "quantity",
                "header_group": "禁食",
                "header": "肉禁",
                "name": "qty.no_meat_x",
                "diet_type": "no_meat",
                "area_id": "X",
            },
            {
                "index": 6,
                "role": "quantity",
                "header_super_group": "禁食、魚",
                "header_group": "常食",
                "header": "花",
                "name": "qty.no_fish_regular_2f",
                "diet_type": "no_fish_regular",
                "area_id": "2F",
            },
        ],
    }

    header = order_service._sheet_header_from_template(  # noqa: SLF001
        [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.no_meat_x",
            "qty.no_fish_regular_2f",
        ],
        template,
    )

    assert header == ["日付", "区分", "メニュー", "常食月", "軟菜花", "肉禁", "禁食、魚 常食花"]


def test_hakodate_canonical_payload_reads_digit_evidence_from_best_method_records(projection_case, monkeypatch) -> None:
    payload, records = _canonical_component_records(
        projection_case, monkeypatch, raw="１２", predicted="12",
    )

    pipeline = payload["hakodate_canonical_pipeline"]
    assert pipeline["status"] == "ready"
    assert pipeline["blockers"] == []
    assert pipeline["target_cell_count"] == 1
    assert pipeline["evidence_record_count"] == 1
    assert pipeline["assigned_target_count"] == 1
    evidence = payload["hakodate_ocr_evidence_records"][0]
    assert evidence["raw_text"] == "１２"
    assert evidence["normalized_value"] == "１２"
    assert evidence["source_scope"] == "hakodate_cell_crop_batch"
    assert evidence["engine_metadata"]["source_artifact"] == "best_method_records"
    original = evidence["engine_metadata"]["recognizer_output"]
    assert original == records[0]
    assert original["raw_text"] == "１２"
    assert original["pred_digits"] == "12"
    assert payload["hakodate_preprocessing"]["target_cell_map"][0]["metadata"]["ocr_text"] == ""


def test_hakodate_canonical_payload_preserves_raw_nondigit_separately_from_topk_candidate(projection_case, monkeypatch) -> None:
    from src.services import hakodate_ocr_evidence_service as evidence_service

    payload, records = _canonical_component_records(
        projection_case, monkeypatch, raw="-", predicted="1",
        accepted_candidate={"normalized_digits": "1", "score": 0.099},
    )

    evidence = payload["hakodate_ocr_evidence_records"][0]
    assert evidence["raw_text"] == "-"
    assert evidence["normalized_value"] == "-"
    assert evidence["confidence"] == 0.099
    original = evidence["engine_metadata"]["recognizer_output"]
    assert original == records[0]
    assert original["raw_text"] == "-"
    assert original["pred_digits"] == "1"
    assert original["recognizer_score"] == 0.473
    assert original["recognizer_accepted_candidate"] == {"normalized_digits": "1", "score": 0.099}
    assert original["recognizer_decision_source"] == "topk_digits"
    pipeline = payload["hakodate_canonical_pipeline"]
    assert pipeline["status"] == "ready"
    assert pipeline["blockers"] == []
    assert pipeline["evidence_record_count"] == 1
    assert pipeline["assigned_target_count"] == 1
    assigned = evidence_service.assign_evidence_to_target_cells(
        evidence_records=payload["hakodate_ocr_evidence_records"],
        target_cells=payload["hakodate_preprocessing"]["target_cell_map"],
    )
    output = evidence_service.sheet_output_from_assigned_results(
        assignments=assigned["assignments"], blockers=assigned["blockers"],
    )
    cell = output["cells"][records[0]["sheet_cell"]]
    assert cell["raw_texts"] == ["-"]
    assert cell["value_normalized"] == "-"
    assert cell["assignment_state"] == "assigned"
    assert output["blockers"] == []


def test_hakodate_evidence_assignment_blocks_without_new_payload_contract() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        facility_config=_component_projection_config(),
        payload={"tables": [{"rows": [["legacy", "ocr"]]}]},
    )

    assert assignment["assignment_mode"] == "ocr_evidence"
    assert assignment["status"] == "blocked"
    assert assignment["blockers"] == [
        "hakodate_ocr_evidence_missing",
        "hakodate_target_cell_map_missing",
    ]
    assert assignment["assignments"] == []


def test_hakodate_overlay_source_requires_captured_selected_evidence(monkeypatch) -> None:
    latest_payload = {
        "hakodate_canonical_pipeline": {
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
        },
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "bbox": [10, 10, 30, 30],
                }
            ]
        },
        "hakodate_ocr_evidence_records": [
            {"text": "7", "center": [20, 20], "confidence": 0.9}
        ],
    }
    monkeypatch.setattr(
        order_service,
        "get_latest_ocr_evidence_run",
        lambda *_args, **_kwargs: pytest.fail("unselected latest evidence must not become the source"),
    )
    monkeypatch.setattr(
        order_service,
        "_load_order_ocr_cache",
        lambda *_args, **_kwargs: pytest.fail("legacy cache must not become the source"),
    )
    monkeypatch.setattr(order_service, "_capture_synchronous_cache_write_input", lambda _order_id: None)
    assert order_service._load_latest_hakodate_overlay_source("ORD_TEST") == ({}, None, False)  # noqa: SLF001
    assert latest_payload["hakodate_ocr_evidence_records"][0]["text"] == "7"


def test_hakodate_overlay_preview_refuses_unowned_latest_evidence_and_cache(monkeypatch, repository_layout_source) -> None:
    legacy_payload = {
        "hakodate_overlay": {
            "uri": "gs://bucket/legacy-overlay.png",
            "fingerprint": "legacy-fingerprint",
            "producer": "hakodate_best_method_pipeline",
        }
    }
    monkeypatch.setattr(
        order_service,
        "get_latest_ocr_evidence_run",
        lambda *args, **kwargs: pytest.fail("unowned latest evidence was consulted"),
    )
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: legacy_payload)
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}")
    monkeypatch.setattr(
        order_service,
        "get_cached_hakodate_assignment_preview",
        lambda _order_id: {"status": "auto_assignable", "blockers": []},
    )

    preview = order_service.get_cached_hakodate_overlay_preview("ORD_TEST")

    assert preview["status"] == "blocked"
    assert preview["blockers"] == ["synchronous_input_missing"]
    assert preview["overlay_uri"] is None and preview["overlay_url"] is None
    assert preview.get("source_evidence_run_id") is None


def test_hakodate_overlay_preview_refuses_unowned_versionless_evidence_and_cache(monkeypatch, repository_layout_source) -> None:
    stale_cache_payload = {
        "hakodate_overlay": {
            "uri": "gs://bucket/stale-cache-overlay.png",
            "fingerprint": "stale-cache-fingerprint",
            "producer": "hakodate_best_method_pipeline",
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
        },
        "hakodate_assignment_preview": {
            "fingerprint": "stale-cache-fingerprint",
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "assignment": {"status": "auto_assignable", "blockers": []},
        },
    }
    monkeypatch.setattr(
        order_service,
        "get_latest_ocr_evidence_run",
        lambda *args, **kwargs: pytest.fail("unowned versionless evidence was consulted"),
    )
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: stale_cache_payload)
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}")
    monkeypatch.setattr(
        order_service,
        "get_cached_hakodate_assignment_preview",
        lambda _order_id: {"status": "auto_assignable", "blockers": []},
    )

    preview = order_service.get_cached_hakodate_overlay_preview("ORD_TEST")

    assert preview["status"] == "blocked"
    assert preview["blockers"] == ["synchronous_input_missing"]
    assert preview["overlay_uri"] is None and preview["overlay_url"] is None
    assert preview.get("source_evidence_run_id") is None


def test_hakodate_current_draft_cannot_rebuild_without_captured_input(monkeypatch) -> None:
    stale_draft = {"draft_sheet_json": {"source": "weekly_menu", "rows": []}}
    rebuilt_draft = {
        "draft_sheet_json": {
            "source": "hakodate_ocr_evidence_sheet",
            "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
            "rows": [["04/26", "朝", "献立A", "7"]],
        }
    }
    calls: list[str] = []
    monkeypatch.setattr(
        order_service,
        "get_latest_sheet_draft",
        lambda _order_id, **_kwargs: stale_draft,
    )

    def fake_switch(order_id: str, *, edited_by: str | None = None):
        calls.append(f"{order_id}:{edited_by}")
        return rebuilt_draft, None

    monkeypatch.setattr(order_service, "switch_draft_to_latest_evidence", fake_switch)

    draft, error = order_service.ensure_hakodate_evidence_draft_current(
        "ORD_TEST",
        edited_by="unit-test",
    )

    assert error == "synchronous_input_missing"
    assert draft is None
    assert calls == []
    assert stale_draft["draft_sheet_json"]["source"] == "weekly_menu"
    assert rebuilt_draft["draft_sheet_json"]["rows"][0][-1] == "7"


def test_hakodate_current_draft_cannot_borrow_projection_without_captured_input(monkeypatch) -> None:
    current_draft = {
        "id": "ODR_CURRENT",
        "draft_sheet_json": {
            "source": "hakodate_ocr_evidence_sheet",
            "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
            "rows": [["04/26", "朝", "献立A", "7"]],
        },
    }
    monkeypatch.setattr(
        order_service,
        "get_latest_sheet_draft",
        lambda _order_id, **_kwargs: current_draft,
    )

    def fail_switch(*_args, **_kwargs):
        raise AssertionError("existing Hakodate projection draft must not be rebuilt on read")

    monkeypatch.setattr(order_service, "switch_draft_to_latest_evidence", fail_switch)

    draft, error = order_service.ensure_hakodate_evidence_draft_current(
        "ORD_TEST",
        edited_by="unit-test",
    )

    assert error == "synchronous_input_missing"
    assert draft is None
    assert current_draft["draft_sheet_json"]["rows"][0][-1] == "7"


def test_hakodate_projection_draft_is_authoritative_and_not_semantic_rebased(monkeypatch) -> None:
    current_draft = {
        "id": "ODR_CURRENT",
        "draft_sheet_json": {
            "source": "hakodate_ocr_evidence_sheet",
            "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
            "rows": [["04/26", "朝", "献立A", "7"]],
            "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
        },
        "edited_by": "auto-hakodate-evidence-ocr-sheet",
    }

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("Hakodate evidence sheet must not be semantically rebased")

    monkeypatch.setattr(order_service, "_build_fresh_semantic_sheet_for_draft_rebase", fail_rebuild)

    assert order_service._draft_record_is_authoritative_current_sheet(current_draft) is True  # noqa: SLF001
    rebase_required, rebuilt = order_service._draft_record_requires_current_sheet_semantic_rebase(  # noqa: SLF001
        "ORD_TEST",
        current_draft,
    )

    assert rebase_required is False
    assert rebuilt is None


@pytest.fixture
def projection_case(isolated_formal_schema, tmp_path, monkeypatch):
    from datetime import date, datetime
    from tests.contract.auth_support import registered_hospital_operator
    from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase, _line

    columns = _component_projection_config()["fax_template"]["columns"]
    with registered_hospital_operator(monkeypatch) as actor:
        yield HistoryCase(
            isolated_formal_schema, tmp_path, monkeypatch, actor,
            week="2026-04@2026-04-26~2026-04-30",
            period="2026-04@2026-04-26~2026-04-30", facility="FAC_TEST",
            received=datetime(2026, 4, 26, 9),
            lines=[_line("2026-04-26", "献立A", 12, daypart="朝", area="X")],
            columns=columns,
            menu_entries=[{"id": "projection-april-a", "monthly_menu_id": "2026-04",
                "menu_date": date(2026, 4, 26), "daypart": "朝", "name": "献立A",
                "slot_index": 0, "diet_type": "regular", "category": "副菜"}],
        )


def _projection_capture(case):
    from src.db import session_scope
    from src.models.order import Order
    from src.models.order_workflow_state import OrderWorkflowState
    from src.services import order_workflow_v2_service as workflow

    with session_scope() as session:
        return workflow._axis_source_receipt(
            session, session.get(Order, case.id), session.get(OrderWorkflowState, case.id)
        )


def _canonical_component_records(case, monkeypatch, *, raw, predicted, accepted_candidate=None):
    from copy import deepcopy
    from tests.unit.test_h2_operator_repair_revision import _provider_output_files

    # Reuse actual registration/capture/queue/claim and its external render leaf.
    # Replace only the external recognizer response; no producer or guard mock.
    case.prepare_provider(cell_values=raw)
    receipt, captured = _projection_capture(case)
    observed = []

    def recognize(*, item, output_dir, **_kwargs):
        assert item["_verified_pdf_bytes"] == case.pdf.read_bytes()
        semantics = item["original_target_semantics"]
        assert len(semantics) == 1
        regions = [{**deepcopy(target), "bbox": [10, 10, 30, 30],
                    "ocr_text": "" if accepted_candidate is None else raw,
                    "ocr_normalized": "" if accepted_candidate is None else predicted}
                   for target in semantics]
        records = [{**deepcopy(target), "bbox": [10, 10, 30, 30],
                    "raw_text": raw, "pred_digits": predicted, "recognizer_score": 0.473}
                   for target in semantics]
        if accepted_candidate is not None:
            for record in records:
                record.update(recognizer_decision_source="topk_digits",
                              recognizer_accepted_candidate=deepcopy(accepted_candidate))
        observed.extend(deepcopy(records))
        outputs = _provider_output_files(output_dir, records=records, regions=regions,
                                        sheet_values={target["sheet_cell"]: raw for target in semantics})
        return SimpleNamespace(
            metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
            expected_sheet_cells=tuple(item["original_expected_sheet_cells"]),
            outputs=outputs, ocr_engine="component-external-recognizer",
        ), None

    monkeypatch.setattr(order_service.hakodate_cell_ocr_batch_service,
                        "build_hakodate_best_method_for_manifest_item", recognize)
    with case.execution():
        payload = order_service._hakodate_canonical_payload_from_manifest_item(
            order_id=case.id, facility_id=case.facility,
            item={"source": "live_order_facility_source_workbook"},
            document_uri_override=receipt["selected_pdf_receipt"]["storage_uri"],
            pdf_bytes_override=case.pdf.read_bytes(),
            configured_input=captured["configured_input"],
        )
    assert len(observed) == 1
    return payload, observed


def _component_assignment(*, payload, **identity):
    from copy import deepcopy
    from src.services.hakodate_ocr_evidence_service import evidence_from_records
    assert "hakodate_ocr_evidence" not in payload
    source = deepcopy(payload)
    records = evidence_from_records(source["hakodate_ocr_evidence_records"],
        run_id="declared-projection-component", engine="component-external-recognizer",
        source_scope="hakodate_cell_crop_batch", retain_empty_records=True)
    source["hakodate_ocr_evidence"] = {"records": records}
    source["hakodate_ocr_evidence_records"] = deepcopy(records)
    return order_service._build_hakodate_evidence_assignment_from_payload(payload=source, **identity)


def _declared_projection_assignment(regions, records, config):
    # Authored component inputs, not selected evidence or an owned artifact receipt.
    return _component_assignment(
        order_id="ORD_TEST", facility_id="FAC_TEST", template_id="tpl-1",
        facility_config=config, payload={"hakodate_preprocessing": {"target_cell_map": regions},
                                        "hakodate_ocr_evidence_records": records},
    )


def _shared_projection_assignment(*, diabetes=False):
    from copy import deepcopy

    config = deepcopy(_component_projection_config())
    config["fax_template"]["body_merge_policy"] = {
        "mode": "daypart", "columns": ["qty.regular_x"], "required": True,
    }
    logical = [{"sheet_cell": f"E{row}", "worksheet_row": row, "worksheet_col": 5,
                "field": "qty.regular_x", "role": "quantity", "date": "2026-04-26",
                "daypart": "朝", "menu_name": menu}
               for row, menu in [(11, "献立A"), (12, "献立B")]]
    regions = [{"target_cell_id": "E11:E12", "sheet_cell": "E11",
        "worksheet_row": 11, "worksheet_col": 5, "semantic_field": "qty.regular_x",
        "role": "quantity", "bbox": [10, 10, 30, 30], "merged_cell": True,
        "covered_sheet_cells": ["E11", "E12"], "logical_targets": logical}]
    records = [{"text": "44", "center": [20, 20], "confidence": 0.9}]
    if diabetes:
        config["fax_template"]["columns"].append({
            "name": "qty.diabetes_x", "role": "quantity", "index": 4, "source_index": 4,
            "diet_type": "diabetes", "area_id": "X",
        })
        regions.append({"target_cell_id": "F11", "sheet_cell": "F11",
            "worksheet_row": 11, "worksheet_col": 6, "semantic_field": "qty.diabetes_x",
            "role": "quantity", "bbox": [40, 10, 60, 30],
            "logical_targets": [{"sheet_cell": "F11", "worksheet_row": 11, "worksheet_col": 6,
                "field": "qty.diabetes_x", "role": "quantity", "date": "2026-04-26",
                "daypart": "朝", "menu_name": "献立A"}]})
        records.append({"text": "5", "center": [50, 20], "confidence": 0.9})
    assignment = _declared_projection_assignment(regions, records, config)
    assert assignment["status"] == "auto_assignable"
    return assignment, config


def test_hakodate_payload_augmentation_uses_live_canonical_payload_not_legacy_ocr_sources(projection_case) -> None:
    case = projection_case
    payload = {
        "grid_column_edges": [9.0, 9.2, 9.4, 9.6, 9.8],
        "grid_row_edges": [9.0, 9.2, 9.4],
        "table_rows": [["4/26", "朝", "旧OCR献立", "99"]],
        "tables": [{"rows": [["4/26", "朝", "旧OCR献立", "99"]],
                    "cells": [{"row_index": 1, "col_index": 3, "text": "９９",
                               "bbox": [9.6, 9.2, 9.8, 9.4]}]}],
        "tokens": [{"text": "９９", "bbox": [9.6, 9.2, 9.8, 9.4], "x": 9.7, "y": 9.3}],
    }
    # Only external render/recognition leaves are doubled by HistoryCase.
    calls = case.prepare_provider(cell_values="１２")
    receipt, start = _projection_capture(case)
    template = {**start["configured_input"]["facility"]["fax_template"],
                "quantity_assignment_strategy": "hakodate"}
    with case.execution():
        augmented = order_service._augment_hakodate_ocr_payload_artifacts(
            order_id=case.id, payload=payload, template=template,
            selected_pdf_receipt=receipt["selected_pdf_receipt"],
            configured_input=start["configured_input"],
        )

    assert len(calls) == 1
    assert augmented["hakodate_canonical_pipeline"]["version"] == order_service.HAKODATE_CANONICAL_PIPELINE_VERSION
    target_cells = augmented["hakodate_preprocessing"]["target_cell_map"]
    evidence_records = augmented["hakodate_ocr_evidence_records"]
    assert target_cells
    assert all(target["semantic_field"] == "qty.regular_x" for target in target_cells)
    logical_targets = [logical for target in target_cells for logical in target["metadata"]["logical_targets"]]
    assert logical_targets
    assert all(logical["menu_name"] == "献立A" for logical in logical_targets)
    assert all(logical["date"] in {"04/26", "2026-04-26"} for logical in logical_targets)
    # Geometry comes from this declared physical producer, never legacy table/tokens.
    assert [target["bbox"] for target in target_cells] == [region["bbox"] for region in calls[0]]
    assert all(record["text"] == "１２" for record in evidence_records)
    assert all(record["normalized_value"] == "１２" for record in evidence_records)
    assert all(record["source_scope"] == "hakodate_cell_crop_batch" for record in evidence_records)
    assert [record["center"] for record in evidence_records] == [
        [(region["bbox"][0] + region["bbox"][2]) / 2,
         (region["bbox"][1] + region["bbox"][3]) / 2] for region in calls[0]
    ]
    assert "99" not in str(target_cells)
    assert "９９" not in str(evidence_records)
    assert payload["table_rows"] == [["4/26", "朝", "旧OCR献立", "99"]]


def test_hakodate_payload_augmentation_regenerates_stale_pipeline_payload(projection_case) -> None:
    case = projection_case
    payload = {
        "hakodate_canonical_pipeline": {
            "version": "old-hakodate-pipeline",
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
        },
        "hakodate_preprocessing": {"target_cell_map": [{"target_cell_id": "old", "sheet_cell": "D11"}]},
        "hakodate_ocr_evidence_records": [{"evidence_id": "old-ev", "text": "9",
            "normalized_value": "9", "source_scope": "hakodate_cell_crop_batch"}],
    }
    calls = case.prepare_provider(cell_values="7")
    receipt, start = _projection_capture(case)
    template = start["configured_input"]["facility"]["fax_template"]
    with case.execution():
        refused = order_service._augment_hakodate_ocr_payload_artifacts(
            order_id=case.id, payload=payload,
            template={**template, "quantity_assignment_strategy": "hakodate"},
        )
        assert refused["hakodate_canonical_pipeline"]["blockers"] == ["hakodate_canonical_version_required"]
        assert calls == []
        augmented = order_service._augment_hakodate_ocr_payload_artifacts(
            order_id=case.id, payload=payload, template=template, force_hakodate=True,
            selected_pdf_receipt=receipt["selected_pdf_receipt"],
            configured_input=start["configured_input"],
        )
    assert len(calls) == 1
    assert augmented["hakodate_canonical_pipeline"]["version"] == order_service.HAKODATE_CANONICAL_PIPELINE_VERSION
    assert augmented["hakodate_preprocessing"]["target_cell_map"]
    assert all(target["target_cell_id"] != "old" for target in augmented["hakodate_preprocessing"]["target_cell_map"])
    assert all(record["text"] == "7" for record in augmented["hakodate_ocr_evidence_records"])
    assert payload["hakodate_ocr_evidence_records"][0]["text"] == "9"


def test_hakodate_output_content_requires_real_artifacts_not_metadata_only() -> None:
    payload = {
        "_reparse_debug": {"provider": "gemini"},
        "hakodate_canonical_pipeline": {
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
            "status": "blocked",
            "blockers": ["hakodate_canonical_pipeline_failed"],
        },
    }

    assert order_service._payload_has_hakodate_output_content(  # noqa: SLF001
        payload,
        order_id="ORD_TEST",
    ) is False


def test_hakodate_evidence_assignment_rejects_generic_legacy_payload_keys() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        facility_config=_component_projection_config(),
        payload={
            "target_cell_map": [
                {
                    "target_cell_id": "legacy-target",
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "semantic_field": "qty.regular_x",
                    "bbox": [10, 10, 30, 30],
                }
            ],
            "ocr_evidence_records": [{"text": "99", "center": [20, 20]}],
        },
    )

    assert assignment["status"] == "blocked"
    assert assignment["blockers"] == [
        "hakodate_ocr_evidence_missing",
        "hakodate_target_cell_map_missing",
    ]


def test_hakodate_evidence_projection_applies_assigned_cells_to_sheet_rows() -> None:
    assignment = _component_assignment(
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        facility_config=_component_projection_config(),
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "role": "quantity",
                        "bbox": [10, 10, 30, 30],
                        "logical_targets": [
                            {
                                "sheet_cell": "E11",
                                "worksheet_row": 11,
                                "worksheet_col": 5,
                                "field": "qty.regular_x",
                                "role": "quantity",
                                "date": "2026-04-26",
                                "daypart": "朝",
                                "menu_name": "献立A",
                            }
                        ],
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [
                {"text": "１２", "center": [20, 20], "confidence": 0.9}
            ],
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", "99"]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )

    assert assignment["status"] == "auto_assignable"
    assert projected["source"] == "hakodate_ocr_evidence_sheet"
    assert projected["rows"] == [["4/26", "朝", "献立A", "１２"]]
    assert projected["blockers"] == []
    assert projected["hakodate_evidence_projection"]["metrics"] == {
        "assignment_count": 1,
        "applied_count": 1,
        "skipped_count": 0,
        "deferred_count": 0,
        "ignored_count": 0,
        "cleared_legacy_cell_count": 1,
        "expanded_cell_same_daypart_filled_count": 0,
    }


def test_hakodate_projection_applies_expanded_cell_same_daypart_copy_when_enabled() -> None:
    assignment, config = _shared_projection_assignment(diabetes=False)
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [
            ["4/26", "朝", "献立A", ""],
            ["4/26", "朝", "献立B", ""],
            ["4/26", "昼", "献立C", ""],
        ],
        "row_ids": ["row-a", "row-b", "row-c"], "warnings": [], "blockers": [],
    }
    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(
        base_sheet=base_sheet, assignment=assignment, facility_config=config,
    )
    assert projected["rows"] == [
        ["4/26", "朝", "献立A", "44"],
        ["4/26", "朝", "献立B", "44"],
        ["4/26", "昼", "献立C", ""],
    ]
    assert projected["blockers"] == []
    metrics = projected["hakodate_evidence_projection"]["metrics"]
    # Each explicitly covered physical cell is projected once; no inferred copy.
    assert metrics["applied_count"] == 2
    assert metrics["expanded_cell_same_daypart_filled_count"] == 0
    assert projected["cell_provenance_rows"][1][3] == "hakodate_ocr_strict_threshold"


def test_hakodate_projection_limits_expanded_cell_copy_to_body_merge_policy_columns() -> None:
    assignment, config = _shared_projection_assignment(diabetes=True)
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x", "qty.diabetes_x"],
        "rows": [
            ["4/26", "朝", "献立A", "", ""],
            ["4/26", "朝", "献立B", "", ""],
            ["4/26", "昼", "献立C", "", ""],
        ],
        "row_ids": ["row-a", "row-b", "row-c"], "warnings": [], "blockers": [],
    }
    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(
        base_sheet=base_sheet, assignment=assignment, facility_config=config,
    )
    assert projected["rows"] == [
        ["4/26", "朝", "献立A", "44", "5"],
        ["4/26", "朝", "献立B", "44", ""],
        ["4/26", "昼", "献立C", "", ""],
    ]
    assert projected["blockers"] == []
    metrics = projected["hakodate_evidence_projection"]["metrics"]
    # Each explicitly covered physical cell is projected once; no inferred copy.
    assert metrics["applied_count"] == 3
    assert metrics["expanded_cell_same_daypart_filled_count"] == 0
    assert projected["cell_provenance_rows"][1][3] == "hakodate_ocr_strict_threshold"
    assert projected["cell_provenance_rows"][1][4] == ""


def test_hakodate_projection_clears_legacy_quantities_when_evidence_missing() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        facility_config=_component_projection_config(),
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "date": "2026-04-26",
                        "daypart": "朝",
                        "menu_name": "献立A",
                        "bbox": [10, 10, 30, 30],
                    }
                ]
            }
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", "99"]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )

    assert assignment["status"] == "blocked"
    assert projected["rows"] == [["4/26", "朝", "献立A", ""]]
    assert "hakodate_ocr_evidence_missing" in projected["blockers"]
    assert projected["hakodate_evidence_projection"]["metrics"]["cleared_legacy_cell_count"] == 1


def test_hakodate_projection_rejects_worksheet_grid_when_cell_identity_is_absent() -> None:
    assignment = {
        "status": "auto_assignable",
        "assignment_mode": "ocr_evidence",
        "warnings": [],
        "blockers": [],
        "target_cells": [
            {"sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5, "semantic_field": "qty.regular_x"},
            {"sheet_cell": "F11", "worksheet_row": 11, "worksheet_col": 6, "semantic_field": "post_menu.F"},
            {"sheet_cell": "E12", "worksheet_row": 12, "worksheet_col": 5, "semantic_field": "qty.regular_x"},
            {"sheet_cell": "F12", "worksheet_row": 12, "worksheet_col": 6, "semantic_field": "post_menu.F"},
        ],
        "sheet_output": {
            "cells": {
                "E11": {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "semantic_field": "qty.regular_x",
                    "value_normalized": "12",
                },
                "F12": {
                    "sheet_cell": "F12",
                    "worksheet_row": 12,
                    "worksheet_col": 6,
                    "semantic_field": "post_menu.F",
                    "value_normalized": "7",
                },
            }
        },
    }
    base_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.unknown_x"],
        "rows": [["04/26", "朝", "献立A", "99", "88"], ["04/26", "朝", "献立B", "77", "66"]],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )

    assert projected["rows"] == [["04/26", "朝", "献立A", "", ""], ["04/26", "朝", "献立B", "", ""]]
    assert "hakodate_sheet_projection_incomplete" in projected["blockers"]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 0
    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 2


def test_hakodate_projection_preserves_low_confidence_ocr_for_manual_editing() -> None:
    config = _component_projection_config()
    config["fax_template"]["columns"].append({
        "name": "qty.soft_x", "role": "quantity", "index": 4, "source_index": 4,
        "diet_type": "soft", "area_id": "X",
    })
    assignment = _component_assignment(
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        facility_config=config,
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "role": "quantity",
                        "bbox": [10, 10, 30, 30],
                        "logical_targets": [
                            {
                                "sheet_cell": "E11",
                                "worksheet_row": 11,
                                "worksheet_col": 5,
                                "field": "qty.regular_x",
                                "role": "quantity",
                                "date": "2026-04-26",
                                "daypart": "朝",
                                "menu_name": "献立A",
                            }
                        ],
                    },
                    {
                        "target_cell_id": "F11",
                        "sheet_cell": "F11",
                        "worksheet_row": 11,
                        "worksheet_col": 6,
                        "semantic_field": "qty.soft_x",
                        "role": "quantity",
                        "bbox": [40, 10, 60, 30],
                        "logical_targets": [
                            {
                                "sheet_cell": "F11",
                                "worksheet_row": 11,
                                "worksheet_col": 6,
                                "field": "qty.soft_x",
                                "role": "quantity",
                                "date": "2026-04-26",
                                "daypart": "朝",
                                "menu_name": "献立A",
                            }
                        ],
                    },
                ]
            },
            "hakodate_ocr_evidence_records": [
                {"text": "5", "center": [20, 20], "confidence": 0.16},
                {"text": "7", "center": [50, 20], "confidence": 0.06},
            ],
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x", "qty.soft_x"],
        "rows": [["4/26", "朝", "献立A", "", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=config,
    )

    assert projected["rows"] == [["4/26", "朝", "献立A", "5", "7"]]
    assert projected["blockers"] == []
    assert projected["ocr_numeric_cell_summary"] == {
        "raw_ocr_numeric_count": 2,
        "accepted_count": 2,
        "deterministic_candidate_count": 0,
        "weak_candidate_count": 0,
        "unresolved_count": 0,
    }
    assert [
        (item["classification"], item["confidence_tier"], item["value"], item["target_col_index"])
        for item in projected["ocr_numeric_cell_items"]
    ] == [
        ("accepted", "medium", "5", 3),
        ("accepted", "low", "7", 4),
    ]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 2
    assert projected["hakodate_evidence_projection"]["metrics"]["deferred_count"] == 0

    assert projected["cell_score_rows"][0][3:] == [0.16, 0.06]
    assert assignment["sheet_output"]["cells"]["E11"]["raw_texts"] == ["5"]
    assert assignment["sheet_output"]["cells"]["F11"]["raw_texts"] == ["7"]


@pytest.mark.parametrize("raw", ["5", "7", "0", "", "?", "１２"])
def test_hakodate_component_raw_quantity_and_explicit_manual_value_survive_projection(raw) -> None:
    from copy import deepcopy
    from src.services import hakodate_ocr_evidence_service as evidence_service

    config = _component_projection_config()
    region = {"target_cell_id": "E11", "sheet_cell": "E11", "worksheet_row": 11,
        "worksheet_col": 5, "semantic_field": "qty.regular_x", "role": "quantity",
        "date": "2026-04-26", "daypart": "朝", "menu_name": "献立A",
        "logical_targets": [{"sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5,
            "date": "2026-04-26", "daypart": "朝", "menu_name": "献立A",
            "field": "qty.regular_x", "role": "quantity"}],
        "bbox": [10, 10, 30, 30]}
    records = evidence_service.evidence_from_records(
        [{"evidence_id": "component-raw", "text": raw, "center": [20, 20], "confidence": 0.06}],
        run_id="component-run", engine="component-external-recognizer",
        source_scope="hakodate_cell_crop_batch", retain_empty_records=True,
    )
    original_records = deepcopy(records)
    base = {"fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", "99"]], "row_ids": ["row-a"],
        "warnings": [], "blockers": []}

    def project(accepted_values=None):
        assigned = evidence_service.assign_evidence_to_target_cells(
            evidence_records=records, target_cells=[region], accepted_values=accepted_values,
        )
        output = evidence_service.sheet_output_from_assigned_results(
            assignments=assigned["assignments"], blockers=assigned["blockers"],
        )
        return order_service._apply_hakodate_sheet_output_to_sheet_payload(
            base_sheet=base, assignment={**assigned, "sheet_output": output}, facility_config=config,
        )

    projected = project()
    assert projected["rows"] == [["4/26", "朝", "献立A", raw]]
    assert projected["blockers"] == []
    assert projected["hakodate_evidence_projection"]["metrics"]["deferred_count"] == 0
    # Explicit authored operator edit at the component API, not a fabricated receipt.
    edited = project({"component-raw": "8"})
    assert edited["rows"] == [["4/26", "朝", "献立A", "8"]]
    assert edited["blockers"] == []
    assert edited["cell_provenance_rows"][0][3] == "operator_recognition_review"
    assert records == original_records
    assert base["rows"] == [["4/26", "朝", "献立A", "99"]]


def test_hakodate_evidence_projection_blocks_stale_row_identity_instead_of_sheet_cell_fallback() -> None:
    assignment = _component_assignment(
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        facility_config=_component_projection_config(),
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "date": "2026-04-26",
                        "daypart": "昼",
                        "menu_name": "献立B",
                        "bbox": [10, 10, 30, 30],
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [{"text": "5", "center": [20, 20]}],
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )

    assert projected["rows"] == [["4/26", "朝", "献立A", ""]]
    assert "hakodate_sheet_projection_incomplete" in projected["blockers"]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 0
    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 1
    assert projected["hakodate_evidence_projection"]["skipped"][0]["skip_reason"] == "logical_row_identity_not_found"


def test_hakodate_evidence_projection_uses_logical_identity_when_physical_rows_have_extra_line() -> None:
    assignment = _component_assignment(
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        facility_config=_component_projection_config(),
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E12",
                        "sheet_cell": "E12",
                        "worksheet_row": 12,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "role": "quantity",
                        "date": "2026-04-27",
                        "daypart": "朝",
                        "menu_name": "献立B",
                        "logical_targets": [{"sheet_cell": "E12", "worksheet_row": 12,
                            "worksheet_col": 5, "field": "qty.regular_x", "role": "quantity",
                            "date": "2026-04-27", "daypart": "朝", "menu_name": "献立B"}],
                        "bbox": [10, 10, 30, 30],
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [{"text": "5", "center": [20, 20]}],
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [
            ["4/26", "朝", "献立A", ""],
            ["4/27", "朝", "余計なOCR物理行ではない", ""],
            ["4/27", "朝", "献立B", ""],
        ],
        "row_ids": ["row-a", "row-extra", "row-b"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )

    assert projected["rows"] == [
        ["4/26", "朝", "献立A", ""],
        ["4/27", "朝", "余計なOCR物理行ではない", ""],
        ["4/27", "朝", "献立B", "5"],
    ]
    assert "hakodate_sheet_projection_incomplete" not in projected["blockers"]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 1
    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 0


def test_hakodate_evidence_projection_requires_logical_identity_even_without_target_cells() -> None:
    assignment = {
        "status": "review_required",
        "blockers": [],
        "warnings": [],
        "sheet_output": {
            "cells": {
                "E11": {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "semantic_field": "qty.regular_x",
                    "role": "quantity",
                    "assignment_state": "blank_evidence",
                    "raw_texts": [""],
                    "date": "2026-04-26",
                    "daypart": "昼",
                    "menu_name": "献立B",
                    "value_text": "",
                    "value_normalized": "",
                }
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )

    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 1
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 0
    assert projected["hakodate_evidence_projection"]["metrics"]["deferred_count"] == 0
    assert projected["hakodate_evidence_projection"]["skipped"][0]["skip_reason"] == "logical_row_identity_not_found"
    assert "hakodate_sheet_projection_incomplete" in projected["blockers"]


def test_hakodate_evidence_projection_uses_logical_identity_without_cell_id() -> None:
    from copy import deepcopy
    assignment = {
        "status": "review_required",
        "blockers": [],
        "warnings": [],
        "sheet_output": {
            "cells": {
                "legacy-region-1": {
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "semantic_field": "qty.regular_x",
                    "role": "quantity",
                    "assignment_state": "assigned",
                    "raw_texts": ["7"],
                    "date": "2026-04-26",
                    "daypart": "朝",
                    "menu_name": "献立A",
                    "value_text": "7",
                    "value_normalized": "7",
                }
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    original = deepcopy(assignment)
    refused = order_service._apply_hakodate_sheet_output_to_sheet_payload(
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )
    assert assignment == original
    assert refused["rows"] == base_sheet["rows"]
    assert refused["hakodate_evidence_projection"]["metrics"]["applied_count"] == 0
    assert refused["hakodate_evidence_projection"]["skipped"][0]["skip_reason"] == "hakodate_logical_targets_invalid"
    assert "hakodate_sheet_projection_incomplete" in refused["blockers"]
    # Separate explicit physical identity; never derive an adopted cell from its position.
    explicit = deepcopy(assignment)
    cell = explicit["sheet_output"]["cells"].pop("legacy-region-1")
    cell["sheet_cell"] = "E11"
    explicit["sheet_output"]["cells"]["E11"] = cell
    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(
        base_sheet=base_sheet, assignment=explicit, facility_config=_component_projection_config())

    assert projected["rows"] == [["4/26", "朝", "献立A", "7"]]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 1
    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 0
    assert "hakodate_sheet_projection_incomplete" not in projected["blockers"]


def test_hakodate_evidence_projection_blocks_quantity_without_logical_identity() -> None:
    assignment = {
        "status": "review_required",
        "blockers": [],
        "warnings": [],
        "target_cells": [
            {"sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5},
            {"sheet_cell": "E12", "worksheet_row": 12, "worksheet_col": 5},
        ],
        "sheet_output": {
            "cells": {
                "E12": {
                    "sheet_cell": "E12",
                    "worksheet_row": 12,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "semantic_field": "qty.regular_x",
                    "role": "quantity",
                    "assignment_state": "assigned",
                    "raw_texts": ["9"],
                    "value_text": "9",
                    "value_normalized": "9",
                }
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_component_projection_config(),
    )

    projection = projected["hakodate_evidence_projection"]
    assert projected["rows"] == [["4/26", "朝", "献立A", ""]]
    assert projection["metrics"]["applied_count"] == 0
    assert projection["metrics"]["skipped_count"] == 1
    assert projection["metrics"]["ignored_count"] == 0
    assert projection["skipped"][0]["skip_reason"] == "logical_row_identity_missing"
    assert "hakodate_sheet_projection_incomplete" in projected["blockers"]


def test_operator_quad_override_requires_live_render_coordinate_space(projection_case) -> None:
    projection_case.queue()
    receipt, captured = _projection_capture(projection_case)
    inputs = {
        "selected_pdf_receipt": receipt["selected_pdf_receipt"],
        "configured_input": captured["configured_input"],
        "order_input_identity": receipt["order_input_identity"],
    }
    valid = {
        "quad_px": [[10, 20], [1800, 20], [1800, 2600], [10, 2600]],
        "source_receipt": receipt,
        "quad_source": "operator_manual",
        "coordinate_space": {"mode": "render_width", "width": 1864},
    }
    quad, source = order_service._normalize_operator_quad_override(valid, render_width=1864, **inputs)  # noqa: SLF001

    assert quad == [[10.0, 20.0], [1800.0, 20.0], [1800.0, 2600.0], [10.0, 2600.0]]
    assert source == "operator_manual"

    wrong_space = {
        "quad_px": [[10, 20], [1896, 20], [1896, 2600], [10, 2600]],
        "source_receipt": receipt,
        "quad_source": "operator_manual",
        "coordinate_space": {"mode": "render_width", "width": 2000},
    }
    quad, source = order_service._normalize_operator_quad_override(wrong_space, render_width=1864, **inputs)  # noqa: SLF001

    assert quad is None
    assert source is None


def test_operator_quad_override_without_space_cannot_exceed_live_render_width(projection_case) -> None:
    projection_case.queue()
    receipt, captured = _projection_capture(projection_case)
    inputs = {
        "selected_pdf_receipt": receipt["selected_pdf_receipt"],
        "configured_input": captured["configured_input"],
        "order_input_identity": receipt["order_input_identity"],
    }
    legacy_wrong_space = {
        "quad_px": [[157.08, 554.7], [1896.77, 570.71], [1880.98, 2636.23], [146.47, 2620.49]],
        "source_receipt": receipt,
        "quad_source": "operator_approved_estimate",
    }

    quad, source = order_service._normalize_operator_quad_override(legacy_wrong_space, render_width=1864, **inputs)  # noqa: SLF001

    assert quad is None
    assert source is None
