import importlib.util
import hashlib
import copy
import json
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_stg_monthly_menus", Path(__file__).resolve().parents[3] / "scripts/verify_stg_monthly_menus.py",
)
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


def payload(quantity=1, diffs=None):
    return {"items": [{"id": "item", "name": "Fish", "menu_master_id": "master",
                       "unit_type": "cut", "qty_per_serving": quantity}],
            "master_checks": {"issues": [] if diffs is None else [{"item_id": "item", "field_diffs": diffs}]}}


MASTER = {"master": {"unit_type": "cut", "qty_per_serving": 1}}


def test_verification_checks_values_and_target_coverage():
    assert verification.validate(payload(), MASTER, {"Fish": ["cut", 1]})["checked_existing_items"] == 1
    with pytest.raises(AssertionError):
        verification.validate(payload(100), MASTER, {"Fish": ["cut", 1]})
    with pytest.raises(AssertionError):
        verification.validate(payload(), MASTER, {"Missing": ["cut", 1]})


def test_verification_rejects_quantity_diff_but_keeps_other_warnings():
    with pytest.raises(AssertionError):
        verification.validate(payload(diffs=[{"field": "qty_per_serving"}]), MASTER, {"Fish": ["cut", 1]})
    assert verification.validate(payload(diffs=[{"field": "category"}]), MASTER,
                                 {"Fish": ["cut", 1]})["remaining_issues"] == 1


def test_production_fixture_uri_is_not_read_by_staging_job():
    with pytest.raises(ValueError):
        verification.download("gs://sawahospitalsystem-prod-raw/file.xlsm")


def test_only_exact_explicit_test_snapshot_can_be_replaced():
    snapshot = payload()
    digest = hashlib.sha256(verification.canonical_snapshot(snapshot).encode()).hexdigest()
    verification.verify_replace_snapshot(snapshot, digest)
    with pytest.raises(RuntimeError):
        verification.verify_replace_snapshot(snapshot, None)
    with pytest.raises(RuntimeError):
        verification.verify_replace_snapshot(payload(2), digest)


def test_snapshot_ignores_item_order_but_preserves_all_values():
    snapshot = {"items": [{"id": "b", "quantity": 1}, {"id": "a", "quantity": 2}]}
    reordered = {"items": list(reversed(snapshot["items"]))}
    assert verification.canonical_snapshot(snapshot) == verification.canonical_snapshot(reordered)
    changed = {"items": [{"id": "a", "quantity": 2}, {"id": "b", "quantity": 3}]}
    assert verification.canonical_snapshot(snapshot) != verification.canonical_snapshot(changed)


def unresolved_issue():
    return {"source_name": "Fish", "normalized_name": "fish", "issue_key": "fish", "reason": "candidate_review_required",
            "suggested_patch": {"name": "Fish", "unit_type": "cut", "qty_per_serving": 1, "temp_type": None,
                "daypart": "朝食", "category": "主菜", "bag_max_qty": None, "bag_max_unit": None, "condiments": []},
            "candidates": [{"id": "not-selected", "name": "Fish candidate", "normalized_name": "fishcandidate",
                "revision": 1, "unit_type": "cut", "qty_per_serving": 1, "temp_type": None,
                "daypart": "朝食", "category": "主菜", "bag_max_qty": None, "bag_max_unit": None, "condiments": []}]}


