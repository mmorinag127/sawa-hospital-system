"""Dated JSON facility behavior source; execution is explicitly deferred."""

from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path

import pytest

from src.services import canonical_facility_store as store


def test_preview_source_has_independent_generation_and_cannot_invalidate_own_save():
    page = (Path(__file__).resolve().parents[3] / "frontend/src/pages/facilities/[id].tsx").read_text()
    preview = page.split("const resolveVersionDate = async () => {", 1)[1].split("const ownResponse", 1)[0]
    assert "++previewGeneration.current" in preview
    assert "versionGeneration.current" not in preview
    assert "versionBusyOwner.current !== 0" in preview
    save = page.split("const saveVersion = async () => {", 1)[1].split("const resolveVersionDate", 1)[0]
    assert "previewGeneration.current += 1" in save
    assert "generation === versionGeneration.current" in save
    assert "const owns = captureVersionOwnership()" in save
    assert "versionSnapshotRef.current = res.data" in save
    assert "versionBusyOwner.current = 0" in save
    assert 'disabled={versionBusy} value={effectiveDate}' in page


@pytest.mark.parametrize("race,guards", [
    ("read_before_save", ["command === commandGeneration.current", "versionBusyOwner.current === 0"]),
    ("save_before_read", ["versionBusyOwner.current !== 0", "versionBusy"]),
    ("input_during_read", ["input === inputGeneration.current"]),
    ("input_during_save", ["input === inputGeneration.current"]),
    ("same_target_reselected", ["generation === versionGeneration.current"]),
    ("target_changed", ["target === targetRef.current"]),
    ("auth_changed", ["session === browserSessionKey()", "hasActiveSessionAuthHeader()"]),
])
def test_version_read_save_source_ownership_races(race, guards):
    page = (Path(__file__).resolve().parents[3] / "frontend/src/pages/facilities/[id].tsx").read_text()
    ownership = page.split("const captureVersionOwnership = () => {", 1)[1].split("const loadVersions", 1)[0]
    load = page.split("const loadVersions = async () => {", 1)[1].split("const selectVersion", 1)[0]
    save = page.split("const saveVersion = async () => {", 1)[1].split("const resolveVersionDate", 1)[0]
    for guard in guards:
        assert guard in ownership + load + save, race
    assert load.index("if (!current()) return") < load.index("clearVersionEditor()")
    assert "const command = commandGeneration.current" in ownership
    assert "const input = inputGeneration.current" in ownership
    assert "const session = browserSessionKey()" in ownership
    assert "const owns = captureVersionOwnership()" in load
    assert "const owns = captureVersionOwnership()" in save
    assert save.index("++commandGeneration.current") < save.index("captureVersionOwnership()")
    assert save.index("if (!current()) return") < save.index("versionSnapshotRef.current = res.data")
    assert "if (current()) setVersionMessage" in load


@pytest.mark.parametrize("patch", [
    {"table_box": [0.95, 0.9, 0.05, 0.2]}, {"table_box": [0, 0, 0, 1]},
    {"table_box": [0, 0, 1.1, 1]}, {"grid_table_box": [0, 0, float("inf"), 1]},
    {"facility_name_box": [-0.1, 0, 1, 1]}, {"date_boxes": [{"bbox": [0, 1, 1, 0]}]},
    {"warp": {"output_size": [0, 100]}}, {"warp": {"output_size": [100, float("nan")]}},
    {"rois": {"menu_band": [0.9, 0, 0.2, 1]}},
    {"warp": {"output_size": [100, 100]}, "rois": {"table_box": [90, 0, 20, 100]}},
    {"rois": {"qty": {"boxes_row_major": [{"x": 0, "y": 0, "w": 0, "h": 1}]}}},
    {"grid_column_edges": [0, 0.5, 0.4, 1]},
    {"rois": {"qty": {"column_edges": [0, float("inf")]}}},
])
def test_geometry_contract_rejects_invalid_read_and_write(patch):
    record = version()
    record["facility"]["fax_template"].update(patch)
    read = snapshot([record])
    for action in (lambda: store._encode(read.master),
                   lambda: store.resolve_facility_version(read, "F", "2026-01-15")):
        with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
            action()


def test_real_pixel_xywh_roi_pattern_is_not_misread_as_xyxy_or_rewritten():
    record = version()
    record["facility"]["fax_template"].update({
        "warp": {"output_size": [1000, 1200]},
        "rois": {"facility_name_box": [600, 50, 200, 60], "table_box": [50, 200, 900, 900],
                 "qty": {"schema": {"rows": 1, "cols": 1},
                         "boxes_row_major": [{"x": 600, "y": 800, "w": 100, "h": 50}],
                         "column_edges": [0, 0.5, 1], "row_edges": [0, 1]}},
    })
    read = snapshot([record])
    before = deepcopy(read.master)
    store._encode(read.master)
    result = store.resolve_facility_version(read, "F", "2026-01-15")
    assert result["facility"] == record["facility"]
    assert read.master == before


