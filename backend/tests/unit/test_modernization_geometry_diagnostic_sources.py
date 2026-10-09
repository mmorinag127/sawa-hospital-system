"""Isolated diagnostic orchestration contracts; no producer, network, DB or live proof.

Production script function ASTs run with explicit transport/capture fixtures.
No canonical producer receipt is fabricated or adopted into application state.
"""

import ast
from copy import deepcopy
import __future__
import json
from pathlib import Path
from types import SimpleNamespace
import time
from typing import Any

import pytest

from src.services.order_service import geometry_source_digest
from src.services.order_workflow_v2_service import _stable_json_hash
from src.services.workflow_attempt_guard_service import snapshots_equal


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def script_functions(name, namespace=None):
    path = SCRIPTS / f"{name}.py"
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    scope = {"Any": Any, "Path": Path, "json": json, "time": time,
             "snapshots_equal": snapshots_equal, "geometry_source_digest": geometry_source_digest,
             "_stable_json_hash": _stable_json_hash}
    scope.update(namespace or {})
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec",
                 flags=__future__.annotations.compiler_flag), scope)
    return scope


@pytest.fixture
def scripts():
    validation = script_functions("validate_hakodate_all_facilities")
    comparison = script_functions("rerun_compare_stg_hakodate_targets", {
        key: validation[key] for key in ("_require_comparison_lineage", "_require_local_artifact", "_revalidate_comparison_geometry")})
    return validation, comparison


@pytest.fixture
def source_pair():
    captured = {
        "order": {"id": "owned-order", "facility_code": "synthetic", "week_code": "2026-09@2026-09-27~2026-10-03"},
        "configured_input": {"facility": {"fax_template": {"page": 1, "columns": [{"role": "quantity", "diet_type": "regular"}]}}},
        "physical_menu_entries": [{"menu_date": "2026-09-27", "daypart": "朝", "name": "synthetic-menu", "category": "", "sort_order": 1, "slot_index": 0}],
        "selected_pdf_receipt": {"order_id": "owned-order", "document_id": "owned-doc", "sha256": "a" * 64,
                                 "storage_uri": "owned.pdf", "generation": None},
        "workflow": {"revision": 3},
    }
    identity = {key: captured["order"][key] for key in ("facility_code", "week_code")}
    receipt = {
        "geometry_source_digest": geometry_source_digest(captured["selected_pdf_receipt"], captured["configured_input"], identity,
                                                        physical_entries=captured["physical_menu_entries"]),
        "configured_input_digest": _stable_json_hash(captured["configured_input"]),
        "physical_menu_entries": deepcopy(captured["physical_menu_entries"]),
        "selected_pdf_receipt": deepcopy(captured["selected_pdf_receipt"]),
        "order_input_identity": identity, "input_digest": "b" * 64,
        "selected_version_input": {"document_id": "owned-doc"}, "workflow_revision": 3,
    }
    return captured, {"order_id": "owned-order", "source_receipt": receipt}


def lineage_surfaces():
    sheet = {"fields": ["menu", "qty.regular_x"], "rows": [["synthetic", "7"], ["zero", "0"], ["blank", ""]]}
    return {
        "order_id": "owned-order",
        "workflow": {"order_id": "owned-order", "revision": 3, "selected_ocr_result_id": "selected"},
        "preview": {"status": "ready", "source_evidence_run_id": "selected", "blockers": []},
        "projection": {"order_id": "owned-order", "selected_ocr_result_id": "selected", "sheet": deepcopy(sheet)},
        "saved": {"order_id": "owned-order", "source_ocr_result_id": "selected", "sheet": deepcopy(sheet)},
    }


def test_matching_diagnostic_source_and_lineage_do_not_mutate_inputs(scripts, source_pair):
    validation, _comparison = scripts
    captured, remote = source_pair
    before = deepcopy(source_pair)
    assert validation["_validate_remote_geometry_source"](captured, remote) == remote["source_receipt"]
    surfaces = lineage_surfaces()
    original = deepcopy(surfaces)
    assert validation["_require_comparison_lineage"](**surfaces) == {
        "order_id": "owned-order", "revision": 3, "selected_ocr_result_id": "selected"}
    assert surfaces == original
    assert source_pair == before