def saved_receipt():
    items = [{"name": "Fish" if index == 0 else f"Fish {index}", "daypart": ("朝食", "昼食", "夕食")[index // 4],
              "category": "主菜", "diet_type": "regular", "unit_type": "cut", "temp_type": None,
              "qty_per_serving": 1, "_inferred_master_fields": []} for index in range(12)]
    review = {"issues": [], "master_diffs": [], "resolutions": [], "item_count": 12,
              "input_snapshot": {"revision": 3, "digest": "b" * 64},
              "plans": {item["name"]: {"action": "existing", "menu_master_id": f"master-{index}",
                  "seed_fields": {key: item[key] for key in ("unit_type", "temp_type", "qty_per_serving", "daypart", "category")}}
                  for index, item in enumerate(items)}, "items": items,
              "entries": [{"name": item["name"], "menu_date": "2026-10-01", "daypart": item["daypart"],
                  "category": item["category"], "diet_type": item["diet_type"], "slot_index": index % 4}
                  for index, item in enumerate(items)], "scope_override": "FAC-A"}
    return {"id": "saved-A", "month_id": "2026-10", "state": "review", "revision": "captured-receipt-3",
            "input_revision": "captured-input-3", "current_input_revision": "captured-input-3",
            "source": {"filename": "original.xlsm", "content_sha256": "a" * 64,
                       "file_uri": "gs://fixture/immutable.xlsm", "sheet_name": "actual-sheet", "scope_override": "FAC-A",
                       "diet_types": None, "media_type": "application/vnd.ms-excel.sheet.macroEnabled.12"},
            "review": review, "current_review": review}


@pytest.mark.parametrize("field", ["file_uri", "filename", "content_sha256", "sheet_name", "scope_override", "diet_types", "media_type"])
def test_script_refuses_missing_recorded_source(field):
    receipt = saved_receipt()
    del receipt["source"][field]
    with pytest.raises(RuntimeError):
        verification.validate_receipt(receipt, month="2026-10", filename="original.xlsm", digest="a" * 64, scope="FAC-A", diet_types=None)


def test_normal_script_receipt_matches_actual_nested_producer_validator():
    from types import SimpleNamespace
    from src.services import menu_service
    receipt = saved_receipt()
    menu_service._validate_import_receipt(SimpleNamespace(id=receipt["id"], month_id=receipt["month_id"], state=receipt["state"],
        revision=3, source_json=receipt["source"], review_json=receipt["review"]))
    assert receipt["review"]["item_count"] == len(receipt["review"]["items"]) == 12
    assert len(receipt["review"]["entries"]) == 12


@pytest.mark.parametrize("field", ["issues", "plans", "items", "entries", "item_count", "master_diffs", "resolutions", "scope_override", "input_snapshot"])
@pytest.mark.parametrize("view", ["review", "current_review"])
def test_script_refuses_missing_saved_or_current_review(view, field):
    receipt = saved_receipt()
    receipt[view] = {key: value for key, value in receipt[view].items() if key != field}
    with pytest.raises(RuntimeError):
        verification.review_material(receipt)


def test_script_preserves_authored_null_and_zero():
    receipt = saved_receipt()
    receipt["source"]["scope_override"] = None
    receipt["review"]["scope_override"] = None
    receipt["review"]["item_count"] = 0
    receipt["review"].update({"items": [], "entries": [], "plans": {}})
    verification.validate_receipt(receipt, month="2026-10", filename="original.xlsm", digest="a" * 64, scope=None, diet_types=None)
    assert receipt["source"]["diet_types"] is None
    assert verification.review_material(receipt)["current_review"]["item_count"] == 0


@pytest.mark.parametrize("missing", ["scope_override", "diet_types"])
def test_missing_author_selection_stops_upload_before_request(monkeypatch, missing):
    case = {"month": "2026-10", "scope_override": None, "diet_types": None}
    del case[missing]
    monkeypatch.setattr(verification, "request", lambda *_args, **_kwargs: pytest.fail("no request without authored scope/food types"))
    with pytest.raises(RuntimeError, match="absence is not null"):
        verification.upload(case, b"original")


@pytest.mark.parametrize("diets", [None, ["regular"], ["soft", "mixer"]])
def test_exact_authored_diets_are_sent_and_receipt_mismatch_stops(monkeypatch, diets):
    from urllib.parse import parse_qs, urlparse
    calls = []
    monkeypatch.setattr(verification, "request", lambda *args, **_kwargs: calls.append(args))
    verification.upload({"month": "2026-10", "scope_override": None, "diet_types": diets}, b"original")
    query = parse_qs(urlparse(calls[0][0]).query)
    assert "scope_type" not in query and "scope_value" not in query
    if diets is None:
        assert "diet_types" not in query
    else:
        assert json.loads(query["diet_types"][0]) == diets
    receipt = saved_receipt()
    receipt["source"]["diet_types"] = diets
    verification.validate_receipt(receipt, month="2026-10", filename="original.xlsm", digest="a" * 64,
        scope="FAC-A", diet_types=diets)
    receipt["source"]["diet_types"] = ["mixer"] if diets != ["mixer"] else None
    with pytest.raises(RuntimeError, match="exact original/month/scope"):
        verification.validate_receipt(receipt, month="2026-10", filename="original.xlsm", digest="a" * 64,
            scope="FAC-A", diet_types=diets)


def test_saved_review_and_apply_use_only_captured_then_own_tokens():
    receipt = saved_receipt()
    approval = {**copy.deepcopy(verification.review_material(receipt)), "resolutions": []}
    calls = []
    def send(path, body=None, content_type=None, *, revision=None):
        calls.append((path, body, content_type, revision))
        if path.endswith("/review"):
            return {**receipt, "revision": "own-review-4"}
        return {"created": True, "item_count": 12, "receipt": {**receipt, "state": "applied"},
                "snapshot": {"menu": {"id": "2026-10"}, "source_month_id": "2026-10",
                             "items": [], "entries": [], "revision": "own-month-4"}}
    result = verification.apply_saved_import(receipt, approval, send)
    assert result["created"] is True
    assert result["item_count"] == 12
    assert calls[0][0] == "/monthly-menus/2026-10/imports/saved-A/review"
    assert calls[0][3] == "captured-receipt-3"
    assert json.loads(calls[0][1]) == {"resolutions": [], "input_revision": "captured-input-3"}
    assert calls[1] == ("/monthly-menus/2026-10/imports/saved-A/apply", b"", None, "own-review-4")
    assert receipt["revision"] == "captured-receipt-3"


@pytest.mark.parametrize("field", ["receipt_id", "month_id", "source", "revision", "input_revision", "current_review"])
def test_changed_review_material_stops_without_command_or_latest_read(field):
    receipt = saved_receipt()
    approval = {**copy.deepcopy(verification.review_material(receipt)), "resolutions": []}
    approval[field] = None
    def unexpected(*args, **kwargs):
        pytest.fail("missing/stale operator review must not dispatch any request")
    with pytest.raises(RuntimeError, match="Explicit operator review"):
        verification.apply_saved_import(receipt, approval, unexpected)


@pytest.mark.parametrize("approval", [None, {}, {"resolutions": []}])
def test_absent_operator_approval_is_not_inferred_from_production_master(approval):
    def unexpected(*args, **kwargs):
        pytest.fail("no automatic approval, first candidate or business retry")
    with pytest.raises(RuntimeError, match="Explicit operator review"):
        verification.apply_saved_import(saved_receipt(), approval, unexpected)


@pytest.mark.parametrize("change", ["issues", "input", "source", "target"])
def test_unresolved_or_wrong_own_review_stops_before_apply(change):
    receipt = saved_receipt()
    approval = {**copy.deepcopy(verification.review_material(receipt)), "resolutions": []}
    own = copy.deepcopy(receipt)
    if change == "issues":
        own["current_review"] = copy.deepcopy(own["review"])
        own["current_review"]["issues"] = [unresolved_issue()]
        del own["current_review"]["plans"]["Fish"]
    elif change == "input":
        own["current_input_revision"] = "unseen-input"
    elif change == "source":
        own["source"]["file_uri"] = "gs://fixture/other.xlsm"
    else:
        own["month_id"] = "2026-11"
    calls = []
    def send(path, *args, **kwargs):
        calls.append(path)
        return own
    with pytest.raises(RuntimeError, match="Own saved review"):
        verification.apply_saved_import(receipt, approval, send)
    assert calls == ["/monthly-menus/2026-10/imports/saved-A/review"]


@pytest.mark.parametrize("field", ["id", "month_id", "filename", "content_sha256", "scope_override", "sheet_name"])
def test_saved_receipt_cannot_alias_another_original_or_facility(field):
    receipt = saved_receipt()
    if field in {"id", "month_id"}:
        receipt[field] = "2026-11"
    else:
        receipt["source"][field] = "different"
    with pytest.raises(RuntimeError, match="exact original/month/scope"):
        verification.validate_receipt(receipt, month="2026-10", filename="original.xlsm", digest="a" * 64,
                                      scope="FAC-A", diet_types=None, sheet_name="actual-sheet", import_id="saved-A")


def test_upload_multipart_contains_only_original_and_explicit_query_selection(monkeypatch):
    calls = []
    monkeypatch.setattr(verification, "request", lambda *args, **kwargs: calls.append((args, kwargs)) or saved_receipt())
    verification.upload({"month": "2026-10", "sheet_name": "actual-sheet", "scope_override": "TAG:explicit", "diet_types": ["soft", "mixer"]}, b"original")
    path, body, content_type = calls[0][0]
    assert "sheet_name=actual-sheet" in path and "scope_type=tag" in path and "scope_value=explicit" in path
    assert "diet_types=" in path
    assert b"review_resolutions" not in body
    assert b"original" in body
    assert content_type.startswith("multipart/form-data;")


def test_apply_transport_is_post_even_with_no_json_body(monkeypatch):
    monkeypatch.setenv("VERIFICATION_TOKEN", "fixture-auth")
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"created":true}'
    def open_request(req, timeout):
        assert req.get_method() == "POST"
        assert req.get_header("If-match") == "own-review-4"
        assert req.data == b""
        return Response()
    monkeypatch.setattr(verification.urllib.request, "urlopen", open_request)
    assert verification.request("/monthly-menus/2026-10/imports/saved-A/apply", b"", revision="own-review-4")["created"] is True