def version(identity="operator-A", start="2026-01-01", end="2026-01-31"):
    return {"version_id": identity, "valid_from": start, "valid_to": end, "unknown": None,
            "facility": {"facility_id": "F", "facility_name": "Recorded name", "areas": [{"name": "No guessed ID"}],
                         "fax_template": {"units": "normalized", "page": 1,
                             "table_box": [0.05, 0.2, 0.95, 0.9], "header_rows": 0,
                             "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
                             "columns": [
                                 {"index": 0, "source_index": 0, "role": "date", "format": "MM/DD"},
                                 {"index": 1, "source_index": 1, "role": "daypart"},
                                 {"index": 2, "source_index": 2, "role": "menu_name"},
                                 {"index": 3, "source_index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X", "semantic": None},
                                 {"index": 4, "source_index": 4, "role": "note"}], "unknown": None},
                         "quantity_setting": 0, "unknown": {"null": None}}}


def snapshot(versions):
    return store.MasterSnapshot({"unknown_root": None, "fax_template_base": {"must_not_inherit": True},
        "facilities": [{"facility_id": "F", "facility_name": "Unversioned reference",
                        "valid_from": None, "valid_to": None, "areas": ["Reference"], "versions": versions}]},
        "fixed-revision", "explicit-test-source")


@pytest.mark.parametrize("field", ["source_index", "index"])
@pytest.mark.parametrize("raw", [True, False, -1, 0.5, 1.0, float("nan"),
                                float("inf"), "0", "bad", [], {}])
def test_exact_column_positions_stop_encode_and_dated_config(field, raw, monkeypatch):
    from src.services import config_service, facility_master_service
    record = version()
    record["facility"]["fax_template"]["columns"][0][field] = raw
    read = snapshot([record])
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: read)
    for action in (lambda: store._encode(read.master),
                   lambda: store.resolve_facility_version(read, "F", "2026-01-15"),
                   lambda: config_service.get_facility_config_for_date("F", "2026-01-15")):
        with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
            action()
    assert type(read.master["facilities"][0]["versions"][0]["facility"]
                ["fax_template"]["columns"][0][field]) is type(raw)


@pytest.mark.parametrize("field", ["source_index", "index"])
@pytest.mark.parametrize("failure", ["missing", "null", "duplicate"])
def test_missing_null_duplicate_explicit_positions_stop(field, failure):
    record = version()
    columns = record["facility"]["fax_template"]["columns"]
    if failure == "missing":
        del columns[0][field]
    elif failure == "null":
        columns[0][field] = None
    else:
        columns[1][field] = columns[0][field]
    read = snapshot([record])
    before = deepcopy(read.master)
    for action in (lambda: store._encode(read.master),
                   lambda: store.resolve_facility_version(read, "F", "2026-01-15")):
        with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
            action()
        assert read.master == before


def test_zero_distinct_source_and_column_positions_preserve_raw_snapshot(monkeypatch):
    from src.services import config_service, facility_master_service
    record = version()
    columns = record["facility"]["fax_template"]["columns"]
    columns[2]["source_index"] = 8
    columns[0]["unknown"] = {"zero": 0, "null": None, "bool": False}
    read = snapshot([record])
    before = deepcopy(read.master)
    decoded = store._decode(store._encode(read.master))
    persisted = store.MasterSnapshot(decoded, read.revision, read.source)
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: persisted)
    result = config_service.get_facility_config_for_date("F", "2026-01-15")
    assert decoded == before
    assert result["config"] == record["facility"]
    assert result["config"]["fax_template"]["columns"][0]["source_index"] == 0
    assert result["config"]["fax_template"]["columns"][2]["index"] == 2
    assert result["config"]["fax_template"]["columns"][2]["source_index"] == 8


def test_optional_source_index_is_not_fabricated_or_newly_required():
    from src.services.facility_template_version_service import validate_template_columns
    columns = [{"index": 0, "source_index": 0, "role": "quantity"},
               {"index": 1, "role": "quantity_change"}]
    before = deepcopy(columns)
    assert validate_template_columns(columns)["errors"] == []
    assert columns == before


@pytest.mark.parametrize("columns", [None, {}, [None], [0]])
def test_malformed_column_container_is_explicit_validation_error(columns):
    from src.services.facility_template_version_service import validate_template_columns
    assert validate_template_columns(columns)["errors"] == ["template_columns_invalid"]


