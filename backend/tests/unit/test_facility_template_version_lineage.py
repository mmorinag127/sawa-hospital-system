from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.db import Base, engine, session_scope
from src.models.facility import Facility
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import facility_template_version_service, order_service


Base.metadata.create_all(bind=engine)


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
    columns = facility_template_version_service.normalize_template_columns(
        [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
        ]
    )

    validation = facility_template_version_service.validate_template_columns(columns)

    assert "template_source_index_missing" in validation["errors"]
    assert all(column.get("source_index") is None for column in columns)

    validation_after_placeholder = facility_template_version_service.validate_template_columns(
        facility_template_version_service.normalize_template_columns(
            [
                {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                {"index": 1, "role": "quantity", "header": "-", "diet_type": "placeholder", "area_id": "X", "source_index": 1},
                {"index": 2, "role": "note", "header": "備考欄"},
            ]
        )
    )
    assert "template_source_index_missing" in validation_after_placeholder["errors"]


def test_multiple_active_template_versions_are_blocker() -> None:
    facility_id = _id("FAC")
    columns = facility_template_version_service.normalize_template_columns(_registered_config(facility_id)["fax_template"]["columns"])
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Ambiguous Template Facility"))
        for index in range(2):
            session.add(
                FacilityTemplateVersion(
                    id=_id("FTV"),
                    facility_id=facility_id,
                    version=str(index + 1),
                    status="active",
                    template_id="fax_layout_regular_forbidden_v1",
                    source="test",
                    columns_json=columns,
                    cells_json=[],
                    template_digest=f"digest-{index}",
                    validation_json={"errors": [], "warnings": []},
                    created_at=datetime.utcnow(),
                    activated_at=datetime.utcnow(),
                )
            )

    with session_scope() as session:
        active, error = facility_template_version_service.resolve_single_active_template_version(session, facility_id)

    assert active is None
    assert error == "facility_template_ambiguous"


def test_backfill_stamps_existing_order_artifact_lineage(monkeypatch) -> None:
    facility_id = _id("FAC")
    order_id = _id("ORD")
    evidence_id = _id("OEV")
    draft_id = _id("ODS")
    snapshot_id = _id("OCS")
    job_id = f"OCR-{order_id}"
    monkeypatch.setattr(
        facility_template_version_service.config_service,
        "get_facility_config",
        lambda requested_id: _registered_config(requested_id) if requested_id == facility_id else None,
    )
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Backfill Facility"))
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

    with session_scope() as session:
        summary = facility_template_version_service.backfill_facility_template_version_lineage(session)
        assert summary["updated"]["orders"] >= 1
        assert summary["updated"]["ocr_jobs"] >= 1
        assert summary["updated"]["order_ocr_evidence_runs"] >= 1
        assert summary["updated"]["order_sheet_drafts"] >= 1
        assert summary["updated"]["order_confirmed_snapshots"] >= 1
        assert summary["updated"]["order_workflow_states"] >= 1
        assert summary["updated"]["order_current_states"] >= 1

    with session_scope() as session:
        version_id = session.get(Order, order_id).template_version_id
        assert version_id
        assert session.get(OcrJob, job_id).template_version_id == version_id
        assert session.get(OrderOcrEvidenceRun, evidence_id).template_version_id == version_id
        assert session.get(OrderSheetDraft, draft_id).template_version_id == version_id
        assert session.get(OrderConfirmedSnapshot, snapshot_id).template_version_id == version_id
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow.template_version_id == version_id
        assert workflow.secondary_actions_json["workflow_v2"]["template_version_id"] == version_id
        current_state = session.get(OrderCurrentState, order_id)
        assert current_state.template_version_id == version_id
        assert current_state.state_json["template_version_id"] == version_id


def test_current_state_cache_requires_matching_template_version(monkeypatch) -> None:
    order_id = _id("ORD")
    draft_id = _id("ODS")
    facility_id = _id("FAC")
    template_version_id = _id("FTV")
    stale_template_version_id = _id("FTV")
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Cache Facility"))
        session.add(
            FacilityTemplateVersion(
                id=template_version_id,
                facility_id=facility_id,
                version="1",
                status="active",
                template_id="fax_layout_regular_forbidden_v1",
                source="test",
                columns_json=[],
                cells_json=[],
                template_digest="digest-a",
                validation_json={},
                created_at=datetime.utcnow(),
                activated_at=datetime.utcnow(),
            )
        )
        session.add(
            FacilityTemplateVersion(
                id=stale_template_version_id,
                facility_id=facility_id,
                version="2",
                status="archived",
                template_id="fax_layout_regular_forbidden_v1",
                source="test",
                columns_json=[],
                cells_json=[],
                template_digest="digest-b",
                validation_json={},
                created_at=datetime.utcnow(),
                activated_at=datetime.utcnow(),
            )
        )
        session.add(
            Order(
                id=order_id,
                facility_code=None,
                week_code="2026-04@2026-04-26~2026-04-30",
                status="要確認",
                document_uri=f"file:///{order_id}.pdf",
                message_id=f"msg-{order_id}",
                template_version_id=template_version_id,
                received_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderSheetDraft(
                id=draft_id,
                order_id=order_id,
                template_version_id=template_version_id,
                draft_sheet_json={
                    "fields": ["date_mmdd", "daypart", "menu", "qty.regular"],
                    "header": ["日付", "区分", "献立", "常食"],
                    "rows": [["04/26", "朝", "大豆のトマト煮", ""]],
                    "source": "workflow_v2_selected_ocr_projection",
                },
                draft_state="saved",
                blockers_json=[],
                warnings_json=[],
                created_at=datetime.utcnow(),
                edited_at=datetime.utcnow(),
                edited_by="operator",
            )
        )

    assert (
        order_service._persisted_current_state_is_reusable(
            {
                "draft_id": draft_id,
                "template_version_id": stale_template_version_id,
                "order_payload": {"lines_updated_at": None},
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular"],
                "header": ["日付", "区分", "献立", "常食"],
                "base_evidence_run_id": None,
            },
            order_id=order_id,
            refresh_draft_from_semantic=False,
        )
        is False
    )

    monkeypatch.setattr(
        order_service,
        "_resolve_current_sheet_context_week_id",
        lambda **_kwargs: "2026-04@2026-04-26~2026-04-30",
    )
    monkeypatch.setattr(order_service, "_build_monthly_menu_diagnostics", lambda **_kwargs: {})
    context = order_service._build_current_sheet_context_uncached(
        order_id,
        refresh_draft_from_semantic=False,
        upgrade_generic_from_sheet=True,
    )
    assert context["template_version_id"] == template_version_id


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
    monkeypatch.setattr(
        facility_template_version_service.config_service,
        "get_facility_config",
        lambda requested_id: resolved_config if requested_id == facility_id else None,
    )
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Digest Facility"))
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

    with session_scope() as session:
        version = facility_template_version_service.ensure_active_template_version_from_resolved_config(
            session,
            facility_id=facility_id,
            created_by="digest-test",
        )
        assert version is not None
        assert version.id != stale_version_id
        assert version.template_digest == fresh_digest
        stale = session.get(FacilityTemplateVersion, stale_version_id)
        assert stale is not None
        assert stale.status == "archived"