@pytest.mark.parametrize("field", ["issues", "master_diffs"])
def test_unresolved_differences_require_operator_selection_even_with_matching_review(field):
    receipt = saved_receipt()
    receipt["current_review"] = copy.deepcopy(receipt["review"])
    if field == "issues":
        receipt["current_review"][field] = [unresolved_issue()]
        del receipt["current_review"]["plans"]["Fish"]
    else:
        receipt["current_review"][field] = [{"source_name": "Fish",
            "current_master": {"id": "master-0", "revision": 1, "name": "Fish", "normalized_name": "fish", "unit_type": "cut",
                "qty_per_serving": 1, "bag_max_qty": None, "bag_max_unit": None, "temp_type": None, "daypart": "朝食", "category": "主菜", "condiments": []},
            "field_diffs": [{"field": "qty_per_serving", "label": "量", "monthly_value": 2, "master_value": 1}]}]
    approval = {**copy.deepcopy(verification.review_material(receipt)), "resolutions": []}
    def unexpected(*args, **kwargs):
        pytest.fail("exact review material must not automatically choose or approve a difference")
    with pytest.raises(RuntimeError, match="Unresolved difference"):
        verification.apply_saved_import(receipt, approval, unexpected)


def test_exact_operator_resolution_is_saved_without_quantity_or_candidate_supplementation():
    receipt = saved_receipt()
    receipt["current_review"] = copy.deepcopy(receipt["review"])
    receipt["current_review"]["issues"] = [unresolved_issue()]
    del receipt["current_review"]["plans"]["Fish"]
    choice = {"source_name": "Fish", "action": "existing", "menu_master_id": "operator-selected", "qty_per_serving": 0}
    approval = {**copy.deepcopy(verification.review_material(receipt)), "resolutions": [choice]}
    own_review = None
    def send(path, body=None, content_type=None, *, revision=None):
        nonlocal own_review
        if path.endswith("/review"):
            assert json.loads(body)["resolutions"] == [choice]
            own = copy.deepcopy(receipt)
            own["current_review"]["issues"] = []
            own["current_review"]["plans"]["Fish"] = {"action": "existing", "menu_master_id": "operator-selected",
                "seed_fields": {"unit_type": "cut", "qty_per_serving": 1, "temp_type": None, "daypart": "朝食", "category": "主菜"}}
            own["current_review"]["resolutions"] = [choice]
            own["review"] = copy.deepcopy(own["current_review"])
            own["revision"] = "own-review-4"
            own_review = own
            return own
        assert revision == "own-review-4"
        assert own_review is not None
        return {"created": True, "receipt": {**own_review, "state": "applied"}, "snapshot": {
            "menu": {"id": "2026-10"}, "source_month_id": "2026-10", "items": [], "entries": [], "revision": "own-month"}}
    assert verification.apply_saved_import(receipt, approval, send)["created"] is True


