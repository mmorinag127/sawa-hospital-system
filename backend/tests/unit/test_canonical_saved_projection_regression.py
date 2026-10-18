"""Canonical SQL regressions with synthetic raster/model inputs, not live OCR.

Frozen saved-schema assertion bodies are invoked unchanged. Only their external
provider setup is replaced by a native serializer plus verified checkpoint.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.contract import test_saved_sheet_quantity_schema as original
from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase, hospital_operator
from tests.unit import test_h2_operator_repair_revision as repair
from src.db import session_scope
from src.models.order import Order
from src.models.document import OrderDocument
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_service as orders, order_workflow_v2_service as workflow
from src.services import hakodate_cell_ocr_batch_service as batch
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from src.services import hakodate_preprocess_observation as observation
from src.services import hakodate_step_review_pipeline_service as pipeline
from src.services import master_order_form_template_service as master
from src.services import ocr_row_recovery_service as row_recovery
from src.services.storage_service import load_selected_pdf_bytes
from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as renderer
from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as recognizer


@pytest.fixture(autouse=True)
def strict_sqlite():
    def enable(connection, _record):
        if isinstance(connection, sqlite3.Connection):
            connection.execute("PRAGMA foreign_keys=ON")
            assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    event.listen(Engine, "connect", enable)
    try:
        yield
    finally:
        event.remove(Engine, "connect", enable)


class CanonicalHistoryCase(HistoryCase):
    def prepare_provider(self, *, cell_values="2"):
        self.queue()
        with session_scope() as session:
            receipt, start = workflow._axis_source_receipt(
                session, session.get(Order, self.id), session.get(OrderWorkflowState, self.id))

        def manifest(**_kwargs):
            pdf = receipt["selected_pdf_receipt"]
            return {"configured_input": deepcopy(start["configured_input"]),
                    "selected_pdf_receipt": deepcopy(pdf),
                    "_verified_pdf_bytes": load_selected_pdf_bytes(pdf["storage_uri"], pdf)}

        calls = []

        def provider(*, item, output_dir, **_kwargs):
            assert item["_verified_pdf_bytes"] == self.pdf.read_bytes()
            regions = deepcopy(item["original_target_semantics"])
            for index, region in enumerate(regions):
                x, y = (index % 5) * 160, (index // 5) * 110
                region["bbox"] = [x, y, x + 150, y + 100]
            raw = [cell_values[r["sheet_cell"]] if isinstance(cell_values, dict) else cell_values
                   for r in regions]
            if isinstance(cell_values, dict):
                assert set(cell_values) == {r["sheet_cell"] for r in regions}
            image = Image.new("RGB", (800, ((len(regions) + 4) // 5) * 110), "white")
            draw = ImageDraw.Draw(image)
            for region, value in zip(regions, raw):
                x, y, _, _ = region["bbox"]
                draw.text((x + 35, y + 30), value, font=renderer._font(24), fill="black")
            pixels = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)

            class Model:
                def __init__(self, *args, **kwargs):
                    pass

                def recognize(self, pixels, *, points):
                    calls.append(deepcopy(points))
                    assert len(points) == len(raw)
                    return {"contents": raw, "scores": [.99] * len(raw),
                            "candidates": [[{"text": text, "score": .99}] for text in raw],
                            "directions": ["horizontal"] * len(raw), "points": points}

            self.monkeypatch.setattr(recognizer, "YomitokuTextRecognizerTopKWrapper", Model)
            self.monkeypatch.setattr(renderer, "_TEXT_RECOGNIZER", object())
            records, metrics, contact = renderer._run_text_recognizer_records(
                raw_rectified_bgr=pixels, regions=regions, truth={})
            assert [r["recognizer_raw_text"] for r in records] == raw
            assert all(r["resolution_state"] == "resolved" for r in records)
            assert metrics["contact_sheet_skipped_region_count"] == 0
            checkpoint_item = {**deepcopy(item), "order_id": self.id, "facility_code": self.facility,
                "week_code": self.period,
                "original_physical_draft": batch.original_physical_draft(
                    physical_menu_entries=start["physical_menu_entries"], week_code=self.period),
                "selected_page": 1, "quad_px": [[0, 0], [799, 0], [799, pixels.shape[0]-1], [0, pixels.shape[0]-1]],
                "quad_source": "authored-raster-component", "header_axis_override": None,
                "row_axis_override": None}
            checkpoint = checkpoints.persist_recognizer_checkpoint(item=checkpoint_item,
                raw_rectified_bgr=pixels, regions=regions,
                expected_sheet_cells=item["original_expected_sheet_cells"],
                metadata={"axis_evidence": {}, "rectified_quad_points": checkpoint_item["quad_px"], "snap_debug": {}})
            outputs = repair._provider_output_files(output_dir, records=records, regions=records,
                sheet_values={r["sheet_cell"]: r["resolved_value"] for r in records})
            contact.save(outputs["contact_sheet"])
            path = Path(output_dir) / "observation.json"
            path.write_text(json.dumps({"contract": observation.CONTRACT, "checkpoint": checkpoint,
                "observations": [r["recognizer_preprocess_observation"] for r in records]}))
            outputs["preprocess_observation"] = str(path)
            return SimpleNamespace(metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
                expected_sheet_cells=tuple(item["original_expected_sheet_cells"]), outputs=outputs,
                ocr_engine="synthetic-model-native-serializer"), None

        self.monkeypatch.setattr(orders, "_build_live_hakodate_manifest_item", manifest)
        self.monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", provider)
        return calls


@pytest.fixture
def canonical_history(isolated_formal_schema, tmp_path, monkeypatch, hospital_operator):
    cases = []
    def create(**kwargs):
        assert not cases
        case = CanonicalHistoryCase(isolated_formal_schema, tmp_path, monkeypatch, hospital_operator, **kwargs)
        cases.append(case)
        return case
    return create


@pytest.mark.parametrize("fault,code", [("missing", "quantity_template_field_missing"),
    ("unknown", "quantity_template_field_unknown"), ("duplicate", "quantity_template_alias_collision")])
def test_saved_schema_materialization_paths(canonical_history, fault, code):
    original.test_saved_schema_failure_is_readable_and_blocks_every_materialization_path(canonical_history, fault, code)


@pytest.mark.parametrize("value", ["8", 0, None, "", "  "])
def test_saved_typed_values_and_candidate(canonical_history, value):
    original.test_complete_schema_preserves_typed_operator_values_and_real_candidate(canonical_history, value)


def test_programming_error_is_not_domain_blocker(canonical_history, monkeypatch):
    original.test_unknown_programming_value_error_is_not_converted_to_domain_blocker(canonical_history, monkeypatch)


def test_saved_schema_invalidates_previously_accepted_output(canonical_history):
    original.test_invalid_schema_revision_cannot_reuse_real_accepted_outputs_or_aggregate(canonical_history)


def test_invalid_schema_direct_materializer(canonical_history):
    original.test_invalid_schema_candidate_cannot_be_adopted_even_by_direct_materializer(canonical_history)


def test_saved_schema_source_race(canonical_history, monkeypatch):
    original.test_context_revalidates_real_source_after_schema_validation(canonical_history, monkeypatch)


@pytest.fixture
def row_case(isolated_formal_schema, tmp_path, monkeypatch):
    case = repair._make_repair_order(monkeypatch, tmp_path)
    # The real explicit-document review API requires the registration version,
    # not merely an Order.current_document_id pointing at an authored row.
    with session_scope() as session:
        order = session.get(Order, case[0])
        orders._record_current_order_version(session, order, session.get(OrderDocument, order.current_document_id))
    return case


def row_image_provider(monkeypatch):
    seen = []
    def image(order_id, *, source_receipt, start_input):
        orders._validate_geometry_source(source_receipt, source_receipt["selected_pdf_receipt"],
            start_input["configured_input"], source_receipt["order_input_identity"])
        saved = ((start_input["workflow"]["secondary_actions_json"] or {}).get("workflow_v2") or {}).get("row_axis_override")
        consumed = orders._normalize_operator_row_axis_override(saved,
            selected_pdf_receipt=source_receipt["selected_pdf_receipt"],
            configured_input=start_input["configured_input"], order_input_identity=source_receipt["order_input_identity"])
        seen.append(consumed)
        plan = [{"date": r["menu_date"]} for r in start_input["physical_menu_entries"]]
        return {"status": "ready", "saved_override": consumed,
            "calendar_days": row_recovery.calendar_contract(plan),
            "header_band_count": len(master.TEMPLATE_HEADER_EDGE_ROWS),
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 100}}
    monkeypatch.setattr(orders, "build_hakodate_row_axis_review", image)
    return seen


def save_valid_row(order_id, revision, receipt, review):
    count = review["header_band_count"] + 8
    ys = [float(i * 10) for i in range(count - 1)] + [100.0]
    result, error = workflow.save_row_axis_review_decision(order_id, corrected_ys=ys,
        coordinate_space=review["coordinate_space"], body_day_boundary_indexes=list(range(8)),
        day_labels=[day["date"] for day in review["calendar_days"]],
        source_receipt=receipt, expected_revision=revision)
    assert error is None
    return ys, result


def test_row_save_consumes_original_source(row_case, monkeypatch):
    order_id, revision, config = row_case
    row_image_provider(monkeypatch)
    review, error = workflow.get_row_axis_review(order_id)
    assert error is None
    receipt = review["source_receipt"]
    ys, _ = save_valid_row(order_id, revision, receipt, review)
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        saved = workflow._workflow_meta(state)["row_axis_override"]
        updated, _ = workflow._axis_source_receipt(session, session.get(Order, order_id), state)
    inputs = {"selected_pdf_receipt": receipt["selected_pdf_receipt"], "configured_input": config,
              "order_input_identity": receipt["order_input_identity"]}
    normalized = orders._normalize_operator_row_axis_override(saved, **inputs)
    assert normalized["corrected_ys"] == ys
    assert normalized["source_receipt"] == receipt
    assert updated["workflow_revision"] != receipt["workflow_revision"]
    assert updated["input_digest"] != receipt["input_digest"]
    assert updated["geometry_source_digest"] == receipt["geometry_source_digest"]
    changed = deepcopy(inputs)
    changed["selected_pdf_receipt"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="axis_source_input_changed"):
        orders._normalize_operator_row_axis_override(saved, **changed)
    missing = deepcopy(saved)
    del missing["source_receipt"]["geometry_source_digest"]
    with pytest.raises(ValueError, match="geometry_source_proof_required"):
        orders._normalize_operator_row_axis_override(missing, **inputs)


def test_row_get_save_get_keeps_proof(row_case, monkeypatch):
    order_id, revision, _ = row_case
    seen = row_image_provider(monkeypatch)
    first, error = workflow.get_row_axis_review(order_id)
    assert error is None
    receipt = deepcopy(first["source_receipt"])
    ys, _ = save_valid_row(order_id, revision, receipt, first)
    second, error = workflow.get_row_axis_review(order_id)
    assert error is None
    assert seen[0] is None
    assert seen[-1]["corrected_ys"] == ys
    assert second["saved_override"]["source_receipt"] == receipt
    assert second["source_receipt"]["geometry_source_digest"] == receipt["geometry_source_digest"]
    assert second["source_receipt"]["workflow_revision"] != receipt["workflow_revision"]


def test_partial_period_same_plan_all_consumers(isolated_formal_schema, monkeypatch, tmp_path):
    from src.services import hakodate_preprocessing_pipeline_service as collector
    generator = repair._make_connected_physical_pipeline(monkeypatch, tmp_path, register_menu_amounts=True)
    try:
        case = next(generator)
        order_id, receipt, start, item, output = case
        image = cv2.imread(item["step2_png"])
        assert image is not None
        # Use the real alignment/manual calendar validator. Observed synthetic
        # coordinates are explicit inputs, not a manufactured used=True result.
        days = row_recovery.calendar_contract([
            {"date": row["menu_date"]} for row in start["physical_menu_entries"]])
        space = {"mode": "template_canvas", "width": image.shape[1], "height": image.shape[0]}
        _, ys, _, _ = batch._align_axes()
        for module in (batch, collector):
            monkeypatch.setattr(module, "_align_axes", pipeline._align_axes)
        item.update(week_code=start["order"]["week_code"], selected_page=1,
            quad_px=[[20, 20], [270, 20], [270, 290], [20, 290]], quad_source="explicit-fixture",
            header_axis_override={"corrected_xs": item["template_axes_x"], "coordinate_space": space},
            row_axis_override={"corrected_ys": ys, "coordinate_space": space,
                "day_labels": [day["date"] for day in days], "body_day_boundary_indexes": list(range(8))})
        repair.test_actual_header_row_and_normal_collector_share_partial_period_plan(case)
    finally:
        generator.close()
