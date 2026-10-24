from copy import deepcopy

import pytest

from src.services import config_service


@pytest.fixture(autouse=True)
def no_secondary_facility_source(monkeypatch):
    def forbidden_secondary_source(facility_id):
        raise AssertionError("canonical getter must not use a second DB/legacy source")
    monkeypatch.setattr(config_service, "get_facility_by_id", forbidden_secondary_source)


@pytest.mark.parametrize("value", [None, {}, {"columns": []}, {"columns": [{"index": 0}]}])
def test_missing_or_incomplete_authored_template_never_uses_base(monkeypatch, value):
    master = {
        "fax_template_base": {"columns": [{"index": 0, "role": "quantity", "source_index": 0}]},
        "facilities": [{"facility_id": "facility", "fax_template": value, "fax_template_override": {"columns": [{"index": 0, "role": "quantity"}]}}],
    }
    monkeypatch.setattr(config_service, "load_facility_master", lambda: master)
    with pytest.raises(ValueError, match="authored_fax_template_(missing|incomplete)"):
        config_service.get_facility_config("facility")


def test_authored_zero_and_false_remain_unchanged_without_normalization(monkeypatch):
    template = {
        "columns": [{"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"}],
        "table_box": [0, 0, 100, 100], "large_cell_mode": False,
        "unknown_authored_value": {"zero": 0, "nullable": None, "empty": ""},
    }
    original = deepcopy(template)
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {
        "fax_template_base": {"large_cell_mode": True},
        "facilities": [{"facility_id": "facility", "fax_template": template}],
    })
    result = config_service.get_facility_config_for_template("facility", None)
    assert result["fax_template"] == original
    assert result["fax_template"] is not template
    assert template == original


def test_ambiguous_facility_blocks_instead_of_first_candidate(monkeypatch):
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {
        "facilities": [{"facility_id": "facility"}, {"facility_id": "facility"}],
    })
    with pytest.raises(ValueError, match="facility_template_ambiguous"):
        config_service.get_facility_config("facility")


def test_missing_authored_key_blocks_without_db_or_base_template(monkeypatch):
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {
        "facilities": [{"facility_id": "facility"}],
        "fax_template_base": {"columns": [{"role": "quantity", "source_index": 0}]},
    })
    with pytest.raises(ValueError, match="authored_fax_template_missing"):
        config_service.get_facility_config("facility")


def test_exact_template_selection_never_injects_id_or_db_columns(monkeypatch):
    template = {"template_id": "selected", "columns": [{"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"}]}
    facility = {"facility_id": "facility", "fax_template_id": "selected", "fax_template": template}
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {"facilities": [facility]})
    result = config_service.get_facility_config_for_template("facility", "selected")
    assert result == facility
    assert "fax_template_ids" not in result
    with pytest.raises(ValueError, match="authored_fax_template_id_mismatch"):
        config_service.get_facility_config_for_template("facility", "old")


def test_multiple_template_ids_require_matching_authored_selection(monkeypatch):
    template = {"template_id": "two", "columns": [{"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"}]}
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {
        "facilities": [{"facility_id": "facility", "fax_template_ids": ["one", "two"], "fax_template": template}],
    })
    with pytest.raises(ValueError, match="authored_fax_template_ambiguous"):
        config_service.get_facility_config("facility")
    assert config_service.get_facility_config_for_template("facility", "two")["fax_template"] == template
    with pytest.raises(ValueError, match="authored_fax_template_id_mismatch"):
        config_service.get_facility_config_for_template("facility", "one")


def test_embedded_manifest_template_cannot_replace_authored_template(monkeypatch):
    from src.services.hakodate_cell_ocr_batch_service import _fax_template_for_manifest_item

    template = {"page": 1, "columns": [{"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"}]}
    captured = {"facility": {"facility_id": "facility", "fax_template": template}, "policy": {}}
    monkeypatch.setattr(config_service, "load_facility_master",
                        lambda: pytest.fail("recognition recaptured a different master"))
    item = {"configured_input": captured, "fax_template": {"columns": []}}
    original = deepcopy(item)
    assert _fax_template_for_manifest_item(item, "facility") == template
    assert item == original
    with pytest.raises(ValueError, match="^hakodate_captured_facility_mismatch$"):
        _fax_template_for_manifest_item(item, "another-facility")
    assert item == original
    with pytest.raises(ValueError, match="^hakodate_captured_config_required$"):
        _fax_template_for_manifest_item({"fax_template": template}, "facility")
    with pytest.raises(ValueError, match="authored_fax_template_missing"):
        _fax_template_for_manifest_item({"configured_input": {"facility": {"facility_id": "facility"}, "policy": {}},
                                         "fax_template": template}, "facility")