@pytest.mark.parametrize("mode", ["explicit", "missing", "stale"])
def test_actual_verification_main_preserves_assertions_and_stops_before_unapproved_commands(monkeypatch, tmp_path, mode):
    raw = b"verified archived workbook fixture"
    digest = hashlib.sha256(raw).hexdigest()
    receipt = saved_receipt()
    receipt["source"]["content_sha256"] = digest
    approval = {**copy.deepcopy(verification.review_material(receipt)), "resolutions": []}
    if mode == "stale":
        approval["input_revision"] = "unseen-input"
    current = {**payload(), "menu": {"id": "2026-10"},
               "entries": [{"id": str(index)} for index in range(12)]}
    case = {"month": "2026-10", "uri": verification.BUCKET + "explicit-original.xlsm", "sha256": digest,
        "quantity_fields_absent_verified": True, "scope_override": "FAC-A", "diet_types": None, "sheet_name": "actual-sheet",
        "saved_import_id": "saved-A", "expected_entries": 12, "expected_quantities": {"Fish": ["cut", 1]},
        "replace_test_snapshot_sha256": hashlib.sha256(verification.canonical_snapshot(current).encode()).hexdigest()}
    if mode != "missing":
        case["import_review"] = approval
    manifest = {"cases": [case], "production_masters": {}}
    manifest_uri = verification.BUCKET + "explicit-manifest.json"
    monkeypatch.setattr(sys, "argv", ["verify_stg_monthly_menus.py", "--manifest", manifest_uri, "--output-dir", str(tmp_path)])
    monkeypatch.setattr(verification, "download", lambda uri: json.dumps(manifest).encode() if uri == manifest_uri else raw)
    calls = []
    def request(path, body=None, content_type=None, *, revision=None):
        calls.append((path, body, revision))
        if path == "/menu-masters?limit=10000":
            return {"items": [{"id": "master", **MASTER["master"]}]}
        if path == "/monthly-menus/2026-10":
            return copy.deepcopy(current)
        if path.endswith("/imports/saved-A"):
            return copy.deepcopy(receipt)
        if path.endswith("/review"):
            assert revision == "captured-receipt-3"
            return {**copy.deepcopy(receipt), "revision": "own-review-4"}
        assert path.endswith("/apply") and body == b"" and revision == "own-review-4"
        return {"created": True, "replaced": True, "item_count": 12,
                "receipt": {**receipt, "state": "applied"},
                "snapshot": {**current, "source_month_id": "2026-10", "revision": "own-month"}}
    monkeypatch.setattr(verification, "request", request)
    if mode == "explicit":
        verification.main()
        summary = json.loads((tmp_path / "summary.json").read_text())["cases"][0]
        assert summary["checked_existing_items"] == 1
        assert summary["sha256"] == digest
        assert [path for path, body, revision in calls if body is not None] == [
            "/monthly-menus/2026-10/imports/saved-A/review", "/monthly-menus/2026-10/imports/saved-A/apply"]
    else:
        with pytest.raises(RuntimeError, match="Explicit operator review"):
            verification.main()
        assert all(body is None for path, body, revision in calls)
        assert not (tmp_path / "2026-10/upload.json").exists()
    assert json.loads((tmp_path / "2026-10/saved-import.json").read_text())["id"] == "saved-A"