@pytest.mark.parametrize("key", ["configured_input_digest", "geometry_source_digest", "physical_menu_entries", "selected_pdf_receipt", "order_input_identity"])
def test_remote_geometry_mismatch_is_explicit(scripts, source_pair, key):
    validation, _ = scripts
    captured, remote = source_pair
    remote["source_receipt"][key] = "mismatch"
    with pytest.raises(ValueError, match="^validation_remote_geometry_source_mismatch$"):
        validation["_validate_remote_geometry_source"](captured, remote)


@pytest.mark.parametrize("key", ["configured_input_digest", "physical_menu_entries", "selected_pdf_receipt", "workflow_revision"])
def test_missing_remote_source_cannot_validate(scripts, source_pair, key):
    validation, _ = scripts
    captured, remote = source_pair
    del remote["source_receipt"][key]
    with pytest.raises(ValueError, match="^validation_remote_geometry_source_required$"):
        validation["_validate_remote_geometry_source"](captured, remote)


@pytest.mark.parametrize("key", ["configured_input", "physical_menu_entries", "selected_pdf_receipt", "workflow"])
def test_terminal_full_snapshot_change_blocks(scripts, source_pair, key):
    validation, _ = scripts
    captured, remote = source_pair
    current = deepcopy(captured)
    current[key] = {"changed": True}
    validation["_capture_geometry_input"] = lambda order_id: deepcopy(current)
    with pytest.raises(ValueError, match="^validation_geometry_source_changed$"):
        validation["_revalidate_comparison_geometry"](
            order_id="owned-order", context={"captured_input": captured, "remote_geometry_source": remote["source_receipt"]}, review=remote)


def test_terminal_remote_receipt_change_blocks_even_when_local_capture_stable(scripts, source_pair):
    validation, _ = scripts
    captured, remote = source_pair
    context = {"captured_input": deepcopy(captured), "remote_geometry_source": deepcopy(remote["source_receipt"])}
    validation["_capture_geometry_input"] = lambda order_id: deepcopy(captured)
    remote["source_receipt"]["input_digest"] = "c" * 64
    with pytest.raises(ValueError, match="^validation_remote_geometry_source_changed$"):
        validation["_revalidate_comparison_geometry"](order_id="owned-order", context=context, review=remote)


@pytest.mark.parametrize("surface,key,value,reason", [
    ("workflow", "order_id", "foreign", "validation_workflow_order_mismatch"),
    ("workflow", "revision", None, "validation_workflow_revision_invalid"),
    ("workflow", "revision", True, "validation_workflow_revision_invalid"),
    ("workflow", "selected_ocr_result_id", None, "validation_workflow_selected_evidence_required"),
    ("preview", "source_evidence_run_id", "other", "validation_preview_workflow_lineage_mismatch"),
    ("preview", "status", "blocked", "validation_preview_workflow_lineage_mismatch"),
    ("projection", "selected_ocr_result_id", "other", "validation_sheet_ocr_lineage_mismatch"),
    ("saved", "source_ocr_result_id", "other", "validation_sheet_ocr_lineage_mismatch"),
    ("saved", "order_id", "foreign", "validation_sheet_order_mismatch:saved_sheet"),
    ("saved", "sheet", None, "validation_sheet_missing:saved_sheet"),
])
def test_lineage_missing_or_mismatch_blocks(scripts, surface, key, value, reason):
    validation, _ = scripts
    surfaces = lineage_surfaces()
    surfaces[surface][key] = value
    with pytest.raises(ValueError, match=f"^{reason}$"):
        validation["_require_comparison_lineage"](**surfaces)