@pytest.mark.parametrize("raw", [True, 0.5, "0", None])
def test_actual_dated_save_rejects_before_replacing_original_json(tmp_path, monkeypatch, raw):
    from src.services import facility_service
    master = snapshot([version()]).master
    path = tmp_path / "master.json"
    original = store._encode(master)
    path.write_bytes(original)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    captured = store.read_snapshot()
    changed = version()
    changed["facility"]["fax_template"]["columns"][0]["source_index"] = raw
    with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
        facility_service.save_facility_version(
            "F", changed["version_id"], changed,
            expected_revision=captured.revision, create=False)
    assert path.read_bytes() == original
    assert store.read_snapshot().revision == captured.revision


@pytest.mark.parametrize("literal", ["true", "0.5", '"0"', "null", "NaN"])
def test_literal_json_source_index_cannot_be_selected(literal):
    data = json.dumps(snapshot([version()]).master).replace('"source_index": 0', '"source_index": ' + literal, 1)
    with pytest.raises(store.CanonicalStoreError):
        read = store.MasterSnapshot(store._decode(data.encode()), "literal-revision", "literal-source")
        store.resolve_facility_version(read, "F", "2026-01-15")


@pytest.mark.parametrize("template", [
    {"template_id": "X"}, {"columns": []}, {"columns": [{"index": 0, "role": "date", "source_index": 0}]},
    {"columns": [{"index": 0, "role": "quantity", "diet_type": "regular", "area_id": "X"}]},
    {"columns": [{"index": 0, "source_index": 0, "role": "quantity"}]},
])
def test_incomplete_authored_template_stops_both_write_and_dated_read(template):
    recorded = version()
    recorded["facility"]["fax_template"] = template
    read = snapshot([recorded])
    before = deepcopy(read.master)
    for action in (lambda: store._encode(read.master),
                   lambda: store.resolve_facility_version(read, "F", "2026-01-15")):
        with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
            action()
        assert read.master == before


@pytest.mark.parametrize("field,value", [("openai_ocr_max_tokens", "bad"),
                                        ("order_form_month_sources", []),
                                        ("invoice_template", {"columns": [{"name": "qty"}]}),
                                        ("packaging_policy_override", {"split_key": "bad"})])
def test_invalid_recorded_config_stops_read_and_write_without_repair(field, value):
    recorded = version()
    recorded["facility"][field] = value
    read = snapshot([recorded])
    for action in (lambda: store._encode(read.master),
                   lambda: store.resolve_facility_version(read, "F", "2026-01-15")):
        with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
            action()


def test_invalid_authored_geometry_and_row_field_mapping_stop():
    for patch in ({"table_box": [0, 1, 2]}, {"warp": {"output_size": [100]}},
                  {"main_ocr_row_fields": ["qty.soft_x"]},
                  {"rois": {"qty": {"schema": {"rows": 2, "cols": 2}, "boxes_row_major": [[0, 0, 1, 1]]}}}):
        recorded = version()
        recorded["facility"]["fax_template"].update(patch)
        read = snapshot([recorded])
        with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
            store._encode(read.master)
        with pytest.raises(store.CanonicalStoreError, match="snapshot_incomplete"):
            store.resolve_facility_version(read, "F", "2026-01-15")


@pytest.mark.parametrize("day", ["2026-01-01", "2026-01-31", date(2026, 1, 15)])
def test_inclusive_boundaries_return_exact_full_version_and_same_revision(day):
    recorded = version()
    read = snapshot([recorded])
    before = deepcopy(read.master)
    result = store.resolve_facility_version(read, "F", day)
    assert result["facility"] == recorded["facility"]
    assert result["version"] == recorded
    assert result["revision"] == read.revision
    assert "must_not_inherit" not in result["facility"]["fax_template"]
    assert "id" not in result["facility"]["areas"][0]
    result["facility"]["quantity_setting"] = 99
    assert read.master == before


@pytest.mark.parametrize("day", [None, "", "2026-02-30", "20260101", "2026-01-01T00:00:00Z", " 2026-01-01", 0,
                                  datetime(2026, 1, 1)])
def test_missing_or_invalid_calendar_date_stops(day):
    with pytest.raises(store.CanonicalStoreError, match="facility_version_date_"):
        store.resolve_facility_version(snapshot([version()]), "F", day)


@pytest.mark.parametrize("day", ["2025-12-31", "2026-02-01", "2026-02-28"])
def test_unrecorded_past_and_gaps_never_use_reference_or_latest(day):
    read = snapshot([version(), version("operator-B", "2026-03-01", None)])
    with pytest.raises(store.CanonicalStoreError, match="facility_version_date_unrecorded"):
        store.resolve_facility_version(read, "F", day)