def test_pipeline_missing_template_stops_before_any_pipeline_side_effect(monkeypatch):
    from src.services import ocr_pipeline_service

    monkeypatch.setattr(config_service, "load_facility_master", lambda: {
        "facilities": [{"facility_id": "facility"}],
    })
    def forbidden_pipeline_start():
        raise AssertionError("pipeline must not start without authored template")
    monkeypatch.setattr(ocr_pipeline_service, "_get_pipeline_semaphore", forbidden_pipeline_start)
    with pytest.raises(ValueError, match="authored_fax_template_missing"):
        ocr_pipeline_service.run_ocr_pipeline(pdf_bytes=b"", job_id="actual", facility_id="facility")


def test_db_version_columns_do_not_replace_authored_template(monkeypatch):
    from types import SimpleNamespace
    from src.services import facility_template_version_service

    template = {"columns": [{"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"}]}
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {
        "facilities": [{"facility_id": "facility", "fax_template": template}],
    })
    monkeypatch.setattr(facility_template_version_service, "serialize_template_version", lambda version: {"id": version.id})
    result = facility_template_version_service._resolved_config_from_parts(
        facility_id="facility", facility_name=None,
        config=config_service.get_facility_config("facility"), template_id=None,
        columns=[{"index": 9, "role": "quantity", "source_index": 9}],
        version=SimpleNamespace(id="version", config_json={"fax_template": {"columns": []}}),
    )
    assert result["fax_template"] == template
    assert result["facility_template_version_id"] == "version"
    assert "facility_template_version_id" not in result["fax_template"]


def test_version_resolution_uses_same_captured_raw_snapshot(monkeypatch):
    from types import SimpleNamespace
    from src.services import facility_template_version_service

    captured = {
        "facility_id": "facility", "unknown": {"quantity": 0, "value": None},
        "fax_template_id": "selected",
        "fax_template": {"template_id": "selected", "columns": [
            {"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"},
        ]},
    }
    def reread():
        raise AssertionError("canonical snapshot reread")
    monkeypatch.setattr(config_service, "load_facility_master", reread)
    monkeypatch.setattr(facility_template_version_service, "serialize_template_version", lambda version: {"id": version.id})
    result = facility_template_version_service._resolved_config_from_parts(
        facility_id="facility", facility_name="DB name", config=captured,
        template_id="selected", columns=[], version=SimpleNamespace(id="version"),
    )
    assert result["fax_template"] == captured["fax_template"]
    assert result["unknown"] == captured["unknown"]
    assert "facility_name" not in result


@pytest.mark.parametrize("getter", [
    lambda: config_service.get_facility_config("facility"),
    lambda: config_service.get_facility_config_for_template("facility", "selected"),
])
def test_single_full_raw_snapshot_preserves_all_input_keys(monkeypatch, getter):
    facility = {
        "facility_id": "facility", "fax_template_id": "selected",
        "fax_template": {"template_id": "selected", "columns": [
            {"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"},
        ]},
        "packaging_policy": None, "label_profile": {}, "bag_types": [],
        "unknown": {"zero": 0, "nullable": None, "flag": False},
        "fax_template_override": {"unknown_authored_annotation": None},
    }
    calls = []
    def read_once():
        calls.append(True)
        assert len(calls) == 1
        return {"facilities": [facility], "packaging_policy_base": {"default": True}}
    monkeypatch.setattr(config_service, "load_facility_master", read_once)
    result = getter()
    assert result == facility
    assert result is not facility
    assert result["unknown"] is not facility["unknown"]
    assert calls == [True]


@pytest.mark.parametrize("actual_id", [None, "", "different"])
def test_declared_id_without_matching_actual_template_blocks(monkeypatch, actual_id):
    monkeypatch.setattr(config_service, "load_facility_master", lambda: {"facilities": [{
        "facility_id": "facility", "fax_template_ids": ["selected"],
        "fax_template": {"template_id": actual_id, "columns": [
            {"index": 0, "source_index": 0, "role": "quantity", "diet_type": "normal"},
        ]},
    }]})
    with pytest.raises(ValueError, match="authored_fax_template_id_mismatch"):
        config_service.get_facility_config_for_template("facility", "selected")