@pytest.mark.parametrize("key", ["records", "ocr_regions"])
@pytest.mark.parametrize("kind", ["path-missing", "file-missing", "invalid-json", "empty", "wrong-type", "bad-item"])
def test_required_artifact_never_becomes_empty_success(scripts, tmp_path, key, kind):
    validation, _ = scripts
    path = tmp_path / f"{key}.json"
    outputs = {key: str(path)}
    if kind == "path-missing":
        outputs.clear()
        reason = "path_required"
    elif kind == "file-missing":
        reason = "missing"
    else:
        path.write_text({"invalid-json": "{", "empty": "[]", "wrong-type": "{}", "bad-item": "[null]"}[kind])
        reason = "invalid"
    with pytest.raises(ValueError, match=f"^validation_local_artifact_{reason}:{key}$"):
        validation["_require_local_artifact"](outputs, key)


@pytest.mark.parametrize("key", ["master_template_sha256", "facility_template_canonical_digest", "schema_digest"])
@pytest.mark.parametrize("value", [None, "", "a" * 63, "z" * 64])
def test_missing_or_invalid_template_hashes_cannot_equal_ok(scripts, key, value):
    _, comparison = scripts
    diagnostics = {name: "a" * 64 for name in ("master_template_sha256", "facility_template_canonical_digest", "schema_digest")}
    diagnostics[key] = value
    client = SimpleNamespace(request_json=lambda *args, **kwargs: deepcopy(diagnostics))
    with pytest.raises(ValueError, match=f"^validation_template_digest_invalid:{key}$"):
        comparison["_compare_template_diagnostics"](client=client, facility_id="synthetic", week_value="explicit", local_diagnostics=diagnostics)


def test_valid_template_hashes_and_unavailable_transport_are_distinct(scripts):
    _, comparison = scripts
    diagnostics = {name: "a" * 64 for name in ("master_template_sha256", "facility_template_canonical_digest", "schema_digest")}
    client = SimpleNamespace(request_json=lambda *args, **kwargs: deepcopy(diagnostics))
    assert comparison["_compare_template_diagnostics"](client=client, facility_id="synthetic", week_value="explicit", local_diagnostics=diagnostics)["status"] == "ok"

    def unavailable(*args, **kwargs):
        raise RuntimeError("isolated transport unavailable")

    client.request_json = unavailable
    assert comparison["_compare_template_diagnostics"](client=client, facility_id="synthetic", week_value="explicit", local_diagnostics=diagnostics)["status"] == "unavailable"