def test_unversioned_unbounded_reference_is_not_a_recorded_version():
    read = snapshot([])
    del read.master["facilities"][0]["versions"]
    with pytest.raises(store.CanonicalStoreError, match="facility_version_date_unrecorded"):
        store.resolve_facility_version(read, "F", "2026-01-01")


def test_inclusive_shared_boundary_and_duplicate_identity_stop():
    for records, error in [
        ([version(), version("operator-B", "2026-01-31", "2026-02-28")], "period_overlap"),
        ([version(), version("operator-A", "2026-02-01", "2026-02-28")], "identity_invalid"),
    ]:
        with pytest.raises(store.CanonicalStoreError, match=error):
            store.resolve_facility_version(snapshot(records), "F", "2026-01-15")


@pytest.mark.parametrize("patch", [{"valid_from": None}, {"valid_to": ""}, {"valid_to": "2025-12-31"},
                                  {"version_id": None}, {"facility": {"facility_id": "F"}}])
def test_incomplete_definition_stops_without_supplementing_fields(patch):
    with pytest.raises(store.CanonicalStoreError):
        store.resolve_facility_version(snapshot([{**version(), **patch}]), "F", "2026-01-01")


def test_missing_end_and_conflicting_nested_period_are_not_inferred():
    missing = version()
    del missing["valid_to"]
    conflicting = version()
    conflicting["facility"]["valid_from"] = "2025-01-01"
    for record in [missing, conflicting]:
        with pytest.raises(store.CanonicalStoreError):
            store.resolve_facility_version(snapshot([record]), "F", "2026-01-01")


def test_config_common_entry_reads_exactly_one_snapshot(monkeypatch):
    from src.services import config_service, facility_master_service

    read = snapshot([version()])
    calls = []
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: calls.append("read") or read)
    monkeypatch.setattr(config_service, "get_facility_config", lambda *_args, **_kwargs: pytest.fail("unversioned fallback"))
    result = config_service.get_facility_config_for_date("F", "2026-01-10")
    assert calls == ["read"]
    assert result["config"] == version()["facility"]
    assert result["revision"] == "fixed-revision"


def test_version_edit_uses_cas_own_result_and_keeps_reference_and_siblings(monkeypatch):
    from src.services import facility_master_service, facility_service

    read = snapshot([version(), version("operator-B", "2026-02-01", None)])
    original = deepcopy(read.master)
    saved = []
    def mutate(expected, transform):
        assert expected == read.revision
        master = transform(deepcopy(read.master))
        store.validate_facility_versions(master)
        saved.append(master)
        return {"facility_master": master, "revision": "own-revision", "projection": {"status": "reconcile_required", "revision": "own-revision"}}
    monkeypatch.setattr(facility_master_service, "mutate_master", mutate)
    edited = version()
    edited["facility"]["facility_name"] = "Explicit edit"
    response = facility_service.save_facility_version("F", "operator-A", edited, expected_revision=read.revision, create=False)
    assert response["version"] == edited
    assert response["revision"] == "own-revision"
    assert response["versions"][1] == original["facilities"][0]["versions"][1]
    expected = deepcopy(original)
    expected["facilities"][0]["versions"][0] = edited
    assert saved == [expected]
    assert read.master == original
    with pytest.raises(store.CanonicalStoreError, match="edit_target_missing"):
        facility_service.save_facility_version("F", "missing", {**edited, "version_id": "missing"}, expected_revision=read.revision, create=False)


def test_version_command_actual_store_cas_and_second_own_revision(tmp_path, monkeypatch):
    from src.services import facility_master_service, facility_service

    path = tmp_path / "explicit-test-master.json"
    path.write_text(json.dumps(snapshot([version()]).master), encoding="utf-8")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    monkeypatch.setattr(facility_master_service, "_project_snapshot", lambda saved: {
        "status": "reconcile_required", "revision": saved.revision})
    first = store.read_snapshot()
    edited = version()
    edited["facility"]["facility_name"] = "Edited"
    own = facility_service.save_facility_version("F", "operator-A", edited, expected_revision=first.revision, create=False)
    second = facility_service.save_facility_version("F", "operator-A", edited, expected_revision=own["revision"], create=False)
    assert second["version"] == edited
    assert second["revision"] != own["revision"]
    before = path.read_bytes()
    for expected, status in [(None, 428), (first.revision, 409)]:
        with pytest.raises(store.CanonicalStoreError) as error:
            facility_service.save_facility_version("F", "operator-A", edited, expected_revision=expected, create=False)
        assert error.value.status_code == status
        assert path.read_bytes() == before
