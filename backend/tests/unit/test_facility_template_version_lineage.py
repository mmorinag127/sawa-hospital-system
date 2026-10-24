from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4
from copy import deepcopy
import pytest

from src.db import engine, session_scope
from src.models.facility import Facility
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import facility_template_version_service, order_service, output_builder
from src.services.canonical_facility_store import CanonicalStoreError


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


def _registered_config(facility_id: str) -> dict:
    return {
        "facility_id": facility_id,
        "fax_template_id": "fax_layout_regular_forbidden_v1",
        "fax_template": {
            "template_id": "fax_layout_regular_forbidden_v1",
            "columns": [
                {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
                {"index": 2, "role": "menu_name", "header": "献立", "source_index": 2},
                {
                    "index": 3,
                    "role": "quantity",
                    "header": "常食",
                    "diet_type": "regular",
                    "area_id": "X",
                    "source_index": 3,
                },
                {"index": 4, "role": "note", "header": "備考欄", "source_index": 4},
            ],
        },
    }


def test_template_columns_missing_source_index_is_blocker() -> None:
    columns = (
        [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "aux", "header": "合計"},
            {"index": 2, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
        ]
    )

    validation = facility_template_version_service.validate_template_columns(columns)

    assert "template_source_index_missing" in validation["errors"]
    assert all(column.get("source_index") is None for column in columns)
    original = deepcopy(columns)
    with pytest.raises(CanonicalStoreError, match="authored_template_columns_invalid"):
        facility_template_version_service.normalize_template_columns(columns)
    assert columns == original

    validation_after_placeholder = facility_template_version_service.validate_template_columns(
        (
            [
                {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                {"index": 1, "role": "quantity", "header": "-", "diet_type": "placeholder", "area_id": "X", "source_index": 1},
                {"index": 2, "role": "note", "header": "備考欄"},
            ]
        )
    )
    assert "template_source_index_missing" in validation_after_placeholder["errors"]


def test_save_path_repairs_missing_source_index_from_reference_and_next_available() -> None:
    reference_columns = facility_template_version_service.normalize_template_columns(
        [
            {"index": 0, "role": "date", "header": "日付", "source_index": 0},
            {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
            {"index": 2, "role": "menu_name", "header": "メニュー", "source_index": 3},
            {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 4},
        ]
    )
    draft_columns = (
        [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "menu_name", "header": "メニュー"},
            {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "role": "quantity", "header": "肉禁", "diet_type": "no_meat", "area_id": "X"},
        ]
    )

    original = deepcopy((draft_columns, reference_columns))
    with pytest.raises(CanonicalStoreError, match="legacy_source_index_repair_disabled"):
        facility_template_version_service._repair_missing_source_indexes(
            draft_columns, reference_columns=reference_columns)
    assert (draft_columns, reference_columns) == original
    # The component author explicitly declares all positions; none is inferred.
    repaired = facility_template_version_service.normalize_template_columns([
        {**column, "source_index": position}
        for column, position in zip(draft_columns, [0, 1, 3, 4, 5], strict=True)])

    assert [column.get("source_index") for column in repaired] == [0, 1, 3, 4, 5]
    assert facility_template_version_service.validate_template_columns(repaired)["errors"] == []


def test_template_column_normalization_preserves_explicit_header_group() -> None:
    columns = facility_template_version_service.normalize_template_columns(
        [
            {"index": 0, "role": "date", "header": "日付", "source_index": 0},
            {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
            {"index": 2, "role": "menu_name", "header": "メニュー", "source_index": 3},
            {
                "index": 3,
                "role": "quantity",
                "header": "2F",
                "header_group": "常食",
                "diet_type": "regular",
                "area_id": "2F",
                "source_index": 4,
            },
            {
                "index": 4,
                "role": "quantity",
                "header": "3F",
                "header_group": "常食",
                "diet_type": "regular",
                "area_id": "3F",
                "source_index": 5,
            },
        ]
    )

    assert columns[3]["header"] == "2F"
    assert columns[3]["header_group"] == "常食"
    assert columns[3]["diet_type"] == "regular"
    assert columns[3]["area_id"] == "2F"
    assert columns[4]["header"] == "3F"
    assert columns[4]["header_group"] == "常食"


def _declare_dated_versions(monkeypatch, tmp_path, facility_id, periods):
    import json
    from src.services.template_field_schema_service import canonical_field_name_from_template_column

    base = _registered_config(facility_id)
    base.update(facility_name="Explicit dated component facility", areas=[])
    for column in base["fax_template"]["columns"]:
        column["name"] = canonical_field_name_from_template_column(column)
    versions = []
    for index, (start, end, template_id, name) in enumerate(periods):
        config = deepcopy(base)
        config["facility_name"] = name
        config["fax_template_id"] = config["fax_template"]["template_id"] = template_id
        versions.append({"version_id": f"dated-{index}", "valid_from": start,
                         "valid_to": end, "facility": config})
    path = tmp_path / "dated-template-source.json"
    path.write_text(json.dumps({"facilities": [{**base, "versions": versions}]}))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    return path


def test_multiple_active_template_versions_are_blocker(monkeypatch, tmp_path) -> None:
    facility_id = _id("FAC")
    source = _declare_dated_versions(monkeypatch, tmp_path, facility_id, [
        ("2026-07-01", None, "fax_layout_regular_forbidden_v1", "Ambiguous Template Facility"),
        ("2026-07-01", None, "fax_layout_regular_forbidden_v1", "Ambiguous Template Facility"),
    ])
    original = source.read_bytes()
    with session_scope() as session:
        active, error = facility_template_version_service.resolve_single_active_template_version(
            session, facility_id, effective_date=date(2026, 7, 5))
        assert not session.new and not session.dirty and not session.deleted
    assert active is None
    assert error == "facility_version_period_overlap"
    assert source.read_bytes() == original


def test_effective_date_resolves_one_active_template_version(monkeypatch, tmp_path) -> None:
    facility_id = _id("FAC")
    source = _declare_dated_versions(monkeypatch, tmp_path, facility_id, [
        ("2026-07-01", "2026-07-10", "template-1", "effective-1"),
        ("2026-07-11", None, "template-2", "effective-2"),
    ])
    original = source.read_bytes()
    with session_scope() as session:
        first, first_error = facility_template_version_service.resolve_single_active_template_version(
            session, facility_id, effective_date=date(2026, 7, 5))
        second, second_error = facility_template_version_service.resolve_single_active_template_version(
            session, facility_id, effective_date=date(2026, 7, 12))
        first_template_id = first.template_id if first is not None else None
        second_template_id = second.template_id if second is not None else None
        assert not session.new and not session.dirty and not session.deleted

    assert first_error is None
    assert first_template_id == "template-1"
    assert second_error is None
    assert second_template_id == "template-2"

    with session_scope() as session:
        resolved_config, resolved_version, resolved_error = facility_template_version_service.resolve_facility_config_for_date(
            session, facility_id=facility_id, effective_date=date(2026, 7, 12))
        resolved_template_id = resolved_version.template_id if resolved_version is not None else None
        resolved_version_id = resolved_version.id if resolved_version is not None else None
        assert not session.new and not session.dirty and not session.deleted
    assert resolved_error is None
    assert resolved_template_id == "template-2"
    assert resolved_config["facility_name"] == "effective-2"
    assert resolved_config["facility_template_version_id"] == resolved_version_id
    assert source.read_bytes() == original


def test_effective_date_overlapping_active_template_versions_are_blocker(monkeypatch, tmp_path) -> None:
    facility_id = _id("FAC")
    source = _declare_dated_versions(monkeypatch, tmp_path, facility_id, [
        ("2026-07-01", "2026-07-20", "template-overlap-1", "Overlapping Effective Facility"),
        ("2026-07-02", "2026-07-20", "template-overlap-2", "Overlapping Effective Facility"),
    ])
    original = source.read_bytes()
    with session_scope() as session:
        active, error = facility_template_version_service.resolve_single_active_template_version(
            session, facility_id, effective_date=date(2026, 7, 10))
        assert not session.new and not session.dirty and not session.deleted
    assert active is None
    assert error == "facility_version_period_overlap"
    assert source.read_bytes() == original


def test_output_builder_blocks_template_version_date_mismatch(monkeypatch, tmp_path) -> None:
    import json
    from pathlib import Path
    from src.db import get_engine
    from tests.helpers.newnarrowbaggingfixture import selected_bagging_order, accepted_bagging_order
    from tests.unit.test_disabled_facility_columns import _snapshot

    facility_id = _id("FAC")
    lines = [{"line_id": "dated-output", "date": "2026-07-12", "daypart": "昼",
              "menu_name": "output-effective", "diet_type": "regular", "area_id": "2F",
              "bag_type": "standard", "quantity_original": 0}]
    prepared = selected_bagging_order(monkeypatch, tmp_path, lines,
        facility_id=facility_id, week_start="2026-07-12", week_end="2026-07-18",
        prepare_only=True)
    source = tmp_path / "authored-facilities.json"
    master = json.loads(source.read_text())
    version = master["facilities"][0]["versions"][0]
    version.update(valid_from="2026-07-01", valid_to="2026-07-31")
    source.write_text(json.dumps(master))
    order = accepted_bagging_order(monkeypatch, tmp_path, lines,
        facility_id=facility_id, week_start="2026-07-12", week_end="2026-07-18",
        prepared_sources=True, prepared_original=prepared, prepared_menu_sources=True)
    captured_order = order_service.get_order_for_output_read(order["id"])
    accepted = output_builder._accepted_output_candidate(captured_order)
    assert accepted["lines"][0]["quantity_original"] == 0
    authored = accepted["configured_input"]["selected_authored_version"]
    assert authored["valid_from"] == "2026-07-01"
    assert authored["valid_to"] == "2026-07-31"
    # Positive precondition: the real saved-sheet receipt is usable before the
    # authoritative source is changed to exclude the complete order period.
    output_builder._prepare_output_context(order["id"], include_bags=False)
    original = Path(prepared["path"]).read_bytes()
    captured = deepcopy(accepted)
    before = _snapshot(get_engine())
    version["valid_to"] = "2026-07-10"
    source.write_text(json.dumps(master))
    narrowed_source = source.read_bytes()
    # This is a dated canonical-source change, not an unowned legacy FTV stamp.
    # Both the captured-config consumer and the current output entry must stop.
    with pytest.raises(ValueError, match="^original_template_configured_source_mismatch$"):
        output_builder._resolve_facility_config_for_order(captured_order)
    with pytest.raises(ValueError, match="^original_template_configured_source_mismatch$"):
        output_builder._prepare_output_context(order["id"], include_bags=False)
    assert _snapshot(get_engine()) == before
    assert accepted == captured
    assert Path(prepared["path"]).read_bytes() == original
    assert source.read_bytes() == narrowed_source


def test_backfill_stamps_existing_order_artifact_lineage(monkeypatch) -> None:
    facility_id = _id("FAC")
    order_id = _id("ORD")
    evidence_id = _id("OEV")
    draft_id = _id("ODS")
    snapshot_id = _id("OCS")
    job_id = f"OCR-{order_id}"
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Backfill Facility"))
        session.flush()
        session.add(
            Order(
                id=order_id,
                facility_code=facility_id,
                week_code="2026-04@2026-04-26~2026-04-30",
                status="要確認",
                document_uri=f"file:///{order_id}.pdf",
                message_id=f"msg-{order_id}",
                received_at=datetime.utcnow(),
            )
        )
        session.flush()
        session.add(OcrJob(id=job_id, order_id=order_id, input_reference="file:///input.pdf", status="success"))
        session.add(
            OrderOcrEvidenceRun(
                id=evidence_id,
                order_id=order_id,
                schema_version="test",
                producer_version="test",
                source="test",
                status="ready",
                payload_json={},
                artifact_digest=f"digest-{evidence_id}",
                created_at=datetime.utcnow(),
            )
        )
        session.flush()
        session.add(
            OrderSheetDraft(
                id=draft_id,
                order_id=order_id,
                base_evidence_run_id=evidence_id,
                draft_sheet_json={"fields": ["date_mmdd", "daypart", "menu", "qty.regular"], "rows": [["04/26", "朝", "A", ""]]},
                draft_state="saved",
                blockers_json=[],
                warnings_json=[],
                created_at=datetime.utcnow(),
                edited_at=datetime.utcnow(),
            )
        )
        session.flush()
        session.add(
            OrderConfirmedSnapshot(
                id=snapshot_id,
                order_id=order_id,
                draft_id=draft_id,
                snapshot_digest="digest",
                snapshot_json={"source": "workflow_v2"},
                created_at=datetime.utcnow(),
                confirmed_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id,
                draft_id=draft_id,
                state="sheet_saved",
                secondary_actions_json={"workflow_v2": {"facility_id": facility_id, "ocr_job_id": job_id}},
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderCurrentState(
                order_id=order_id,
                draft_id=draft_id,
                evidence_run_id=evidence_id,
                snapshot_version="v2",
                state_json={"draft_id": draft_id, "evidence_run_id": evidence_id},
                updated_at=datetime.utcnow(),
            )
        )

    from tests.unit.test_disabled_facility_columns import _snapshot
    before = _snapshot(engine)
    for dry_run in (True, False):
        with session_scope() as session:
            with pytest.raises(CanonicalStoreError) as refused:
                facility_template_version_service.backfill_facility_template_version_lineage(session, dry_run=dry_run)
            assert refused.value.detail["error"] == "legacy_canonical_lineage_backfill_disabled"
            assert not session.new and not session.dirty and not session.deleted
        assert _snapshot(engine) == before

    with session_scope() as session:
        version_id = session.get(Order, order_id).template_version_id
        assert version_id is None
        assert session.get(OcrJob, job_id).template_version_id == version_id
        assert session.get(OrderOcrEvidenceRun, evidence_id).template_version_id == version_id
        assert session.get(OrderSheetDraft, draft_id).template_version_id == version_id
        assert session.get(OrderConfirmedSnapshot, snapshot_id).template_version_id == version_id
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow.template_version_id == version_id
        assert "template_version_id" not in workflow.secondary_actions_json["workflow_v2"]
        current_state = session.get(OrderCurrentState, order_id)
        assert current_state.template_version_id == version_id
        assert "template_version_id" not in current_state.state_json


def test_backfill_does_not_stamp_ocr_job_by_name_without_explicit_order_lineage(monkeypatch) -> None:
    facility_id = _id("FAC")
    order_id = _id("ORD")
    job_id = f"OCR-{order_id}"
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Backfill Facility"))
        session.flush()
        session.add(
            Order(
                id=order_id,
                facility_code=facility_id,
                week_code="2026-04@2026-04-26~2026-04-30",
                status="要確認",
                document_uri=f"file:///{order_id}.pdf",
                message_id=f"msg-{order_id}",
                received_at=datetime.utcnow(),
            )
        )
        session.add(OcrJob(id=job_id, input_reference="file:///input.pdf", status="success"))

    from tests.unit.test_disabled_facility_columns import _snapshot
    before = _snapshot(engine)
    with session_scope() as session:
        with pytest.raises(CanonicalStoreError) as refused:
            facility_template_version_service.backfill_facility_template_version_lineage(session)
        assert refused.value.detail["error"] == "legacy_canonical_lineage_backfill_disabled"
    assert _snapshot(engine) == before
    with session_scope() as session:
        assert session.get(Order, order_id).template_version_id is None
        assert session.get(OcrJob, job_id).template_version_id is None


def test_current_state_cache_requires_matching_template_version(monkeypatch, tmp_path) -> None:
    from src.db import get_engine
    from tests.helpers.newnarrowbaggingfixture import selected_bagging_order
    from tests.unit.test_disabled_facility_columns import _snapshot
    from src.services import order_workflow_v2_service as workflow

    order, sheet = selected_bagging_order(monkeypatch, tmp_path, [{
        "line_id": "blank-quantity", "date": "2026-04-26", "daypart": "朝",
        "menu_name": "大豆のトマト煮", "diet_type": "regular", "area_id": "2F",
        "bag_type": "standard", "quantity_original": None,
    }], facility_id=_id("FAC"))
    current, error = workflow.get_workflow(order["id"])
    assert error is None
    saved, error = workflow.save_sheet(order["id"], sheet=sheet,
        expected_revision=current["revision"])
    assert error is None
    stored, error = workflow.get_saved_sheet(order["id"])
    assert error is None
    context = order_service.get_current_sheet_context(order["id"])
    assert context is not None
    stale = deepcopy(context)
    stale["template_version_id"] = "foreign-cache-template"
    before = _snapshot(get_engine())
    assert order_service._persisted_current_state_is_reusable(
        stale, order_id=order["id"], refresh_draft_from_semantic=False) is False
    rebuilt = order_service._build_current_sheet_context_uncached(
        order["id"], refresh_draft_from_semantic=False, upgrade_generic_from_sheet=True)
    assert rebuilt["template_version_id"] == stored["template_version_id"]
    assert rebuilt["draft_id"] == stored["saved_sheet_id"]
    again, error = workflow.get_saved_sheet(order["id"])
    assert error is None
    assert again["sheet"] == stored["sheet"] == sheet
    assert _snapshot(get_engine()) == before


def test_resolved_config_digest_change_replaces_active_template_version(monkeypatch) -> None:
    facility_id = _id("FAC")
    stale_version_id = _id("FTV")
    resolved_config = _registered_config(facility_id)
    normalized_columns = facility_template_version_service.normalize_template_columns(
        resolved_config["fax_template"]["columns"]
    )
    fresh_digest = facility_template_version_service.template_digest(
        template_id=resolved_config["fax_template_id"],
        columns=normalized_columns,
    )
    assert fresh_digest != "stale-digest"
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Digest Facility"))
        session.flush()
        session.add(
            FacilityTemplateVersion(
                id=stale_version_id,
                facility_id=facility_id,
                version="1",
                status="active",
                template_id=resolved_config["fax_template_id"],
                source="stale-test",
                columns_json=[],
                cells_json=[],
                template_digest="stale-digest",
                validation_json={},
                created_at=datetime.utcnow(),
                activated_at=datetime.utcnow(),
            )
        )

    from tests.unit.test_disabled_facility_columns import _snapshot
    before = _snapshot(engine)
    with session_scope() as session:
        with pytest.raises(CanonicalStoreError) as refused:
            facility_template_version_service.ensure_active_template_version_from_resolved_config(
                session, facility_id=facility_id, facility_config=resolved_config, created_by="digest-test")
        assert refused.value.detail["error"] == "legacy_current_config_lineage_stamp_disabled"
        assert not session.new and not session.dirty and not session.deleted
        stale = session.get(FacilityTemplateVersion, stale_version_id)
        assert stale is not None
        assert stale.status == "active"
        assert stale.template_digest == "stale-digest"
        assert stale.template_digest != fresh_digest
    assert _snapshot(engine) == before