@pytest.mark.parametrize("script,function,change", [
    (script, function, change)
    for script, function in [("validate_hakodate_all_facilities", "validate_case"),
                             ("rerun_compare_stg_hakodate_targets", "_compare_one")]
    for change in ("revision", "evidence", "geometry", "none")
] + [("validate_hakodate_all_facilities", "validate_case", "saved")])
def test_actual_comparison_stops_terminal_changes_and_preserves_matching_or_unavailable_outcomes(tmp_path, source_pair, script, function, change):
    validation = script_functions("validate_hakodate_all_facilities")
    captured, remote = source_pair
    context = {"captured_input": captured, "remote_geometry_source": remote["source_receipt"]}
    executed = False

    def capture(order_id):
        current = deepcopy(captured)
        if executed and change == "geometry":
            current["physical_menu_entries"][0]["name"] = "changed-during-native-boundary"
        return current

    validation["_capture_geometry_input"] = capture
    surfaces = lineage_surfaces()
    calls = []
    region = {"sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5,
              "semantic_field": "qty.regular_x", "bbox": [0, 0, 10, 10], "ocr_candidate": True}
    records, regions = tmp_path / "records.json", tmp_path / "regions.json"
    records.write_text(json.dumps([{"ocr_text": "7"}]))
    regions.write_text(json.dumps([region]))
    outputs = {"records": str(records), "ocr_regions": str(regions)}
    workflow_reads = 0

    def get(path, *args, **kwargs):
        nonlocal workflow_reads
        calls.append(path)
        if path.endswith("/workflow-v2"):
            workflow_reads += 1
            return {**surfaces["workflow"], "revision": 4 if executed and change == "revision" else 3,
                    "selected_ocr_result_id": "changed" if executed and change == "evidence" else "selected"}
        if path.endswith("/sheet-source"):
            payload = deepcopy(surfaces["projection"])
            if executed and change == "evidence":
                payload["selected_ocr_result_id"] = "changed"
            return payload
        if path.endswith("/sheet"):
            payload = deepcopy(surfaces["saved"])
            if executed and change == "evidence":
                payload["source_ocr_result_id"] = "changed"
            if executed and change == "saved":
                payload["sheet"]["rows"][0][1] = "8"
            return payload
        if path.endswith("/hakodate-overlay-preview"):
            return {**surfaces["preview"], "source_evidence_run_id": "changed" if executed and change == "evidence" else "selected",
                    "assignment": {"assignments": [{"sheet_cell": "E11", "assigned_value": "7"}], "target_cells": [region]}}
        if path.endswith("/quad-review"):
            return deepcopy(remote)
        raise AssertionError(f"unexpected endpoint: {path}")

    scope = validation if function == "validate_case" else script_functions(script)
    scope.update({"_require_comparison_lineage": validation["_require_comparison_lineage"],
                  "_require_local_artifact": validation["_require_local_artifact"],
                  "_revalidate_comparison_geometry": validation["_revalidate_comparison_geometry"]})
    def build(**kwargs):
        nonlocal executed
        executed = True
        return ({"outputs": outputs} if function == "validate_case" else SimpleNamespace(outputs=outputs)), None

    # These are diagnostic transport/output boundaries, not producer or canonical guard substitutes.
    if function == "validate_case":
        scope.update({"cmp": SimpleNamespace(STG_API_BASE="https://isolated.invalid"), "_json_get": get,
                      "_build_live_manifest_item_from_stg_order": lambda **kwargs: ({}, context),
                      "build_best_method_for_manifest_item": build,
                      "_cell_value_map_from_local_artifacts": lambda **kwargs: {"E11": "7"}})
        kwargs = {"page_index": 1, "item": {"order_id": "owned-order", "facility_code": "synthetic"},
                  "auth_header": "isolated", "output_dir": tmp_path, "render_width": 1200, "http_timeout": 1}
    else:
        scope.update({"_build_local_manifest_item_from_stg": lambda **kwargs: ({"local_template_diagnostics": {}}, context),
                      "_compare_template_diagnostics": lambda **kwargs: {"status": "unavailable"},
                      "build_hakodate_best_method_for_manifest_item": build,
                      "hakodate_ocr_evidence_service": SimpleNamespace(target_cells_from_regions=lambda value: deepcopy(value))})
        kwargs = {"client": SimpleNamespace(request_json=lambda method, path, **kw: get(path)),
                  "order": {"id": "owned-order", "facility": "synthetic", "week_value": "explicit"},
                  "output_dir": tmp_path, "render_width": 1200, "page": 1, "require_template_parity": False}
    if change != "none":
        reason = "validation_geometry_source_changed" if change == "geometry" else "validation_comparison_source_changed"
        with pytest.raises(ValueError, match=f"^{reason}$"):
            scope[function](**kwargs)
    else:
        result = scope[function](**kwargs)
        assert result["status"] == ("ok" if function == "validate_case" else "ng")
        if function == "_compare_one":
            assert result["template_parity"]["status"] == "unavailable"
    assert workflow_reads == (1 if change == "geometry" else 2)
    assert any(path.endswith("/workflow-v2/quad-review") for path in calls)
    assert not any(path.endswith(("/ocr-sheet", "/draft-sheet", "/workflow-state")) for path in calls)
    if change != "none":
        assert not list(tmp_path.rglob("comparison.json"))
        assert not list(tmp_path.rglob("validation_result.json"))
