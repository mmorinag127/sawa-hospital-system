"""Offline native serialization/projection contracts, not real-model or FAX proof.

Only the model boundary is stubbed. Workbook planning, contact preprocessing,
native serialization, raw validation, assignment and projection are real code.
"""
from copy import deepcopy
from hashlib import sha256
from shutil import copyfile

import cv2
import numpy as np
from openpyxl import load_workbook
from PIL import Image, ImageDraw
import pytest

from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as renderer
from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime
from src.services import hakodate_cell_ocr_batch_service as batch
from src.services import hakodate_ocr_evidence_service as evidence
from src.services import hakodate_step_review_pipeline_service as pipeline
from src.services import master_order_form_template_service as master
from src.services import order_service as orders
from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence


def authored_plan(tmp_path, members, *, merged=True):
    fields = ["date", "daypart", "category", "menu_name", "qty.regular_x",
              "qty.no_meat_x", "qty.no_fish_x", "qty.staff_x", "remarks"]
    identities = [("2026-03-01", "朝", f"Menu {i}") for i in range(members)]
    identities += [("2026-03-01", "昼", "Other meal"),
                   ("2026-03-02", "朝", "Other day")]
    base = {"fields": fields, "rows": [[day, meal, "主", name, "", "", "", "", ""]
                                       for day, meal, name in identities]}
    columns = [{"index": i, "source_index": i, "role": role}
               for i, role in enumerate(("date", "daypart", "category", "menu_name"))]
    columns += [{"index": i, "source_index": i, "role": "quantity", "diet_type": diet, "area_id": "X"}
                for i, diet in enumerate(("regular", "no_meat", "no_fish", "staff"), 4)]
    columns.append({"index": 8, "source_index": 8, "role": "note"})
    template = {"template_id": "fax_layout_regular_forbidden_v1", "units": "normalized",
                "table_box": [0, 0, 1, 1], "header_rows": 0, "columns": columns}
    if merged:
        template["body_merge_policy"] = {"mode": "daypart", "columns": ["regular"], "required": True}
    config = {"facility_id": "native-projection-synthetic", "facility_name": "Synthetic projection",
              "fax_template": template}
    before = sha256(master.MASTER_TEMPLATE_PATH.read_bytes()).hexdigest()
    copied = tmp_path / "copied-master.xlsx"
    copyfile(master.MASTER_TEMPLATE_PATH, copied)
    output = tmp_path / "canonical-authored.xlsx"
    entries = [{"menu_date": day, "daypart": meal, "category": "主", "name": name, "sort_order": i + 1}
               for i, (day, meal, name) in enumerate(identities)]
    master.build_facility_template_xlsx(
        facility_config=config, master_template_path=copied, output_path=output,
        week_value="2026-03@2026-03-01~2026-03-07", week_menu_entries=entries,
    )
    workbook = load_workbook(output)
    try:
        plan = pipeline.canonical_logical_target_plan(
            worksheet=workbook[master.FACILITY_TEMPLATE_SHEET_NAME],
            fax_template=template, draft_sheet=deepcopy(base),
        )
    finally:
        workbook.close()
    assert sha256(copied.read_bytes()).hexdigest() == before
    assert sha256(master.MASTER_TEMPLATE_PATH.read_bytes()).hexdigest() == before
    for i, region in enumerate(plan):
        # Synthetic observed pixels are independent of canonical logical membership.
        x, y = (i % 5) * 160, (i // 5) * 110
        region["bbox"] = [x, y, x + 150, y + 100]
    return base, config, plan


def native_records(monkeypatch, plan, *, quantity="7", note="", score=.99, white=False, single=False):
    raw = [note if r["role"] == "note" else quantity
           if r["field"] == "qty.regular_x" and r["date"] == "2026-03-01" and r["daypart"] == "朝"
           else "" for r in plan]
    if single:
        eligible = [i for i, r in enumerate(plan) if r["field"] == "qty.regular_x"
                    and r["date"] == "2026-03-01" and r["daypart"] == "朝"]
        assert eligible
        for i in eligible[1:]:
            raw[i] = ""
    image = Image.new("RGB", (800, ((len(plan) + 4) // 5) * 110), "white")
    draw = ImageDraw.Draw(image)
    for region, value in zip(plan, raw):
        if value and not white:
            x, y, _, _ = region["bbox"]
            draw.text((x + 35, y + 30), value, font=renderer._font(24), fill="black")
    pixels = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    before_pixels, before_plan = pixels.copy(), deepcopy(plan)
    calls = []

    class ModelWrapper:
        def __init__(self, *args, **kwargs):
            pass

        def recognize(self, pixels, *, points):
            calls.append(deepcopy(points))
            assert len(points) == len(raw)
            return {"contents": list(raw), "scores": [score] * len(raw),
                    "candidates": [[{"text": value, "score": score}] for value in raw],
                    "directions": ["horizontal"] * len(raw), "points": points}

    # Stub only model construction/output; do not patch runtime functions or guards.
    monkeypatch.setattr(runtime, "YomitokuTextRecognizerTopKWrapper", ModelWrapper)
    monkeypatch.setattr(renderer, "_TEXT_RECOGNIZER", object())
    records, metrics, contact = renderer._run_text_recognizer_records(
        raw_rectified_bgr=pixels, regions=plan, truth={},
    )
    assert len(calls) == 1 and metrics["raw_prediction_count"] == len(plan)
    assert metrics["contact_sheet_skipped_region_count"] == 0
    assert plan == before_plan and np.array_equal(pixels, before_pixels)
    for record, expected in zip(records, raw):
        assert record["raw_text"] == record["recognizer_raw_text"] == expected
        x0, y0, x1, y1 = record["recognizer_input_evidence"]["bbox"]
        assert record["recognizer_input_evidence"] == contact_crop_evidence(
            contact, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        )
    return records


def project(base, config, plan, records, *, diagnostics=None):
    originals = deepcopy((base, config, plan, records))
    targets = evidence.target_cells_from_regions(plan)
    # Same lossless native-to-evidence adapter as the production source boundary.
    payload = [{"raw_text": r["raw_text"], "normalized_value": r["resolved_value"],
                "confidence": r["recognizer_score"], "bbox": deepcopy(r["bbox"]),
                "engine_metadata": {"sheet_cell": r["sheet_cell"], "recognizer_output": deepcopy(r)}}
               for r in records]
    normalized = evidence.evidence_from_records(
        payload, run_id="synthetic-native-projection", engine="synthetic-model-boundary",
        source_scope="offline-native-serialization", retain_empty_records=True,
    )
    assigned = evidence.assign_evidence_to_target_cells(evidence_records=normalized, target_cells=targets)
    sheet = evidence.sheet_output_from_assigned_results(
        assignments=assigned["assignments"], blockers=assigned["blockers"],
        unassigned_evidence=assigned["unassigned_evidence"],
    )
    result = orders._apply_hakodate_sheet_output_to_sheet_payload(
        base_sheet=base, assignment={**assigned, "target_cells": targets, "sheet_output": sheet,
                                    "status": "blocked" if sheet["blockers"] else "auto_assignable"},
        facility_config=config,
    )
    assert (base, config, plan, records) == originals
    assert [r["raw_text"] for r in normalized] == [r["raw_text"] for r in records]
    if diagnostics is not None:
        diagnostics.update(evidence=deepcopy(normalized), assignments=deepcopy(assigned["assignments"]))
    return result, sheet


@pytest.mark.parametrize("members,value", [(3, "0"), (4, "1"), (5, "7"), (6, "20"), (4, "")])
def test_native_values_expand_only_to_authored_members(tmp_path, monkeypatch, members, value):
    base, config, plan = authored_plan(tmp_path, members)
    regions = [r for r in plan if r["field"] == "qty.regular_x" and r["daypart"] == "朝"
               and r["date"] == "2026-03-01"]
    assert len(regions) == 1 and len(regions[0]["logical_targets"]) == members
    records = native_records(monkeypatch, plan, quantity=value)
    before = deepcopy(records)
    grid = batch.sheet_value_grid_from_assignments(batch.sheet_assignments_from_ocr_regions(records))
    result, sheet = project(base, config, plan, records)
    assert records == before and result["blockers"] == []
    assert [r[4] for r in result["rows"]] == [value] * members + ["", ""]
    assert all(r[5:8] == ["", "", ""] for r in result["rows"])
    expected_cells = {t["sheet_cell"] for r in plan for t in r["logical_targets"]}
    assert set(grid["cells"]) == set(sheet["cells"]) == expected_cells
    member_cells = {t["sheet_cell"] for t in regions[0]["logical_targets"]}
    for key in expected_cells:
        expected = value if key in member_cells else ""
        assert grid["cells"][key]["value_normalized"] == sheet["cells"][key]["value_normalized"] == expected
    assert result["hakodate_evidence_projection"]["metrics"]["expanded_cell_same_daypart_filled_count"] == 0


@pytest.mark.parametrize("note", ["0", "", "備考減塩007", "７? -7"])
def test_native_note_strings_preserved_in_both_paths(tmp_path, monkeypatch, note):
    base, config, plan = authored_plan(tmp_path, 3)
    records = native_records(monkeypatch, plan, note=note)
    before = deepcopy(records)
    grid = batch.sheet_value_grid_from_assignments(batch.sheet_assignments_from_ocr_regions(records))
    result, sheet = project(base, config, plan, records)
    assert records == before and not result["blockers"]
    assert [row[8] for row in result["rows"]] == [note] * len(base["rows"])
    for region in plan:
        if region["role"] == "note":
            key = region["sheet_cell"]
            assert grid["cells"][key]["value_normalized"] == sheet["cells"][key]["value_text"] == note


def test_unmerged_native_cells_do_not_copy_to_neighbor_menu(tmp_path, monkeypatch):
    base, config, plan = authored_plan(tmp_path, 3, merged=False)
    records = native_records(monkeypatch, plan, single=True)
    # Every nonmerged region has its own real model response; no post-serializer injection.
    assert all(len(r["logical_targets"]) == 1 for r in plan)
    result, sheet = project(base, config, plan, records)
    grid = batch.sheet_value_grid_from_assignments(batch.sheet_assignments_from_ocr_regions(records))
    assert not result["blockers"]
    for region in plan:
        cell = region["sheet_cell"]
        assert grid["cells"][cell]["value_normalized"] == sheet["cells"][cell]["value_normalized"]
        assert sheet["cells"][cell]["source_region_id"] == region["region_id"]
    assert [r[4] for r in result["rows"]] == ["7", "", "", "", ""]


@pytest.mark.parametrize("route", ["batch", "evidence"])
@pytest.mark.parametrize("raw,derived", [("7", "0"), ("0", "7"), ("", "1"), ("1", "0")])
def test_derived_alias_never_replaces_native_raw_in_either_projection(tmp_path, monkeypatch, route, raw, derived):
    base, config, plan = authored_plan(tmp_path, 3)
    records = native_records(monkeypatch, plan, quantity=raw)
    tampered = deepcopy(records)
    target = next(r for r in tampered if r["field"] == "qty.regular_x"
                  and len(r["logical_targets"]) == 3)
    target["resolved_value"] = derived
    before = deepcopy(tampered)
    if route == "batch":
        grid = batch.sheet_value_grid_from_assignments(batch.sheet_assignments_from_ocr_regions(tampered))
    else:
        result, grid = project(base, config, plan, tampered)
        assert result['blockers'] == []
    for logical in target['logical_targets']:
        assert grid['cells'][logical['sheet_cell']]['value_normalized'] == raw
    assert tampered == before
    assert next(r for r in records if r['sheet_cell'] == target['sheet_cell'])['resolved_value'] == raw


def test_null_derived_alias_keeps_existing_nonnull_alias_contract(tmp_path, monkeypatch):
    base, config, plan = authored_plan(tmp_path, 3)
    records = native_records(monkeypatch, plan)
    target = next(r for r in records if r["raw_text"] == "7")
    target["resolved_value"] = None
    before = deepcopy(records)
    # Null alone is not a new resolution policy: existing nonnull aliases/raw agree.
    assert evidence.recognition_input_issue(target) is None
    grid = batch.sheet_value_grid_from_assignments(batch.sheet_assignments_from_ocr_regions(records))
    result, sheet = project(base, config, plan, records)
    assert not result["blockers"] and records == before
    for logical in target["logical_targets"]:
        key = logical["sheet_cell"]
        assert grid["cells"][key]["value_normalized"] == sheet["cells"][key]["value_normalized"] == "7"
        assert sheet["cells"][key]["raw_texts"] == ["7"]
    assert target["resolved_value"] is None


@pytest.mark.parametrize("derived", ["0", None])
def test_native_top_level_derived_alias_retained_in_source_values(tmp_path, monkeypatch, derived):
    _base, _config, plan = authored_plan(tmp_path, 3)
    native = native_records(monkeypatch, plan)
    record = deepcopy(next(r for r in native if r["raw_text"] == "7"))
    record["resolved_value"] = derived
    before = deepcopy(record)
    saved = evidence.evidence_from_records(
        [record], run_id="native-alias-retention", engine="synthetic-model-boundary",
        source_scope="offline-native-serialization", retain_empty_records=True,
    )[0]
    assert saved["raw_text"] == "7" and record == before
    assert "resolved_value" in saved["engine_metadata"]["source_values"]
    assert saved["engine_metadata"]["source_values"]["resolved_value"] == derived
    assert evidence.validate_ocr_quantity_source(saved) == '7'


@pytest.mark.parametrize("score,white,reason", [(.99, True, "hakodate_blank_input_nonempty_prediction"),
                                              (.01, False, "hakodate_ocr_quantity_low_confidence")])
def test_image_and_confidence_diagnostics_do_not_block_quantity_projection(tmp_path, monkeypatch, score, white, reason):
    base, config, plan = authored_plan(tmp_path, 3)
    records = native_records(monkeypatch, plan, quantity="1", score=score, white=white)
    affected = [r for r in records if r["raw_text"] == "1"]
    assert affected and all(r['resolved_value'] == '1' for r in affected)
    grid = batch.sheet_value_grid_from_assignments(batch.sheet_assignments_from_ocr_regions(records))
    result, sheet = project(base, config, plan, records)
    assert result['blockers'] == sheet['blockers'] == []
    for record in affected:
        assert record['raw_text'] == record['recognizer_raw_text'] == '1'
        for logical in record['logical_targets']:
            key = logical['sheet_cell']
            assert grid['cells'][key]['value_normalized'] == sheet['cells'][key]['value_normalized'] == '1'
            assert sheet['cells'][key]['raw_texts'] == ['1']
