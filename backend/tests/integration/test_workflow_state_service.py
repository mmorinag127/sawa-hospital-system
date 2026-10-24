import json
import pathlib
import sys
from datetime import date, datetime, timedelta

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.order_workflow_state import OrderWorkflowState  # noqa: E402
from src.services import config_service, critical_decision_service, draft_sheet_service, order_current_state_service, order_service, template_resolution_service, workflow_state_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job as get_ocr_job, update_job  # noqa: E402
from src.models.order import Order, OrderLine  # noqa: E402
from src.models.order_current_state import OrderCurrentState  # noqa: E402
from src.models.menu import MonthlyMenuEntry  # noqa: E402
from tests.helpers.authored_sheet_case import PERIOD, authored_sheet_case, publish_current, save_draft  # noqa: E402,F401
from src.services.workflow_attempt_guard_service import CurrentSheetInputChanged  # noqa: E402
from tests.contract.test_saved_sheet_quantity_schema import unchanged_after_refusal  # noqa: E402
from tests.helpers.owned_order import authored_layout_registration, owned_registration  # noqa: E402,F401
from tests.contract.test_orders_ocr_sheet_history_api import history_case  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("isolated_formal_schema")


def _structured_cells(rows: list[list[str]]) -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    row_height = 1.0 / max(len(rows), 1)
    col_width = 1.0 / max(max((len(row) for row in rows), default=1), 1)
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            cells.append(
                {
                    "row_index": row_index,
                    "col_index": col_index,
                    "text": value,
                    "bbox": [
                        round(col_index * col_width, 4),
                        round(row_index * row_height, 4),
                        round((col_index + 1) * col_width, 4),
                        round((row_index + 1) * row_height, 4),
                    ],
                }
            )
    return cells


def _seed_order(register, *, message_id, facility_id, week, effective_date):
    """Registration-only component: explicit source/period and real ingest owner."""
    return register(message_id=message_id, facility_id=facility_id,
        received_at=datetime(2026, 3, 22, 9), week=week,
        effective_date=effective_date, lines=[])


def _legacy_unowned_evidence_payload(
    *,
    facility_choice_required: bool = False,
    degraded: bool = False,
    extra_payload: dict | None = None,
) -> dict:
    payload = {
        "input_reference": "gs://bucket/orders/order.pdf",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
            }
        ],
        "table_raw": "\n".join(
            [
                "|日付|区分|メニュー|常食|",
                "|---|---|---|---|",
                "|03/22|朝|Menu A|5|",
            ]
        ),
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
            "confidence": 0.96,
            "blocked": False,
            "blocked_reasons": [],
        },
        "quantity_subgrid_passes": [
            {
                "page_index": 1,
                "cells": [{"row_index": 0, "column_index": 3, "text": "5"}],
            }
        ],
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }
    if degraded:
        payload.pop("input_reference", None)
        payload.pop("pages", None)
    if facility_choice_required:
        payload["facility_candidates"] = [
            {"facility_id": "FAC00001", "facility_name": "Facility 1", "score": 0.81},
            {"facility_id": "FAC00002", "facility_name": "Facility 2", "score": 0.76},
        ]
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)
    return payload


def _owned_declared_cells(history_case, *, facility, period, rows, quantity_columns):
    """Authored component, not a live FAX: explicit menu identities and typed cells."""
    from src.services import hakodate_assignment_service

    start = date.fromisoformat(period.split("@")[1].split("~")[0])
    fields = ["date_mmdd", "daypart", "menu"] + [item[0] for item in quantity_columns]
    columns = [{"index": i, "source_index": i, "name": field, "header": header, "role": role}
        for i, (field, header, role) in enumerate([
            ("date_mmdd", "日付", "date"), ("daypart", "区分", "daypart"),
            ("menu", "メニュー", "menu_name")])]
    for index, (field, label, diet, area) in enumerate(quantity_columns, 3):
        columns.append({"index": index, "source_index": index, "name": field, "header": label,
                        "role": "quantity", "diet_type": diet, "area_id": area})
    declared, entries = {}, []
    for index, row in enumerate(rows):
        assert len(row) == len(fields)
        day = date.fromisoformat(start.strftime("%Y-") + row[0].replace("/", "-"))
        assert day >= start
        identity = (day.isoformat(), order_service._normalize_daypart_key(row[1]),
                    hakodate_assignment_service._normalize_slot_text(row[2]))
        assert identity not in declared
        declared[identity] = row
        entries.append({"id": f"workflow-authored-{index}", "monthly_menu_id": day.strftime("%Y-%m"),
            "menu_date": day, "daypart": row[1], "name": row[2], "slot_index": 0, "diet_type": "regular"})
    case = history_case(facility=facility, period=period, week=period,
        received=datetime.combine(start, datetime.min.time()).replace(hour=9),
        columns=columns, menu_entries=entries, lines=[])

    def recognize(region):
        values = set()
        for target in region["logical_targets"]:
            identity = (str(target["date"]), order_service._normalize_daypart_key(target["daypart"]),
                        hakodate_assignment_service._normalize_slot_text(target["menu_name"]))
            assert identity in declared
            from tests.helpers.authored_cell_values import declared_field
            values.add(declared[identity][fields.index(declared_field(target, fields))])
        assert len(values) == 1, "merged physical targets require identical authored values"
        return values.pop()

    source = case.produce(cell_values=recognize)["sheet"]
    assert source["fields"] == fields
    assert source["rows"] == rows
    return case, source


def _assert_unowned_input_cannot_replace_selected(case, original, expected_sheet):
    """Exercise the actual write fence with the original unowned input, not HTTP 410."""
    from copy import deepcopy
    from src.models.order_ocr_cache import OrderOcrCache

    before_input = deepcopy(original)
    evidence = order_service.get_ocr_evidence_run(case.evidence)
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        with pytest.raises(ValueError, match="^original_template_capture_missing_or_mismatched$"):
            order_service.persist_ocr_evidence_run(case.id, original)
    assert original == before_input
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == case.evidence
        for key in ("fields", "rows"):
            assert current[key] == expected_sheet[key]
        assert order_service.get_ocr_evidence_run(case.evidence) == evidence
        payload, error = order_service.get_ocr_output(case.id, persist_cache=False)
        assert error is None
        assert payload["hakodate_ocr_evidence_records"] == evidence["payload_json"]["hakodate_ocr_evidence_records"]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


def _semantic_initial_draft(
    order_id: str,
    *,
    rows: list[list[str]] | None = None,
    fields: list[str] | None = None,
    header: list[str] | None = None,
    warnings: list[str] | None = None,
    source: str = "weekly_menu+ocr_payload",
) -> dict:
    normalized_fields = list(fields or ["date_mmdd", "daypart", "menu", "qty.regular_2f"])
    normalized_header = list(header or ["日付", "区分", "メニュー", "常食2F"])
    normalized_rows = [list(row) for row in (rows or [["03/22", "朝", "Menu A", "5"]])]
    return {
        "order_id": order_id,
        "source": source,
        "fields": normalized_fields,
        "header": normalized_header,
        "rows": normalized_rows,
        "row_ids": [f"row-{idx + 1}" for idx in range(len(normalized_rows))],
        "warnings": list(warnings or []),
    }


def test_refresh_workflow_state_persists_current_state_snapshot(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5, include_remarks=False)
    order = {"id": case.id}
    saved = save_draft(case, _semantic_initial_draft(order["id"], source="manual_draft"))

    workflow = workflow_state_service.refresh_workflow_state(order["id"])
    snapshot = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(workflow, dict)
    assert isinstance(snapshot, dict)
    assert snapshot["order_id"] == order["id"]
    assert snapshot["source"] == "manual_draft"
    assert snapshot["draft_id"]
    assert snapshot["resolved_week_id"] == PERIOD
    assert snapshot["draft_id"] == saved["id"]
    assert snapshot["rows"] == saved["draft_sheet_json"]["rows"]
    assert snapshot.input_receipt["input"]["workflow_revision"] == case.revision()


def test_refresh_workflow_state_does_not_overwrite_workflow_v2_owned_row(monkeypatch, tmp_path) -> None:
    from datetime import date
    from src.services.canonical_facility_store import read_snapshot
    from src.services.ingest_facility_selection_service import capture_selection
    from src.services.manual_upload_service import ManualUploadSavedFile
    from src.services.uploaded_pdf_service import create_uploaded_pdf_from_upload
    from src.workers import ingest_worker
    from tests.helpers.ingest_original import (
        authored_sources, configure_delivery, reserve_uploaded_original, register_authored_original,
    )

    order_service.clear_all()
    configure_delivery(monkeypatch)

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("Registration-only workflow fixture must not execute OCR")

    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", forbidden_provider)
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00001",
        menu_date=date(2026, 3, 22), daypart="breakfast")
    saved = ManualUploadSavedFile(message_id="msg-workflow-v2-owned-row-001",
        pdf_uri=uri, content_sha256=digest, original_filename="original.pdf",
        received_at=datetime(2026, 3, 22, 9, 0, 0))
    selection = capture_selection("FAC00001", "2026-03-22", read_snapshot().revision,
        "authored-original", "2026-03@2026-03-22~2026-03-28")
    uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00001",
        week_hint="2026-03@2026-03-22~2026-03-28", facility_name=None,
        skip_ocr=False, source_kind="manual_upload", facility_selection=selection)
    reserve_uploaded_original(uploaded["id"], effective_date=date(2026, 3, 22))
    order = register_authored_original(uploaded["id"], effective_date=date(2026, 3, 22))
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order["id"])
        if row is None:
            row = OrderWorkflowState(order_id=order["id"], last_transition_at=datetime.utcnow())
            session.add(row)
        row.state = "sheet_saved"
        row.headline = "シートが保存されました"
        row.primary_action = "run_bagging"
        row.secondary_actions_json = {
            "workflow_v2": {
                "facility_id": "FAC00001",
                "week_start": "2026-03-22",
                "week_end": "2026-03-28",
            }
        }
        row.blockers_json = []
        row.warnings_json = []
        row.last_transition_at = datetime.utcnow()

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert workflow["state"] == "sheet_saved"
    assert workflow["primary_action"] == "run_bagging"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order["id"])
        assert row is not None
        assert row.state == "sheet_saved"
        assert row.primary_action == "run_bagging"
        assert isinstance(row.secondary_actions_json, dict)
        assert row.secondary_actions_json["workflow_v2"]["facility_id"] == "FAC00001"
        assert row.secondary_actions_json["legacy_workflow_state"]["state"] == "uploaded"


def test_persist_current_state_upserts_existing_order_row(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5, include_remarks=False)
    order = {"id": case.id}
    first_draft = save_draft(case, _semantic_initial_draft(case.id, source="seed-a"))
    first = publish_current(case)
    second_draft = save_draft(case, _semantic_initial_draft(case.id, source="seed-b"))
    second = publish_current(case)
    persisted = order_current_state_service.get_current_state(order["id"])

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert isinstance(persisted, dict)
    assert persisted["draft_id"] == second_draft["id"]
    assert persisted["evidence_run_id"] == case.evidence
    assert persisted["state_json"]["source"] == "seed-b"
    assert first["draft_id"] == first_draft["id"] != second_draft["id"]
    assert persisted["state_json"]["rows"] == second_draft["draft_sheet_json"]["rows"]
    with session_scope() as session:
        assert session.query(OrderCurrentState).filter_by(order_id=case.id).count() == 1


def test_get_current_sheet_context_prefers_persisted_current_state_snapshot(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5, include_remarks=False)
    order = {"id": case.id}
    save_draft(case, _semantic_initial_draft(order["id"], source="manual_draft"))
    persisted = publish_current(case)

    assert isinstance(persisted, dict)

    def _unexpected_rebuild(*_args, **_kwargs):
        raise AssertionError("uncached current-sheet builder should not run when snapshot exists")

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _unexpected_rebuild)

    with case.readonly():
        current = order_service.get_current_sheet_context(order["id"])

    assert current == persisted


def test_get_current_sheet_context_builds_transient_when_current_state_snapshot_missing(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5)
    order = {"id": case.id}
    assert order_current_state_service.get_current_state_payload(case.id) is None
    calls = {"count": 0}
    original_builder = order_service._build_current_sheet_context_uncached

    def _wrapped_builder(*args, **kwargs):
        calls["count"] += 1
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _wrapped_builder)

    with case.readonly():
        current = order_service.get_current_sheet_context(order["id"])
        snapshot = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1
    assert snapshot is None


def test_get_current_sheet_context_rebuilds_when_persisted_current_state_lines_timestamp_stale(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5, include_remarks=False)
    order = {"id": case.id}
    saved_draft = save_draft(case, _semantic_initial_draft(order["id"], source="manual_draft"))
    stale_snapshot = publish_current(case)

    assert isinstance(saved_draft, dict)
    assert isinstance(stale_snapshot, dict)

    # Stored legacy lines are diagnostics, not a revived public line writer.
    with session_scope() as session:
        session.query(OrderLine).filter_by(order_id=case.id).delete()
        session.add(OrderLine(id="line-stale-1", line_id="line-stale-1", order_id=case.id,
            date=datetime(2026, 3, 22).date(), daypart="朝", menu_name="Menu A",
            diet_type=None, area_id=None, bag_type=None, quantity_original=7,
            quantity_corrected=None, change_note=None))
        session.get(Order, case.id).lines_updated_at = datetime.utcnow() + timedelta(seconds=1)
    with session_scope() as session:
        assert session.get(OrderLine, "line-stale-1").quantity_original == 7

    physical_snapshot = order_current_state_service.get_current_state(case.id)
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        with pytest.raises(CurrentSheetInputChanged, match="^current_sheet_input_changed$"):
            order_current_state_service.persist_current_state(
                order_id=case.id, state_json=stale_snapshot, draft_id=stale_snapshot["draft_id"],
                evidence_run_id=stale_snapshot["base_evidence_run_id"],
                template_version_id=stale_snapshot["template_version_id"],
            )
    assert order_current_state_service.get_current_state(case.id) == physical_snapshot
    assert order_current_state_service.get_current_state_payload(case.id) is None

    calls = {"count": 0}
    original_builder = order_service._build_current_sheet_context_uncached

    def _wrapped_builder(*args, **kwargs):
        calls["count"] += 1
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _wrapped_builder)

    with case.readonly():
        current = order_service.get_current_sheet_context(order["id"])
        snapshot_after = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1
    assert current["order_payload"]["lines_updated_at"] != stale_snapshot["order_payload"]["lines_updated_at"]
    assert snapshot_after is None
    assert order_current_state_service.get_current_state(case.id) == physical_snapshot
    assert current["draft_id"] == saved_draft["id"]
    assert current["rows"] == saved_draft["draft_sheet_json"]["rows"]
    for key in ("fields", "header", "row_ids"):
        assert current[key] == saved_draft["draft_sheet_json"][key]


def test_get_current_sheet_context_rebuilds_when_persisted_current_state_evidence_changes(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5, include_remarks=False)
    order = {"id": case.id}
    saved_draft = save_draft(case, _semantic_initial_draft(case.id, source="manual_draft"))
    persisted = publish_current(case)

    assert isinstance(saved_draft, dict)
    assert isinstance(persisted, dict)

    # Corrupt only the stored snapshot; the selected source/receipt stays real.
    with session_scope() as session:
        stored = session.get(OrderCurrentState, case.id)
        assert stored.evidence_run_id == case.evidence
        stored.state_json = {**stored.state_json, "base_evidence_run_id": "OEV-old"}
    physical_snapshot = order_current_state_service.get_current_state(case.id)
    calls = {"count": 0}
    original_builder = order_service._build_current_sheet_context_uncached

    def _wrapped_builder(*args, **kwargs):
        calls["count"] += 1
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _wrapped_builder)

    with case.readonly():
        current = order_service.get_current_sheet_context(order["id"])
        snapshot_after = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1
    assert snapshot_after is None
    assert order_current_state_service.get_current_state(case.id) == physical_snapshot
    assert physical_snapshot["evidence_run_id"] == case.evidence
    assert physical_snapshot["state_json"]["base_evidence_run_id"] == "OEV-old"
    assert current["base_evidence_run_id"] == case.evidence
    assert current["draft_id"] == saved_draft["id"]
    assert current["rows"] == saved_draft["draft_sheet_json"]["rows"]


def test_get_current_sheet_context_does_not_delete_unusable_current_state_on_read(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5)
    publish_current(case)
    with session_scope() as session:
        stored = session.get(OrderCurrentState, case.id)
        stored.state_json = {**stored.state_json, "source": "stale-transient"}
        session.get(Order, case.id).template_version_id = None
    physical_snapshot = order_current_state_service.get_current_state(case.id)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        snapshot_after = order_current_state_service.get_current_state(case.id)
        assert order_current_state_service.get_current_state_payload(case.id) is None
    assert snapshot_after == physical_snapshot

    assert current["blockers"] == ["original_template_projection_missing"]
    assert current["draft_payload"] is None
    assert isinstance(snapshot_after, dict)
    assert snapshot_after["state_json"]["source"] == "stale-transient"


def test_project_workflow_state_does_not_sync_critical_decisions_on_read(monkeypatch, authored_layout_registration) -> None:
    order_service.clear_all()
    order = authored_layout_registration(message_id="msg-workflow-read-no-decision-sync-001",
        facility_id="FAC00001", received_at=datetime(2026, 3, 22, 9),
        week=PERIOD, effective_date="2026-03-22", lines=[])
    current = order_service.get_current_sheet_context(order["id"])

    monkeypatch.setattr(
        workflow_state_service.candidate_resolution_service,
        "resolve_order_candidates",
        lambda **_kwargs: {
            "critical_choices": [
                {
                    "decision_type": "facility",
                    "candidates": [
                        {"value": "FAC00001", "label": "Facility A"},
                    ],
                }
            ]
        },
    )

    projected = workflow_state_service.project_workflow_state(
        order["id"],
        current_sheet_context=current,
        include_candidate_preview=False,
    )
    persisted_decisions = critical_decision_service.list_decisions(order["id"])

    assert isinstance(projected, dict)
    assert projected["critical_decisions"][0]["id"] == "pending:facility"
    assert persisted_decisions == []


def test_get_current_sheet_context_does_not_reuse_transient_current_state_snapshot(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=5)
    order = {"id": case.id}
    published = publish_current(case)
    assert published["draft_id"] is None
    # A stored legacy empty shell is not an accepted projection/receipt.
    with session_scope() as session:
        stored = session.get(OrderCurrentState, case.id)
        stored.state_json = {**stored.state_json, "source": "weekly_menu",
                             "rows": [["03/22", "朝", "Menu A", ""]]}
    physical_snapshot = order_current_state_service.get_current_state(case.id)
    calls = {"count": 0}
    original_builder = order_service._build_current_sheet_context_uncached

    def _rebuilt_context(*_args, **_kwargs):
        calls["count"] += 1
        return original_builder(*_args, **_kwargs)

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _rebuilt_context)

    with case.readonly():
        current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1
    assert current["source"] == case.selected_sheet["source"] == "workflow_v2_selected_ocr_projection"
    assert current["rows"][0][3] == "5"
    assert order_current_state_service.get_current_state(case.id) == physical_snapshot


def test_unselected_facility_candidates_cannot_replace_explicit_owned_selection(monkeypatch, authored_sheet_case):
    from src.services import ingest_job_service
    from tests.helpers.ingest_original import configure_delivery

    configure_delivery(monkeypatch)
    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, case.selected_sheet)
    candidates = [
        {"facility_id": "FAC00001", "facility_name": "Facility 1", "score": 0.81},
        {"facility_id": "FAC00002", "facility_name": "Facility 2", "score": 0.76},
    ]
    payload = {
        "message_id": "msg-workflow-state-001",
        "pdf_uri": str(case.pdf),
        "received_at": datetime(2026, 3, 22, 9),
        "facility_hint": None,
        "week_hint": PERIOD,
        "facility_selection": None,
        "facility_candidates": candidates,
    }
    with case.readonly():
        with pytest.raises(ValueError, match="^ingest_facility_selection_missing$"):
            ingest_job_service.create_ingest_receipt(payload)
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["draft_payload"] == case.selected_sheet
        assert current["base_evidence_run_id"] == case.evidence
        with session_scope() as session:
            assert session.get(Order, case.id).facility_code == case.facility
    assert payload["facility_hint"] is None
    assert payload["facility_selection"] is None
    assert payload["facility_candidates"] == candidates
    assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]


def test_project_workflow_state_returns_uploaded_without_evidence(authored_layout_registration, isolated_formal_schema):
    from tests.contract.test_orders_ocr_sheet_history_api import _snapshot

    order_service.clear_all()
    order = _seed_order(authored_layout_registration, message_id="msg-workflow-state-002", facility_id="FAC00001", week="2026-03@2026-03-22~2026-03-28", effective_date="2026-03-22")

    before = _snapshot(isolated_formal_schema)
    workflow = workflow_state_service.project_workflow_state(order["id"])
    assert _snapshot(isolated_formal_schema) == before

    assert isinstance(workflow, dict)
    assert workflow["state"] == "uploaded"
    assert workflow["primary_action"] == "run_ocr_pipeline"
    blockers = workflow["blockers_json"] or []
    assert "evidence_view_unavailable" in blockers
    assert "evidence_edit_unavailable" in blockers


def test_project_workflow_state_ignores_order_bound_first_pass_job_as_reparse_state(tmp_path, authored_layout_registration, isolated_formal_schema):
    from tests.contract.test_orders_ocr_sheet_history_api import _snapshot

    order_service.clear_all()
    order = _seed_order(authored_layout_registration, message_id="msg-workflow-state-first-pass-order-job", facility_id="FAC00001", week="2026-03@2026-03-22~2026-03-28", effective_date="2026-03-22")
    job_id = f"OCR-{order['id']}"
    output_path = tmp_path / "first_pass_order_job_output.json"
    output_path.write_text('{"status":"done","table_raw":"|03/22|朝|Menu A|"}', encoding="utf-8")
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        metrics={"request_mode": "ingest_first_pass", "processing_stage": "ocr_pipeline"},
    )

    before_job = get_ocr_job(job_id)
    original_output = output_path.read_bytes()
    before = _snapshot(isolated_formal_schema)
    workflow = workflow_state_service.project_workflow_state(order["id"])
    assert _snapshot(isolated_formal_schema) == before

    assert isinstance(workflow, dict)
    assert workflow["state"] == "uploaded"
    assert (workflow.get("reparse_state") or {}).get("status") == "idle"
    assert get_ocr_job(job_id) == before_job
    assert output_path.read_bytes() == original_output


def test_list_workflow_states_serializes_within_session(authored_sheet_case):
    case = authored_sheet_case(quantity=5)
    order = {"id": case.id}

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    with case.readonly():
        listed = workflow_state_service.list_workflow_states([order["id"]])

    assert listed[order["id"]]["order_id"] == order["id"]
    assert listed[order["id"]]["state"] == workflow["state"]


def test_missing_actual_native_artifact_requires_recovery_without_unowned_cache_substitution(authored_sheet_case):
    from src.models.order_ocr_cache import OrderOcrCache
    from src.services import canonical_ocr_artifact_service

    case = authored_sheet_case(quantity=5, include_remarks=False)
    selected = order_service.get_ocr_evidence_run(case.evidence)
    original = _legacy_unowned_evidence_payload(degraded=True)
    assert "input_reference" not in original and "pages" not in original
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        output, error = order_service.get_ocr_output(case.id)
        assert error is None and isinstance(output, dict)
        assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == [["03/22", "朝", "Menu A", "5"]]
    overlay = pathlib.Path(selected["payload_json"]["hakodate_overlay"]["uri"])
    assert overlay.resolve().is_relative_to(case.root.resolve())
    assert overlay.is_file()
    overlay.unlink()
    with case.readonly():
        with pytest.raises(ValueError, match="^canonical_artifact_bytes_unavailable$"):
            canonical_ocr_artifact_service.validate_payload(selected["payload_json"])
        output, error = order_service.get_ocr_output(case.id)
        assert output is None
        assert error == "ocr_evidence_recovery_required"
        stored = order_service.get_ocr_evidence_run(case.evidence)
        assert stored["payload_json"] == selected["payload_json"]
        assert stored["artifact_digest"] == selected["artifact_digest"]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


def test_unowned_partial_template_metadata_cannot_degrade_owned_saved_sheet(authored_sheet_case):
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    original = _legacy_unowned_evidence_payload(extra_payload={
        "template_id": "unknown-template",
        "template_resolution": {"resolved_template_id": "unknown-template",
            "candidate_template_ids": ["unknown-template"], "confidence": 0.91,
            "blocked": False, "blocked_reasons": []},
        "quantity_subgrid_passes": [], "table_box": None,
        "grid_column_edges": [], "grid_row_edges": [],
    })
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert isinstance(state, dict)
        assert state["state"] == "apply_ready"
        assert state["primary_action"] == "apply_draft"
        assert "semantic_shell_only" not in state["apply_gate"]["blockers"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == case.evidence
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


def test_owned_template_grid_does_not_depend_on_legacy_classification_guess(authored_sheet_case):
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=5, area="X", facility="FAC00006", include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id,
        fields=["date_mmdd", "daypart", "menu", "qty.regular_x"],
        header=["日付", "区分", "メニュー", "常食"]))
    original = _legacy_unowned_evidence_payload(extra_payload={
            "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "template_resolution": template_resolution_service.build_template_resolution(
                requested_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
                requested_template_ids=[
                    "fax_layout_regular_soft_mixer_forbidden_v1",
                    "fax_layout_floor_2f3f_v1",
                ],
                resolved_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
                classification={
                    "matched_template_id": "fax_layout_floor_2f3f_v1",
                    "confidence": 0.94,
                    "candidates": [
                        {"id": "fax_layout_floor_2f3f_v1", "score": 0.94},
                        {"id": "fax_layout_regular_soft_mixer_forbidden_v1", "score": 0.91},
                    ],
                },
                page_correction_summary={"pages": [{"mode": "template_warp", "template_id": "fax_layout_regular_soft_mixer_forbidden_v1"}]},
            ),
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        })
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert isinstance(state, dict)
        assert state["state"] == "apply_ready"
        assert state["primary_action"] == "apply_draft"
        assert "semantic_shell_only" not in state["apply_gate"]["blockers"]
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == case.evidence
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert "qty.regular_x" in current["fields"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


def test_explicit_owned_columns_work_without_legacy_grid_or_position_fallback(history_case):
    from src.models.order_ocr_cache import OrderOcrCache

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f",
        "qty.soft_2f", "qty.soft_3f", "qty.mixer_2f", "qty.mixer_3f", "remarks"]
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    roles = ["date", "daypart", "menu_name", *["quantity"] * 6, "note"]
    columns = [{"index": i, "source_index": i, "name": field, "header": rows[0][i],
        "role": roles[i]} for i, field in enumerate(fields)]
    for column, (diet, area) in zip(columns[3:9], [
        ("regular", "2F"), ("regular", "3F"), ("soft", "2F"), ("soft", "3F"),
        ("mixer", "2F"), ("mixer", "3F"),
    ], strict=True):
        column.update(diet_type=diet, area_id=area)
    period = "2026-03@2026-03-22~2026-03-28"
    case = history_case(week=period, period=period, received=datetime(2026, 3, 22, 9),
        lines=[], columns=columns, menu_entries=[{"id": "owned-six-diet-menu",
            "monthly_menu_id": "2026-03", "menu_date": date(2026, 3, 22), "daypart": "朝",
            "name": "献立A", "slot_index": 0, "diet_type": "regular"}])
    values = dict(zip(fields[3:], rows[1][3:], strict=True))

    def recognize(region):
        from tests.helpers.authored_cell_values import declared_field
        declared = {values[declared_field(target, fields)] for target in region["logical_targets"]}
        assert len(declared) == 1
        return declared.pop()

    source = case.produce(cell_values=recognize)
    assert source["sheet"]["fields"] == fields
    assert source["sheet"]["rows"] == [rows[1]]
    case.save(source["sheet"])
    stale = {"template_resolution": None, "quantity_subgrid_passes": [],
        "table_box": None, "grid_column_edges": [], "grid_row_edges": [],
        "tables": [{"page_index": 1, "table_id": "page1_table1", "row_count": len(rows),
            "col_count": len(rows[0]), "rows": rows, "cells": _structured_cells(rows)}]}
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        assert cached is not None
        cached.payload = stale
    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(case.id)
        assert isinstance(workflow, dict)
        assert workflow["state"] == "apply_ready"
        assert workflow["primary_action"] == "apply_draft"
        assert "semantic_shell_only" not in (workflow["apply_gate"]["blockers"] or [])
        mapping = workflow["candidate_resolution"]["resolutions"]["column_mapping"]
        assert mapping["decision_source"] != "position_fallback"
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == case.evidence
        assert current["rows"] == [rows[1]]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == stale


def test_owned_low_score_and_nonnumeric_cells_remain_editable_without_position_guessing(history_case, monkeypatch):
    from tests.helpers.native_selection import select_authored_native_result

    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    original = _legacy_unowned_evidence_payload(extra_payload={
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
            "failed_cells": [{"row_index": 1, "col_index": 3, "reason": "merged_numeric_cell"}],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        })
    typed_rows = [["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1"]]
    quantities = [
        ("qty.regular_2f", "常食2F", "regular", "2F"), ("qty.regular_3f", "常食3F", "regular", "3F"),
        ("qty.soft_2f", "軟菜2F", "soft", "2F"), ("qty.soft_3f", "軟菜3F", "soft", "3F"),
        ("qty.mixer_2f", "ミキサー2F", "mixer", "2F"), ("qty.mixer_3f", "ミキサー3F", "mixer", "3F")]
    case, source = _owned_declared_cells(history_case, facility="FAC00001", period=PERIOD,
        rows=typed_rows, quantity_columns=quantities)
    case.save(source)
    _assert_unowned_input_cannot_replace_selected(case, original, source)
    columns = json.loads(case.source.read_text())["facilities"][0]["fax_template"]["columns"]
    for literal in ("?", "", "5"):
        declared = [["03/22", "朝", "献立A", literal, "4", "3", "2", "1", "1"]]
        evidence = select_authored_native_result(monkeypatch, order_id=case.id,
            table_rows=declared, columns=columns, unlisted_cells="",
            artifact_root=case.root / "low-score-native", recognizer_score=0.09)
        case.evidence = evidence["id"]
        projected = case.get("workflow-v2/sheet-source")
        assert projected["selected_ocr_result_id"] == evidence["id"]
        assert projected["sheet"]["rows"] == declared
        records = evidence["payload_json"]["hakodate_ocr_evidence_records"]
        assert any(record["engine_metadata"]["recognizer_score"] == 0.09 for record in records)
        assert literal in [record["raw_text"] for record in records]
        case.save(projected["sheet"])
        with case.readonly():
            assert order_service.get_current_sheet_context(case.id)["rows"] == declared
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] != "position_fallback"
        assert "numeric_trust_low" not in state["apply_gate"]["blockers"]


def test_owned_partial_schema_is_blocked_without_guessing_missing_quantity_columns(history_case):
    from copy import deepcopy
    from src.services import order_workflow_v2_service as workflow

    rows = [
        ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
        ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
        ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
        ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
    ]
    original = _legacy_unowned_evidence_payload(extra_payload={
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        })
    # Independent authored current-format input: these identities are declarations,
    # not a reinterpretation of the noisy legacy daypart/header strings above.
    expected = [["03/22", "朝", "Menu A", "57", "2"], ["03/22", "昼", "Menu B", "58", "4"]]
    case, source = _owned_declared_cells(history_case, facility="FAC00005", period=PERIOD,
        rows=expected, quantity_columns=[("qty.soft_x", "軟菜", "soft", "X"),
                                       ("qty.change_1_x", "変更1", "change_1", "X")])
    case.save(source)
    _assert_unowned_input_cannot_replace_selected(case, original, source)
    incomplete = deepcopy(source)
    for key in ("fields", "header"):
        incomplete[key] = incomplete[key][:-1]
    incomplete["rows"] = [row[:-1] for row in source["rows"]]
    case.save(incomplete)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["apply_gate"]["can_apply"] is False
        assert "quantity_template_field_missing" in state["apply_gate"]["blockers"]
        assert order_service.get_current_sheet_context(case.id)["rows"] == [row[:-1] for row in expected]
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        result, error = workflow.run_bagging(case.id, expected_revision=revision)
        assert result is None and error == "quantity_template_field_missing"


def test_malformed_unowned_rows_cannot_replace_owned_editable_cells_or_saved_five(authored_sheet_case):
    from src.services.storage_service import load_selected_pdf_bytes
    rows = [
        ["", "日付", "区 分", "", "献立", "##", "44日", "禁食【軟菜】", "", "備考欄"],
        ["", "", "", "", "", "", "", "肉禁", "魚禁", ""],
        ["", "4/5\n(日)", "ま", "...", "\"", "0", "0", "", "", ""],
        ["", "", "", "***", "&", "0", "0", "", "", ""],
        ["", "", "口", "VT", "9", "58", "2", "", "", ""],
        ["", "", "", "OK", "<", "58", "2", "", "", ""],
    ]
    original = _legacy_unowned_evidence_payload(extra_payload={
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        })
    case = authored_sheet_case(quantity=5, include_remarks=False,
        menu_date=date(2026, 4, 5), period="2026-04@2026-04-05~2026-04-11", facility="FAC00005", area="X")
    source = case.selected_sheet
    saved = save_draft(case, source)
    _assert_unowned_input_cannot_replace_selected(case, original, source)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        assert current["rows"] == [["04/05", "朝", "Menu A", "5"]]
        assert current["draft_id"] == saved["id"]
        assert current["source"] != "ocr_table"
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
    selected = order_service.get_ocr_evidence_run(case.evidence)
    receipt = selected["payload_json"]["hakodate_repair_source_receipt"]["selected_pdf_receipt"]
    assert receipt["storage_uri"] == str(case.pdf)
    unavailable = case.root / "deliberately-unavailable-original.pdf"
    assert not unavailable.exists()
    revision = case.revision()
    case.pdf.rename(unavailable)
    try:
        with unchanged_after_refusal(case, revision):
            with pytest.raises(ValueError, match="^selected_pdf_verification_failed$"):
                load_selected_pdf_bytes(receipt["storage_uri"], receipt)
    finally:
        unavailable.rename(case.pdf)
    with case.readonly():
        assert order_service.get_ocr_evidence_run(case.evidence) == selected
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["04/05", "朝", "Menu A", "5"]]


def test_foreign_facility_candidates_and_changed_source_receipt_cannot_replace_owned_cells(history_case):
    from copy import deepcopy
    from src.services import canonical_ocr_artifact_service, candidate_resolution_service

    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    original = _legacy_unowned_evidence_payload(extra_payload={
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
            "facility_candidates": [
                {"facility_id": "FAC99999", "facility_name": "別施設", "score": 0.96},
                {"facility_id": "FAC88888", "facility_name": "次点施設", "score": 0.52},
            ],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        })
    case, source = _owned_declared_cells(history_case, facility="FAC00001", period=PERIOD,
        rows=[["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1"]],
        quantity_columns=[
            ("qty.regular_2f", "常食2F", "regular", "2F"), ("qty.regular_3f", "常食3F", "regular", "3F"),
            ("qty.soft_2f", "軟菜2F", "soft", "2F"), ("qty.soft_3f", "軟菜3F", "soft", "3F"),
            ("qty.mixer_2f", "ミキサー2F", "mixer", "2F"), ("qty.mixer_3f", "ミキサー3F", "mixer", "3F")])
    case.save(source)
    _assert_unowned_input_cannot_replace_selected(case, original, source)
    selected = order_service.get_ocr_evidence_run(case.evidence)
    for foreign in ("FAC99999", "FAC88888"):
        damaged = deepcopy(selected["payload_json"])
        # Negative validator argument only, never a persisted/selected receipt.
        from hashlib import sha256
        damaged["hakodate_repair_source_receipt"]["configured_input_digest"] = sha256(foreign.encode()).hexdigest()
        with case.readonly():
            with pytest.raises(ValueError, match="^canonical_artifact_source_changed$"):
                canonical_ocr_artifact_service.validate_payload(damaged)
            resolution = candidate_resolution_service.build_facility_resolution(current_facility=case.facility, payload=original)
            assert resolution["resolved_value"] == "FAC00001"
            assert {item["value"] for item in resolution["candidates"]} >= {"FAC99999", "FAC88888", "FAC00001"}
            assert order_service.get_current_sheet_context(case.id)["rows"] == source["rows"]
            assert order_service.get_ocr_evidence_run(case.evidence) == selected


def test_stale_ambiguous_position_candidates_cannot_override_owned_columns_or_selected_five(authored_sheet_case):
    from src.services import candidate_resolution_service
    rows_a = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    rows_b = [
        ["日付", "区分", "メニュー", "補助", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "", "5", "4", "3", "2", "1", "1", ""],
    ]
    original = _legacy_unowned_evidence_payload(extra_payload={
            "template_resolution": {
                "resolved_template_id": "fax_layout_floor_2f3f_v1",
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
                "decision_source": "position_fallback",
            },
            "column_mapping_resolution": {
                "resolved_value": "3:qty.regular_2f|4:qty.regular_3f|5:qty.soft_2f|6:qty.soft_3f|7:qty.mixer_2f|8:qty.mixer_3f",
                "resolved_column_mapping_id": "3:qty.regular_2f|4:qty.regular_3f|5:qty.soft_2f|6:qty.soft_3f|7:qty.mixer_2f|8:qty.mixer_3f",
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
                "decision_source": "position_fallback",
            },
            "column_mapping_candidates": [
                {
                    "value": "3:qty.regular_2f|4:qty.regular_3f|5:qty.soft_2f|6:qty.soft_3f|7:qty.mixer_2f|8:qty.mixer_3f",
                    "label": "常食2F / 常食3F",
                    "score": 0.99,
                    "decision_source": "position_fallback",
                },
                {
                    "value": "4:qty.regular_2f|5:qty.regular_3f|6:qty.soft_2f|7:qty.soft_3f|8:qty.mixer_2f|9:qty.mixer_3f",
                    "label": "常食2F / 常食3F",
                    "score": 0.99,
                    "decision_source": "position_fallback",
                },
            ],
            "table_box": [0.0, 0.0, 1.0, 1.0],
            "grid_column_edges": [0.0, 0.1, 0.2, 0.3, 0.4],
            "grid_row_edges": [0.0, 0.5, 1.0],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows_a),
                    "col_count": len(rows_a[0]),
                    "rows": rows_a,
                    "cells": _structured_cells(rows_a),
                },
                {
                    "page_index": 1,
                    "table_id": "page1_table2",
                    "row_count": len(rows_b),
                    "col_count": len(rows_b[0]),
                    "rows": rows_b,
                    "cells": _structured_cells(rows_b),
                },
            ],
        })
    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, case.selected_sheet)
    _assert_unowned_input_cannot_replace_selected(case, original, case.selected_sheet)
    with case.readonly():
        resolution = candidate_resolution_service.build_column_mapping_resolution(original)
        assert resolution["resolved_value"] is None
        assert resolution["requires_user_choice"] is True
        assert "position_fallback_removed" in resolution["blocked_reasons"]
        assert "column_mapping_choice_required" in resolution["blocked_reasons"]
        assert len(resolution["candidates"]) == 2
        assert order_service.get_current_sheet_context(case.id)["draft_id"] == saved["id"]
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] != "position_fallback"
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]


def _owned_fac2_twenty_three_twenty_seven(history_case, *, schema_fault=False):
    from copy import deepcopy
    from src.models.order_sheet_draft import OrderSheetDraft

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.no_meat_x",
        "qty.no_fish_x", "qty.change_1_x", "qty.change_2_x", "remarks"]
    header = ["日付", "区分", "メニュー", "常食", "肉禁", "魚禁", "変更1", "変更2", "備考"]
    roles = ["date", "daypart", "menu_name", *["quantity"] * 5, "note"]
    columns = [{"index": i, "source_index": i, "name": field, "header": header[i],
        "role": roles[i]} for i, field in enumerate(fields)]
    for column, diet in zip(columns[3:8], ["regular", "no_meat", "no_fish", "change_1", "change_2"], strict=True):
        column.update(diet_type=diet, area_id="X")
    period = "2026-03@2026-03-22~2026-03-28"
    menus = [("Menu A", "朝", "23"), ("Menu B", "昼", "27")]
    entries = [{"id": f"fac2-slot-{i}", "monthly_menu_id": "2026-03",
        "menu_date": date(2026, 3, 22), "daypart": daypart, "name": name,
        "slot_index": 0, "diet_type": "regular"} for i, (name, daypart, _) in enumerate(menus)]
    case = history_case(facility="FAC00002", week=period, period=period,
        received=datetime(2026, 3, 22, 9), columns=columns, lines=[], menu_entries=entries)
    values = {(name, field): quantity if field == "qty.regular_x" else ""
        for name, _, quantity in menus for field in fields[3:]}

    def recognize(region):
        from tests.helpers.authored_cell_values import declared_field
        declared = {values[(target["menu_name"], declared_field(target, fields))] for target in region["logical_targets"]}
        assert len(declared) == 1
        return declared.pop()

    source = case.produce(cell_values=recognize)
    assert source["sheet"]["rows"] == [
        ["03/22", "朝", "Menu A", "23", "", "", "", "", ""],
        ["03/22", "昼", "Menu B", "27", "", "", "", "", ""],
    ]
    sheet = {"order_id": case.id, "source": "weekly_menu", "fields": fields,
        "header": header, "rows": [
            ["03/22", "breakfast", "Menu A", "23", "", "", "", "", ""],
            ["03/22", "lunch", "Menu B", "27", "", "", "", "", ""],
        ], "row_ids": ["draft-row-1", "draft-row-2"]}
    if schema_fault:
        sheet = deepcopy(sheet)
        index = sheet["fields"].index("qty.change_2_x")
        sheet["fields"].pop(index)
        sheet["header"].pop(index)
        for row in sheet["rows"]:
            assert row[index] == ""
            row.pop(index)
    saved = save_draft(case, sheet)
    with session_scope() as session:
        draft = session.get(OrderSheetDraft, saved["id"])
        draft.blockers_json = ["template_unresolved"]
        draft.warnings_json = ["template_unresolved", "sheet_quantity_column_unmapped",
            "ocr_evidence_recovery_required", "sheet_ocr_review_required"]
    noisy = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22", '"', "VF", "Menu A", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu B", "27", "", "", "", "", "", ""],
    ]
    original = {"input_reference": "gs://bucket/orders/order.pdf",
        "tables": [{"page_index": 1, "table_id": "page1_table1",
            "row_count": len(noisy), "col_count": len(noisy[0]), "rows": noisy,
            "cells": _structured_cells(noisy)}],
        "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
            "layout_overlay_uri": "gs://bucket/orders/page1-layout.png"}]}
    _store_unowned_workflow_payload(case, original)
    return case, sheet, original


def test_owned_saved_twenty_three_twenty_seven_retain_explicit_layout_blockers(history_case):
    from src.models.order_ocr_cache import OrderOcrCache

    case, sheet, original = _owned_fac2_twenty_three_twenty_seven(history_case)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "draft_blocked"
        assert state["apply_gate"]["can_apply"] is False
        mapping = state["candidate_resolution"]["resolutions"]["column_mapping"]
        assert mapping["decision_source"] != "position_fallback"
        assert order_service.get_current_sheet_context(case.id)["rows"] == sheet["rows"]
        assert "template_unresolved" in state["apply_gate"]["blockers"]
        assert "sheet_quantity_column_unmapped" in state["apply_gate"]["blockers"]
        assert "draft_newer_than_lines" in state["apply_gate"]["blockers"]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


def test_current_schema_failure_and_saved_layout_blockers_preserve_authoritative_quantities(history_case):
    from src.models.order_ocr_cache import OrderOcrCache
    from src.services import order_workflow_v2_service as workflow
    from tests.contract.test_saved_sheet_quantity_schema import unchanged_after_refusal

    case, sheet, original = _owned_fac2_twenty_three_twenty_seven(history_case, schema_fault=True)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "draft_blocked"
        assert state["apply_gate"]["can_apply"] is False
        assert state["apply_gate"]["can_confirm"] is False
        assert "quantity_template_field_missing" in state["apply_gate"]["blockers"]
        assert "template_unresolved" in state["apply_gate"]["blockers"]
        assert "sheet_quantity_column_unmapped" in state["apply_gate"]["blockers"]
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == case.evidence
        assert current["rows"] == sheet["rows"]
        assert [row[3] for row in current["rows"]] == ["23", "27"]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        result, error = workflow.run_bagging(case.id, expected_revision=revision)
        assert result is None and error == "quantity_template_field_missing"


def _saved_five_with_owned_candidate(authored_sheet_case):
    from tests.helpers.authored_sheet_case import prepare_rerun

    case = authored_sheet_case(quantity=5, include_remarks=False)
    first = order_service.get_ocr_evidence_run(case.evidence)
    saved = save_draft(case, {
        "order_id": case.id, "source": "weekly_menu+ocr_payload",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [["03/22", "朝", "Menu A", "5"]], "row_ids": ["row-1"],
    })
    calls = prepare_rerun(case, cell_values="8")
    with case.execution():
        second, error = order_service.rerun_ocr_evidence_only(case.id, job_id=case.job,
            project_sheet=False, refresh_workflow=False)
    assert error is None and isinstance(second, dict)
    assert len(calls) == 1
    assert second["id"] != first["id"]
    assert second["terminal_receipt"]["status"] == "done"
    assert second["terminal_receipt"]["evidence_run_id"] == second["id"]
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["base_evidence_run_id"] == first["id"]
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
    return case, first, saved, second


def test_project_workflow_state_returns_new_evidence_available_when_latest_evidence_differs_from_draft(authored_sheet_case):
    case, first, saved, second = _saved_five_with_owned_candidate(authored_sheet_case)
    order = {"id": case.id}
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == first["id"]

    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "new_evidence_available"
    assert workflow["primary_action"] == "select_candidate_evidence"
    assert workflow["evidence_run_id"] == first["id"]
    assert workflow["active_evidence_run_id"] == first["id"]
    assert workflow["candidate_prompt_visible"] is True
    assert workflow["candidate_evidence_run_id"] == second["id"]
    assert workflow["current_sheet_revision_id"] == saved["id"]
    assert workflow["apply_gate"]["can_apply"] is True
    with case.readonly():
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        preview = order_service.candidate_sheet_state(case.id, candidate_evidence_run_id=second["id"])
        assert preview["candidate_preview_error"] is None
        assert preview["candidate_preview_draft"]["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
        assert order_service.get_current_sheet_context(case.id)["rows"] == saved["draft_sheet_json"]["rows"]


def test_project_workflow_state_new_evidence_available_retains_apply_readiness_for_clean_draft(authored_sheet_case):
    case, first, saved, second = _saved_five_with_owned_candidate(authored_sheet_case)
    order = {"id": case.id}
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == first["id"]

    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "new_evidence_available"
    assert workflow["candidate_prompt_visible"] is True
    assert workflow["candidate_evidence_run_id"] == second["id"]
    assert workflow["apply_gate"]["can_apply"] is True
    assert "draft_newer_than_lines" in (workflow["apply_gate"].get("blockers") or [])
    with case.readonly():
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        preview = order_service.candidate_sheet_state(case.id, candidate_evidence_run_id=second["id"])
        assert preview["candidate_preview_error"] is None
        assert preview["candidate_preview_draft"]["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
        assert order_service.get_current_sheet_context(case.id)["rows"] == saved["draft_sheet_json"]["rows"]


@pytest.mark.parametrize("fault,artifact,code", [
    ("missing-overlay", "overlay", "canonical_artifact_bytes_unavailable"),
    ("changed-records", "records", "canonical_artifact_bytes_changed"),
])
def test_unpreviewable_owned_candidate_cannot_replace_saved_five(authored_sheet_case, fault, artifact, code):
    from src.services import canonical_ocr_artifact_service
    from copy import deepcopy

    case, active, saved, candidate = _saved_five_with_owned_candidate(authored_sheet_case)
    before_job = get_ocr_job(case.job)
    with session_scope() as session:
        original_row = session.get(OrderOcrEvidenceRun, candidate["id"])
        original_payload = deepcopy(original_row.payload_json)
        original_manifest = deepcopy(original_row.artifact_manifest_json)
        original_digest = original_row.artifact_digest
    with case.readonly():
        preview = order_service.candidate_sheet_state(case.id, candidate_evidence_run_id=candidate["id"])
        assert preview["candidate_preview_available"] is True
        assert preview["candidate_preview_draft"]["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
    path = pathlib.Path(candidate["payload_json"]["hakodate_canonical_pipeline"]["outputs"][artifact])
    assert path.resolve().is_relative_to(case.root.resolve()) and path.is_file()
    if fault == "missing-overlay":
        path.unlink()
    else:
        path.write_bytes(b"deliberately changed owned records artifact")
    with case.readonly():
        with pytest.raises(ValueError, match="^" + code + "$"):
            canonical_ocr_artifact_service.validate_payload(candidate["payload_json"])
        preview = order_service.candidate_sheet_state(case.id, candidate_evidence_run_id=candidate["id"])
        assert preview["candidate_evidence_run_id"] == candidate["id"]
        assert preview["candidate_preview_available"] is False
        assert preview["candidate_has_meaningful_diff"] is False
        assert preview["candidate_preview_error"] == code
        assert preview["candidate_preview_draft"] is None
        assert preview["current_sheet_revision_id"] == saved["id"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == active["id"]
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        stored = order_service.get_ocr_evidence_run(candidate["id"])
        payload = stored["payload_json"]
        assert set(payload) == set(candidate["payload_json"])
        read_diagnostics = {"evidence_manifest", "evidence_digest"}
        assert {key: value for key, value in payload.items() if key not in read_diagnostics} == {
            key: value for key, value in candidate["payload_json"].items() if key not in read_diagnostics}
        assert payload["evidence_manifest"]["validation_errors"] == [code]
        assert payload["evidence_manifest"]["artifacts_complete"] is False
        assert payload["evidence_digest"] == payload["evidence_manifest"]["digest"]
        assert payload["evidence_digest"] != candidate["payload_json"]["evidence_digest"]
        assert stored["selectable"] is False
        assert stored["artifact_digest"] == candidate["artifact_digest"]
        with session_scope() as session:
            original_row = session.get(OrderOcrEvidenceRun, candidate["id"])
            assert original_row.payload_json == original_payload
            assert original_row.artifact_manifest_json == original_manifest
            assert original_row.artifact_digest == original_digest
        assert get_ocr_job(case.job) == before_job


def test_real_candidate_selection_rejects_stale_workflow_revision_and_keeps_saved_five(authored_sheet_case):
    case, active, first_save, candidate = _saved_five_with_owned_candidate(authored_sheet_case)
    stale_revision = case.revision()
    latest = save_draft(case, first_save["draft_sheet_json"])
    assert latest["id"] != first_save["id"]
    with case.readonly():
        preview = order_service.candidate_sheet_state(case.id, candidate_evidence_run_id=candidate["id"])
        assert preview["candidate_preview_available"] is True
        assert preview["current_sheet_revision_id"] == latest["id"]
        assert preview["current_sheet_revision_id"] != first_save["id"]
        response = case.client.post(f"/orders/{case.id}/workflow-v2/ocr-results/{candidate['id']}/select",
            headers={**case.actor.headers, "If-Match": f'"{stale_revision}"'})
        assert response.status_code == 409
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == latest
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == active["id"]
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]


def _produce_unselected_nine(case):
    from tests.helpers.authored_sheet_case import prepare_rerun

    calls = prepare_rerun(case, cell_values="9")
    with case.execution():
        evidence, error = order_service.rerun_ocr_evidence_only(
            case.id, job_id=case.job, project_sheet=False, refresh_workflow=False,
        )
    assert error is None and isinstance(evidence, dict)
    assert len(calls) == 1
    assert evidence["terminal_receipt"]["status"] == "done"
    assert evidence["terminal_receipt"]["evidence_run_id"] == evidence["id"]
    return evidence


def _assert_owned_refresh_states(case, result, derived_state):
    with case.readonly():
        canonical, error = order_workflow_v2_service.get_workflow_readonly(case.id)
        assert error is None
        assert result["state"] == canonical["state"] == "ocr_completed"
        assert result["legacy_workflow_state"]["state"] == derived_state


def test_refresh_workflow_state_hides_new_candidate_after_acknowledging_current_sheet(authored_sheet_case):
    case, active, saved, candidate = _saved_five_with_owned_candidate(authored_sheet_case)
    workflow = workflow_state_service.refresh_workflow_state(case.id)
    assert isinstance(workflow, dict)
    _assert_owned_refresh_states(case, workflow, "new_evidence_available")
    assert workflow["candidate_prompt_visible"] is True
    assert workflow["candidate_evidence_run_id"] == candidate["id"]

    decision = critical_decision_service.acknowledge_candidate_evidence(
        case.id, candidate["id"], selected_by="tester",
    )
    assert isinstance(decision, dict)
    assert decision["selected_value"] == candidate["id"]
    refreshed = workflow_state_service.refresh_workflow_state(case.id)

    assert isinstance(refreshed, dict)
    _assert_owned_refresh_states(case, refreshed, "apply_ready")
    assert refreshed["candidate_prompt_visible"] is False
    assert refreshed["candidate_evidence_run_id"] is None
    assert refreshed["acknowledged_candidate_evidence_run_id"] == candidate["id"]
    assert refreshed["active_evidence_run_id"] == active["id"]
    assert refreshed["apply_gate"]["can_apply"] is True
    with case.readonly():
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]


def test_refresh_workflow_state_reprompts_when_new_candidate_arrives_after_acknowledgement(authored_sheet_case):
    case, active, saved, candidate = _saved_five_with_owned_candidate(authored_sheet_case)
    decision = critical_decision_service.acknowledge_candidate_evidence(
        case.id, candidate["id"], selected_by="tester",
    )
    assert decision is not None
    newer = _produce_unselected_nine(case)
    assert newer["id"] not in {active["id"], candidate["id"]}
    refreshed = workflow_state_service.refresh_workflow_state(case.id)

    assert isinstance(refreshed, dict)
    _assert_owned_refresh_states(case, refreshed, "new_evidence_available")
    assert refreshed["candidate_prompt_visible"] is True
    assert refreshed["candidate_evidence_run_id"] == newer["id"]
    assert refreshed["acknowledged_candidate_evidence_run_id"] == candidate["id"]
    assert refreshed["active_evidence_run_id"] == active["id"]
    with case.readonly():
        preview = order_service.candidate_sheet_state(case.id, candidate_evidence_run_id=newer["id"])
        assert preview["candidate_preview_draft"]["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "9"]]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]


def test_refresh_workflow_state_suppresses_older_pending_candidate_ids_after_acknowledgement(authored_sheet_case):
    from copy import deepcopy
    from src.models.ocr_job import OcrJob

    case, active, saved, older = _saved_five_with_owned_candidate(authored_sheet_case)
    visible = _produce_unselected_nine(case)
    initial = workflow_state_service.refresh_workflow_state(case.id)
    _assert_owned_refresh_states(case, initial, "new_evidence_available")
    assert initial["candidate_prompt_visible"] is True
    assert initial["candidate_evidence_run_id"] == visible["id"]
    decision = critical_decision_service.acknowledge_candidate_evidence(
        case.id, visible["id"], selected_by="tester",
    )
    assert decision is not None
    with session_scope() as session:
        job = session.get(OcrJob, case.job)
        receipt = deepcopy(job.metrics["rerun_terminal_receipt"])
        # A stale diagnostic pointer is not a new terminal result or a new source.
        job.metrics = {**job.metrics, "evidence_run_id": older["id"], "new_evidence_available": True}
        assert job.metrics["rerun_terminal_receipt"] == receipt
    job_before = get_ocr_job(case.job)
    refreshed = workflow_state_service.refresh_workflow_state(case.id)

    assert isinstance(refreshed, dict)
    _assert_owned_refresh_states(case, refreshed, "apply_ready")
    assert refreshed["candidate_prompt_visible"] is False
    assert refreshed["candidate_evidence_run_id"] is None
    assert refreshed["acknowledged_candidate_evidence_run_id"] == visible["id"]
    assert refreshed["active_evidence_run_id"] == active["id"]
    with case.readonly():
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert get_ocr_job(case.job) == job_before




def test_refresh_workflow_state_skips_candidate_preview_build_when_requested(monkeypatch, authored_sheet_case):
    case, active, saved, candidate = _saved_five_with_owned_candidate(authored_sheet_case)
    current_revision = order_service.get_current_sheet_context(case.id)["current_sheet_revision_id"]

    def unexpected_preview(*_args, **_kwargs):
        raise AssertionError("candidate preview should be skipped")

    monkeypatch.setattr(order_service, "candidate_sheet_state", unexpected_preview)
    workflow = workflow_state_service.refresh_workflow_state(
        case.id, include_candidate_preview=False,
    )

    assert isinstance(workflow, dict)
    _assert_owned_refresh_states(case, workflow, "new_evidence_available")
    assert workflow["candidate_prompt_visible"] is True
    assert workflow["candidate_evidence_run_id"] == candidate["id"]
    assert workflow["current_sheet_revision_id"] == current_revision
    assert workflow["candidate_sheet_state"]["candidate_evidence_run_id"] == candidate["id"]
    assert workflow["candidate_sheet_state"]["candidate_preview_available"] is False
    assert workflow["candidate_sheet_state"]["candidate_has_meaningful_diff"] is False
    with case.readonly():
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == active["id"]
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]


def test_exact_internal_save_acknowledges_actual_selected_evidence(authored_sheet_case):
    case = authored_sheet_case(quantity=5, include_remarks=False)
    original_evidence = order_service.get_ocr_evidence_run(case.evidence)
    selected_draft = draft_sheet_service.get_selected_sheet_draft(case.id)
    result, error = order_service.save_ocr_sheet_exact(
        case.id,
        header=["日付", "区分", "メニュー", "常食2F"],
        rows=[["03/22", "朝", "Menu A", "5"]],
        ui_mode="sheet",
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        row_ids=["row-1"],
    )

    assert error is None
    assert isinstance(result, dict)
    decision = critical_decision_service.get_acknowledged_candidate_evidence_decision(case.id)
    assert decision is not None
    assert decision["selected_value"] == case.evidence
    assert decision["selected_by"] == "save-ocr-sheet-exact"
    assert result["draft"]["base_evidence_run_id"] == case.evidence
    assert result["draft"]["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "5"]]
    with case.readonly():
        assert order_service.get_ocr_evidence_run(case.evidence) == original_evidence
        assert draft_sheet_service.get_latest_sheet_draft(case.id) == result["draft"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == selected_draft


def test_cached_workflow_without_candidate_identity_cannot_acknowledge_latest_evidence(authored_sheet_case):
    case, first, saved, second = _saved_five_with_owned_candidate(authored_sheet_case)
    second_record = order_service.get_ocr_evidence_run(second["id"])
    projected = workflow_state_service.refresh_workflow_state(case.id)
    assert projected["candidate_evidence_run_id"] == second["id"]
    cached = order_service.get_order_workflow_state(case.id, refresh=False)
    assert isinstance(cached, dict)
    assert "candidate_evidence_run_id" not in cached
    decision = critical_decision_service.get_acknowledged_candidate_evidence_decision(case.id)

    with case.readonly():
        workflow, error = order_service.acknowledge_current_candidate_evidence(
            case.id, selected_by="tester",
        )
        assert workflow is None
        assert error == "candidate_not_found"
        assert critical_decision_service.get_acknowledged_candidate_evidence_decision(case.id) == decision
        assert order_service.get_order_workflow_state(case.id, refresh=False) == cached
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["base_evidence_run_id"] == first["id"]
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert order_service.get_ocr_evidence_run(second["id"]) == second_record


def test_internal_apply_materializes_owned_saved_five_and_records_real_acknowledgement(authored_sheet_case):
    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == case.evidence
    evidence = order_service.get_ocr_evidence_run(case.evidence)
    applied, apply_error = order_service.apply_latest_draft(case.id)

    assert apply_error is None
    assert isinstance(applied, dict)
    assert applied["id"] == case.id
    assert applied["reparse"]["materialized_source"] == "draft_sheet"
    with session_scope() as session:
        lines = session.query(OrderLine).filter_by(order_id=case.id).all()
        assert len(lines) == 1
        assert lines[0].date.isoformat() == "2026-03-22"
        assert lines[0].menu_name == "Menu A"
        assert lines[0].quantity_original == 5
        assert lines[0].quantity_corrected is None
    decision = critical_decision_service.get_acknowledged_candidate_evidence_decision(case.id)
    assert decision is not None
    assert decision["selected_value"] == case.evidence
    assert decision["selected_by"] == "apply-latest-draft"
    with case.readonly():
        assert order_service.get_ocr_evidence_run(case.evidence) == evidence
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved


def test_internal_acknowledgement_uses_explicit_selected_source_without_a_candidate(authored_sheet_case):
    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, case.selected_sheet)
    before = workflow_state_service.project_workflow_state(case.id)
    assert before["candidate_evidence_run_id"] is None
    assert before["active_evidence_run_id"] == case.evidence
    original_evidence = order_service.get_ocr_evidence_run(case.evidence)
    refreshed = order_service._acknowledge_current_candidate_after_authoritative_action(
        case.id,
        selected_by="save-ocr-sheet-exact",
    )

    assert isinstance(refreshed, dict)
    assert refreshed["active_evidence_run_id"] == case.evidence
    assert refreshed["candidate_evidence_run_id"] is None
    decision = critical_decision_service.get_acknowledged_candidate_evidence_decision(case.id)
    assert decision is not None
    assert decision["order_id"] == case.id
    assert decision["selected_value"] == case.evidence
    assert decision["selected_by"] == "save-ocr-sheet-exact"
    with case.readonly():
        assert order_service.get_ocr_evidence_run(case.evidence) == original_evidence
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == case.selected_sheet["rows"]


def test_refresh_workflow_state_uses_owned_terminal_metrics_to_surface_new_candidate(authored_sheet_case):
    case, first, saved, second = _saved_five_with_owned_candidate(authored_sheet_case)
    job = get_ocr_job(case.job)
    assert job["status"] == "done"
    assert job["metrics"]["new_evidence_available"] is True
    assert job["metrics"]["evidence_run_id"] == second["id"]
    assert job["metrics"]["rerun_terminal_receipt"] == second["terminal_receipt"]
    assert saved["base_evidence_run_id"] == first["id"]
    workflow = workflow_state_service.refresh_workflow_state(case.id)

    assert isinstance(workflow, dict)
    assert workflow["state"] == "new_evidence_available"
    assert workflow["active_evidence_run_id"] == first["id"]
    assert workflow["candidate_evidence_run_id"] == second["id"]
    with case.readonly():
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert get_ocr_job(case.job) == job


def test_owned_selected_eight_is_not_replaced_by_stale_candidate_five(authored_sheet_case):
    from copy import deepcopy
    from src.models.ocr_job import OcrJob

    case = authored_sheet_case(quantity=5, include_remarks=False)
    older = case.evidence
    source = case.produce(cell_values="8")
    latest = case.evidence
    assert latest != older
    assert source["selected_ocr_result_id"] == latest
    assert source["sheet"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
    saved = save_draft(case, {
        "order_id": case.id, "source": "weekly_menu+ocr_payload",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [["03/22", "朝", "Menu A", "8"]], "row_ids": ["row-1"],
    })
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == latest
    with session_scope() as session:
        job = session.get(OcrJob, case.job)
        receipt = deepcopy(job.metrics["rerun_terminal_receipt"])
        # Deliberate stale diagnostic metadata; never alter physical ownership or its receipt.
        job.metrics = {**job.metrics, "request_mode": "ocr_rerun",
                       "new_evidence_available": True, "evidence_run_id": older}
        assert job.metrics["rerun_terminal_receipt"] == receipt
    before_job = get_ocr_job(case.job)
    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(case.id)
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["base_evidence_run_id"] == latest
        assert current["rows"] == source["sheet"]["rows"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["active_evidence_run_id"] == latest
    assert workflow["candidate_evidence_run_id"] is None
    assert get_ocr_job(case.job) == before_job
    assert before_job["metrics"]["rerun_terminal_receipt"] == receipt


def test_owned_running_rerun_retains_selected_saved_five(authored_sheet_case):
    from tests.helpers.authored_sheet_case import prepare_rerun

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    active = case.evidence
    calls = prepare_rerun(case, cell_values="9")
    with case.execution():
        progress = order_service._update_reparse_job_progress(case.job,
            status="running", processing_stage="ocr_pipeline", result_state="running",
            metrics_patch={"request_mode": "ocr_rerun", "order_id": case.id})
    assert progress is not None and progress["status"] == "running"
    before_job = get_ocr_job(case.job)
    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(case.id)
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["base_evidence_run_id"] == active
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
    assert isinstance(workflow, dict)
    assert workflow["state"] == "rerun_in_progress"
    assert workflow["primary_action"] == "wait_for_rerun"
    assert case.get("workflow-v2")["state"] == "ocr_running"
    assert get_ocr_job(case.job) == before_job
    assert calls == []


def test_unowned_failed_jobs_cannot_heal_or_replace_owned_saved_five(authored_sheet_case, tmp_path):
    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    active = case.evidence
    owned_job = get_ocr_job(case.job)
    assert owned_job["status"] == "done"
    pending = tmp_path / "ocr_rerun_pending.json"
    pending_payload = {
        "status": "running", "stage": "ocr", "input_reference": "file://dummy-workflow.pdf",
        "output_reference": f"file://{pending}",
    }
    completed_payload = {
        "status": "done",
        "stage": "done",
        "input_reference": "gs://bucket/input/OCR-order-rerun-timeout.pdf",
        "output_reference": "gs://bucket/output/OCR-order-rerun-timeout.pdf.json",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
            }
        ],
        "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|9|",
        "tables": [{"page_index": 1, "rows": [["03/22", "朝", "Menu A", "9"]]}],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
            "confidence": 0.99,
            "blocked": False,
            "blocked_reasons": [],
        },
        "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/22", "朝", "Menu A", "9"]]}],
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }
    specimens = [
        ("failed-pending", pending, pending_payload, "ocr_pipeline_failed:SystemExit(1)",
         {"processing_stage": "ocr_pipeline", "request_mode": "ocr_rerun",
          "result_state": "hard_failed", "order_id": case.id}),
        ("failed-timeout-nine", tmp_path / "ocr-rerun-timeout.pdf.json", completed_payload,
         "evidence_rerun_failed:OCR pipeline output not found",
         {"processing_stage": "ocr_pipeline", "request_mode": "ocr_rerun",
          "status": "failed", "error": "evidence_rerun_failed"}),
    ]
    for case_id, path, payload, failure, metrics in specimens:
        path.write_text(json.dumps(payload), encoding="utf-8")
        case.protected += (path,)
        # Deliberately unowned diagnostic jobs, not fabricated successful executions.
        job_id = f"OCR-unowned-{case.id}-{case_id}"
        created, fresh = create_job(job_id, input_reference="file://dummy-workflow.pdf",
            status="failed", order_id=case.id)
        assert fresh is True
        update_job(job_id, status="failed", output_reference=f"file://{path}",
            error_message=failure, metrics=metrics)
        before = get_ocr_job(job_id)
        assert before["metrics"].get("workflow_attempt_id") is None
        assert before["metrics"].get("rerun_terminal_receipt") is None
        with case.readonly():
            assert order_service._reconcile_finished_ocr_rerun(case.id) is False
            current = order_service.get_current_sheet_context(case.id)
            assert current["draft_id"] == saved["id"]
            assert current["base_evidence_run_id"] == active
            assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
            assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
            assert json.loads(path.read_text()) == payload
        workflow = case.get("workflow-v2")
        assert workflow["state"] == "sheet_saved"
        assert workflow["selected_ocr_result_id"] == active
        assert workflow["saved_sheet_id"] == saved["id"]
        assert workflow["ocr_job"]["status"] == "done"
        assert get_ocr_job(case.job) == owned_job
        assert get_ocr_job(job_id) == before
        assert before["status"] == "failed"
        assert before["error_message"] == failure
        assert before["output_reference"] == f"file://{path}"


def _unowned_completed_rerun_specimen(case_id, order_id):
    specimens = {
        "awaiting-newer-nine": {
            "status": "done",
            "stage": "done",
            "input_reference": "gs://bucket/input/OCR-order-rerun-new.pdf",
            "output_reference": "gs://bucket/output/OCR-order-rerun-new.pdf.json",
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                    "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|9|",
            "tables": [{"page_index": 1, "rows": [["03/22", "朝", "Menu A", "9"]]}],
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
            },
            "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/22", "朝", "Menu A", "9"]]}],
            "table_box": [0.1, 0.2, 0.9, 0.8],
            "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
            "grid_row_edges": [0.2, 0.4, 0.8],
        },
        "awaiting-older-eight": {
            "status": "done",
            "stage": "done",
            "input_reference": "gs://bucket/input/OCR-order-rerun-old.pdf",
            "output_reference": f"gs://bucket/output/OCR-{order_id}_20260322_115959_999999.pdf.json",
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png"}],
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|8|",
            "tables": [{"page_index": 1, "rows": [["03/22", "朝", "Menu A", "8"]]}],
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
            },
            "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/22", "朝", "Menu A", "8"]]}],
            "table_box": [0.1, 0.2, 0.9, 0.8],
            "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
            "grid_row_edges": [0.2, 0.4, 0.8],
        },
        "running-current-nine": {
            "status": "done",
            "stage": "done",
            "input_reference": "gs://bucket/input/OCR-order-rerun.pdf",
            "output_reference": "gs://bucket/output/OCR-order-rerun.pdf.json",
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                    "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|8|",
            "tables": [{"page_index": 1, "rows": [["03/22", "朝", "Menu A", "8"]]}],
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
            },
            "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/22", "朝", "Menu A", "8"]]}],
            "table_box": [0.1, 0.2, 0.9, 0.8],
            "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
            "grid_row_edges": [0.2, 0.4, 0.8],
        },
    }
    return specimens[case_id]


@pytest.mark.parametrize("case_id", [
    "awaiting-newer-nine", "awaiting-older-eight", "running-current-nine",
])
def test_unowned_completed_file_cannot_finish_owned_rerun(authored_sheet_case, tmp_path, case_id):
    from tests.helpers.authored_sheet_case import prepare_rerun

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    active = case.evidence
    calls = prepare_rerun(case, cell_values="9")
    completed_payload = _unowned_completed_rerun_specimen(case_id, case.id)
    completed = tmp_path / (
        f"OCR-{case.id}_20260322_115959_999999.pdf.json"
        if case_id == "awaiting-older-eight" else "ocr-rerun-completed.json")
    completed.write_text(json.dumps(completed_payload), encoding="utf-8")
    pending = tmp_path / (
        f"OCR-{case.id}_20260322_120000_000001.pdf.json"
        if case_id == "awaiting-older-eight" else "ocr_rerun_pending_current_ref.json")
    pending_payload = {
        "status": "running", "stage": "ocr", "input_reference": "file://dummy-workflow.pdf",
        "output_reference": f"file://{pending}",
    }
    pending.write_text(json.dumps(pending_payload), encoding="utf-8")
    case.protected += (completed, pending)
    status = "running" if case_id == "running-current-nine" else "awaiting_output"
    extra = {}
    if case_id == "awaiting-older-eight":
        extra = {"awaiting_output_since": "2026-03-22T12:00:00",
                 "stage_updated_at": "2026-03-22T12:00:00"}
    reference = completed if case_id == "running-current-nine" else pending
    with case.execution():
        progress = order_service._update_reparse_job_progress(case.job, status=status,
            processing_stage="ocr_pipeline", result_state=status,
            error_message=None if status == "running" else "ocr_output_pending",
            metrics_patch={"request_mode": "ocr_rerun", "order_id": case.id, **extra})
        assert progress is not None
        updated, accepted = order_service.update_job_for_workflow_attempt(
            case.job, case.queued["ocr_job"]["workflow_attempt_id"], order_id=case.id,
            expected_workflow_revision=case.queued["workflow"]["revision"],
            output_reference=f"file://{reference}")
    assert accepted is True
    assert updated["output_reference"] == f"file://{reference}"
    before_job = get_ocr_job(case.job)
    with case.readonly():
        assert order_service._reconcile_finished_ocr_rerun(case.id) is False
        current = order_service.get_current_sheet_context(case.id)
        latest = order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False)
        assert latest["id"] == active
        assert current["draft_id"] == saved["id"]
        assert current["base_evidence_run_id"] == active
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert json.loads(completed.read_text()) == completed_payload
        assert json.loads(pending.read_text()) == pending_payload
    state = case.get("workflow-v2")
    assert state["state"] == "ocr_running"
    assert state["selected_ocr_result_id"] == active
    assert get_ocr_job(case.job) == before_job
    assert before_job["status"] == status
    assert before_job["metrics"].get("rerun_terminal_receipt") is None
    assert calls == []


def test_owned_native_nine_requires_real_completion_selection_and_save(authored_sheet_case):
    case = authored_sheet_case(quantity=5, include_remarks=False)
    first = case.evidence
    source = case.produce(cell_values="9")
    assert case.evidence != first
    receipt = order_service.get_ocr_evidence_run(case.evidence)["terminal_receipt"]
    assert receipt["status"] == "done"
    assert receipt["evidence_run_id"] == case.evidence
    assert source["sheet"]["rows"] == [["03/22", "朝", "Menu A", "9"]]
    saved = save_draft(case, source["sheet"])
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["base_evidence_run_id"] == case.evidence
        assert current["rows"] == source["sheet"]["rows"]
    assert case.get("workflow-v2")["selected_ocr_result_id"] == case.evidence
    assert get_ocr_job(case.job)["status"] == "done"








def test_owned_failed_rerun_retains_selected_saved_five(monkeypatch, authored_sheet_case):
    from src.services import hakodate_cell_ocr_batch_service as batch
    from tests.helpers.authored_sheet_case import prepare_rerun

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    active = case.evidence
    calls = prepare_rerun(case, cell_values="9")

    def recognizer_failed(**_kwargs):
        raise RuntimeError("authored_external_recognizer_failed")

    monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", recognizer_failed)
    with case.execution():
        result, error = order_service.rerun_ocr_evidence_only(case.id, job_id=case.job,
            project_sheet=False, refresh_workflow=False)
    assert error == "hakodate_live_rerun_failed"
    assert result["terminal_receipt"]["status"] == "failed"
    assert result["terminal_receipt"]["error"] == error
    assert result["terminal_receipt"]["evidence_run_id"] is None
    assert calls == []
    before_job = get_ocr_job(case.job)
    assert before_job["status"] == "failed"
    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(case.id)
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["base_evidence_run_id"] == active
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
    assert isinstance(workflow, dict)
    assert workflow["state"] == "rerun_failed_keep_current"
    assert workflow["primary_action"] == "rerun_ocr_pipeline"
    assert "rerun_failed_keep_current" in (workflow["warnings_json"] or [])
    assert case.get("workflow-v2")["state"] == "ocr_failed"
    assert get_ocr_job(case.job) == before_job


def test_unowned_llm_reparse_job_cannot_become_current_rerun_or_replace_formally_saved_five(authored_sheet_case):
    from src.models.ocr_job import OcrJob

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, case.selected_sheet)
    original = _legacy_unowned_evidence_payload()
    _assert_unowned_input_cannot_replace_selected(case, original, case.selected_sheet)
    selected = order_service.get_ocr_evidence_run(case.evidence)
    terminal_job = get_ocr_job(case.job)
    job_id = "OCR-unowned-llm-reparse-diagnostic"
    _, created = create_job(job_id, str(case.pdf), status="running", order_id=case.id)
    assert created
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        # This is deliberately an unowned legacy diagnostic, never a terminal receipt.
        job.metrics = {"processing_stage": "llm_reparse", "request_mode": "llm_reparse"}
        assert job.workflow_attempt_id is None
    diagnostic = get_ocr_job(job_id)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] != "rerun_in_progress"
        assert state["primary_action"] == "apply_draft"
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_ocr_evidence_run(case.evidence) == selected
        assert get_ocr_job(case.job) == terminal_job
        assert get_ocr_job(job_id) == diagnostic


def test_legacy_column_choice_cannot_override_authored_canonical_columns(authored_sheet_case):
    from src.services.candidate_resolution_service import build_column_mapping_resolution
    from tests.contract.test_orders_ocr_sheet_history_api import LEGACY

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    original = _legacy_unowned_evidence_payload(extra_payload={
        "column_mapping_candidates": [
            {"value": "cols-a", "label": "常食 / 軟菜 / ミキサー", "score": 0.62},
            {"value": "cols-b", "label": "常食 / 常食(袋分け) / 軟菜", "score": 0.58},
        ],
    })
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        resolution = build_column_mapping_resolution(original)
        assert resolution["requires_user_choice"] is True
        assert "column_mapping_choice_required" in resolution["blocked_reasons"]
        assert resolution["decision_type"] == "column_mapping"
        assert [candidate["value"] for candidate in resolution["candidates"]] == ["cols-a", "cols-b"]
    requests = [
        ("GET", "critical-decisions", None),
        ("POST", "critical-decisions/column_mapping", {"value": "cols-a", "selected_by": "test"}),
    ]
    for method, endpoint, body in requests:
        with case.readonly():
            response = case.client.request(method, f"/orders/{case.id}/{endpoint}",
                headers=case.actor.headers, json=body)
            assert response.status_code == 410
            assert response.json() == {"detail": {**LEGACY, "endpoint": "critical-decisions"}}
            assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
            assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "apply_ready"
        assert state["can_apply"] is True and state["apply_gate"]["can_apply"] is True
        assert type(state["can_confirm"]) is bool
        assert state["can_confirm"] == state["apply_gate"]["can_confirm"]
        blockers = state["apply_gate"]["blockers"]
        assert "column_mapping_choice_required" not in blockers
        assert "draft_rows_empty" not in blockers
        assert "column_mapping" not in state["candidate_resolution"]["gate_summary"]["choice_required_types"]
        assert state["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] != "position_fallback"
        assert order_service.get_current_sheet_context(case.id)["fields"] == [
            "date_mmdd", "daypart", "menu", "qty.regular_2f"]


def test_unowned_template_resolution_failure_cannot_override_owned_saved_source(authored_sheet_case):
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    original = _legacy_unowned_evidence_payload(extra_payload={
        "template_resolution": {"resolved_template_id": "", "candidate_template_ids": [],
            "confidence": 0.2, "blocked": True, "blocked_reasons": ["template_resolution_missing"]},
    })
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert isinstance(state, dict)
        assert state["state"] == "apply_ready"
        assert state["primary_action"] == "apply_draft"
        assert "semantic_shell_only" not in state["apply_gate"]["blockers"]
        assert "template_unresolved" not in state["apply_gate"]["blockers"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


@pytest.mark.parametrize("fault,code", [
    ("missing", "authored_fax_template_missing"),
    ("incomplete", "authored_fax_template_incomplete"),
    ("ambiguous", "authored_fax_template_ambiguous"),
])
def test_actual_authored_template_defects_are_refused_without_guessing_or_state_writes(authored_sheet_case, fault, code):
    case = authored_sheet_case(quantity=5, include_remarks=False, produce=False)
    authored = json.loads(case.source.read_text())
    envelope = authored["facilities"][0]
    for config in (envelope, envelope["versions"][0]["facility"]):
        if fault == "missing":
            config.pop("fax_template")
        elif fault == "incomplete":
            config["fax_template"]["columns"] = []
        else:
            config["fax_template_ids"] = [config["fax_template_id"], "unselected-other-template"]
    case.source.write_text(json.dumps(authored), encoding="utf-8")
    with case.readonly():
        with pytest.raises(ValueError, match="^" + code + "$"):
            config_service.get_authored_facility_config(case.facility)
        assert order_service.get_latest_sheet_draft(case.id) is None
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None


def _store_unowned_workflow_payload(case, payload):
    from src.models.order_ocr_cache import OrderOcrCache

    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        assert cached is not None
        cached.payload = payload


def test_unowned_numeric_diagnostics_cannot_block_owned_saved_five(authored_sheet_case):
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved = save_draft(case, _semantic_initial_draft(case.id))
    original = _legacy_unowned_evidence_payload(extra_payload={
        "cell_issues": [{"issue_code": "column_swap"}, {"issue_code": "merged_numeric_cell"}],
        "failed_cells": [{"row_index": 0, "column_index": 3}],
    })
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "apply_ready"
        assert state["primary_action"] == "apply_draft"
        assert state["can_apply"] is True
        assert state["apply_gate"]["can_apply"] is True
        assert type(state["can_confirm"]) is bool
        assert state["can_confirm"] == state["apply_gate"]["can_confirm"]
        assert "column_mapping_review_required" not in state["apply_gate"]["warnings"]
        assert "quantity_review_required" not in state["apply_gate"]["warnings"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


def test_formal_operator_twenty_one_is_not_blocked_by_unowned_low_trust_flags(authored_sheet_case):
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=5, area="X",
        cell_values=lambda region: "5" if region["role"] == "quantity" else "")
    edited = {"order_id": case.id, "source": "weekly_menu+ocr_payload",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "header": ["日付", "区分", "メニュー", "常食", "備考"],
        "rows": [["03/22", "朝", "Menu A", "21", ""]], "row_ids": ["draft-row-1"]}
    saved = save_draft(case, edited)
    assert saved is not None
    original = _legacy_unowned_evidence_payload(extra_payload={
        "cell_issues": [{"issue_code": "merged_numeric_cell"}],
        "warnings": ["sheet_payload_mapping_low_confidence"],
    })
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "apply_ready"
        assert state["primary_action"] == "apply_draft"
        assert state["apply_gate"]["can_apply"] is True
        assert type(state["apply_gate"]["can_confirm"]) is bool
        assert "numeric_trust_low" not in state["apply_gate"]["warnings"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "21", ""]]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == original


def test_formal_regular_twenty_one_and_mixer_three_are_not_inferred_from_unowned_numeric_review(history_case):
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.mixer_x"]
    header = ["日付", "区分", "メニュー", "常食", "ミキサー"]
    columns = [{"index": i, "source_index": i, "name": field, "header": header[i],
        "role": ["date", "daypart", "menu_name", "quantity", "quantity"][i]}
        for i, field in enumerate(fields)]
    columns[3].update(diet_type="regular", area_id="X")
    columns[4].update(diet_type="mixer", area_id="X")
    period = "2026-03@2026-03-22~2026-03-28"
    case = history_case(facility="FAC00006", week=period, period=period,
        received=datetime(2026, 3, 22, 9), columns=columns, lines=[],
        menu_entries=[{"id": "fac6-manual-march22", "monthly_menu_id": "2026-03",
            "menu_date": date(2026, 3, 22), "daypart": "朝", "name": "Menu A",
            "slot_index": 0, "diet_type": "regular"}])
    raw_values = {"qty.regular_x": "5", "qty.mixer_x": ""}

    def recognize(region):
        from tests.helpers.authored_cell_values import declared_field
        values = {raw_values[declared_field(target, fields)] for target in region["logical_targets"]}
        assert len(values) == 1
        return values.pop()

    source = case.produce(cell_values=recognize)
    assert source["sheet"]["rows"] == [["03/22", "朝", "Menu A", "5", ""]]
    edited = {"order_id": case.id, "source": "weekly_menu+ocr_payload",
        "fields": fields, "header": header,
        "rows": [["03/22", "朝", "Menu A", "21", "3"]], "row_ids": ["row-1"]}
    saved = save_draft(case, edited)
    original = _legacy_unowned_evidence_payload(extra_payload={
        "cell_issues": [{"issue_code": "merged_numeric_cell"}],
        "warnings": ["sheet_payload_mapping_low_confidence", "sheet_ocr_review_required"],
    })
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "apply_ready"
        assert state["primary_action"] == "apply_draft"
        assert state["candidate_evidence_run_id"] is None
        assert state["apply_gate"]["can_apply"] is True
        assert "numeric_trust_low" not in state["apply_gate"]["warnings"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "21", "3"]]
        assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == [["03/22", "朝", "Menu A", "5", ""]]


def test_owned_semantic_twenty_six_and_manual_five_are_not_blocked_by_stale_low_trust_diagnostics(authored_sheet_case):
    case = authored_sheet_case(quantity=26, include_remarks=True, area="X",
        cell_values=lambda region: "" if region["role"] == "note" else "26",
        facility="FAC00015", menu_date=date(2026, 4, 26), period="2026-04@2026-04-26~2026-04-30")
    original = _legacy_unowned_evidence_payload(extra_payload={
            "facility_id": "FAC00015",
            "template_id": "fax_layout_regular_forbidden_v1",
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_forbidden_v1",
                "candidate_template_ids": ["fax_layout_regular_forbidden_v1"],
                "confidence": 0.96,
                "blocked": False,
                "blocked_reasons": [],
            },
            "cell_issues": [{"issue_code": "merged_numeric_cell"}],
            "failed_cells": [{"row_index": 0, "column_index": 3}],
        })
    expected = [["04/26", "朝", "Menu A", "26", ""]]
    assert case.selected_sheet["rows"] == expected
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "evidence_ready"
        assert state["primary_action"] == "open_draft"
    _assert_unowned_input_cannot_replace_selected(case, original, case.selected_sheet)
    sheet = {**case.selected_sheet, "rows": [["04/26", "朝", "Menu A", "5", "operator"]]}
    saved = save_draft(case, sheet)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "apply_ready"
        assert "quantity_review_required" not in state["apply_gate"]["warnings"]
        assert "numeric_trust_low" not in state["apply_gate"]["warnings"]
        assert order_service.get_current_sheet_context(case.id)["rows"] == sheet["rows"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved


def test_real_saved_review_warning_is_visible_without_blocking_quantity_edit_or_ocr_adoption(authored_sheet_case):
    case = authored_sheet_case(quantity=26, include_remarks=False, area="X",
        facility="FAC00015", menu_date=date(2026, 4, 26), period="2026-04@2026-04-26~2026-04-30")
    original = _legacy_unowned_evidence_payload(extra_payload={
            "facility_id": "FAC00015",
            "template_id": "fax_layout_regular_forbidden_v1",
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_forbidden_v1",
                "candidate_template_ids": ["fax_layout_regular_forbidden_v1"],
                "confidence": 0.96,
                "blocked": False,
                "blocked_reasons": [],
            },
            "cell_issues": [{"issue_code": "merged_numeric_cell"}],
            "failed_cells": [{"row_index": 0, "column_index": 3}],
        })
    _assert_unowned_input_cannot_replace_selected(case, original, case.selected_sheet)
    sheet = {**case.selected_sheet, "warnings": ["sheet_ocr_review_required"]}
    saved = save_draft(case, sheet)
    selected = order_service.get_ocr_evidence_run(case.evidence)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        state = workflow_state_service.project_workflow_state(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["rows"] == [["04/26", "朝", "Menu A", "26"]]
        assert "sheet_ocr_review_required" in current["warnings"]
        assert "ocr_review_required" in state["apply_gate"]["confirm_warnings"]
        assert "numeric_trust_low" not in state["apply_gate"]["blockers"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        assert order_service.get_ocr_evidence_run(case.evidence) == selected
    # A formal operator save clears this explicit review warning; no metadata SQL.
    corrected = {**case.selected_sheet, "rows": [["04/26", "朝", "Menu A", "5"]], "warnings": []}
    saved = save_draft(case, corrected)
    with case.readonly():
        assert order_service.get_current_sheet_context(case.id)["rows"] == corrected["rows"]
        assert "ocr_review_required" not in workflow_state_service.project_workflow_state(case.id)["apply_gate"]["confirm_warnings"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved


def _assert_refresh_workflow_state_blocks_when_monthly_menu_object_is_missing(authored_sheet_case):
    from src.models.menu import MonthlyMenu, MonthlyMenuItem

    case = authored_sheet_case(quantity=5, include_remarks=False)
    evidence = order_service.get_ocr_evidence_run(case.evidence)
    assert case.selected_sheet["rows"] == [["03/22", "朝", "Menu A", "5"]]
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MonthlyMenu).delete()
    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(case.id)
        current = order_service.get_current_sheet_context(case.id)
        assert current["menu_diagnostics"]["order_codes"] == ["monthly_menu_object_missing"]
        assert current["rows"] == []
        assert current["draft_payload"] is None
        assert order_service.get_ocr_evidence_run(case.evidence) == evidence

    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    assert workflow["primary_action"] == "resolve_blockers"
    blockers = workflow["apply_gate"]["blockers"] or []
    warnings = workflow["apply_gate"]["warnings"] or []
    assert "monthly_menu_object_missing" in blockers
    assert "monthly_menu_object_missing" not in warnings
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False


def test_project_workflow_state_blocks_when_monthly_menu_object_is_missing(authored_sheet_case):
    _assert_refresh_workflow_state_blocks_when_monthly_menu_object_is_missing(authored_sheet_case)


def test_formal_saved_five_preserves_weekly_warning_but_missing_physical_menu_blocks_output(authored_sheet_case):
    from src.models.menu import MonthlyMenu, MonthlyMenuItem
    from src.services import order_workflow_v2_service as workflow

    case = authored_sheet_case(quantity=5, include_remarks=False)
    sheet = {**case.selected_sheet, "row_ids": ["draft-row-1"], "warnings": ["sheet_weekly_menu_missing"]}
    saved = save_draft(case, sheet)
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MonthlyMenu).delete()
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        state = workflow_state_service.project_workflow_state(case.id)
        assert current["draft_id"] == saved["id"]
        assert current["rows"] == [["03/22", "朝", "Menu A", "5"]]
        assert "sheet_weekly_menu_missing" in current["warnings"]
        assert "monthly_menu_object_missing" in current["menu_diagnostics"]["order_codes"]
        assert "current_sheet_source_unavailable" in state["apply_gate"]["blockers"]
        assert state["apply_gate"]["can_apply"] is False
        assert state["apply_gate"]["can_confirm"] is False
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        result, error = workflow.run_bagging(case.id, expected_revision=revision)
        assert result is None and error == "current_sheet_source_unavailable"


def test_actual_initial_blank_semantic_sheet_is_editable_but_missing_menu_has_no_guessed_shell(authored_sheet_case):
    from src.models.menu import MonthlyMenu
    from src.services import order_workflow_v2_service as workflow

    case = authored_sheet_case(quantity=5, cell_values="", include_remarks=False)
    original = _legacy_unowned_evidence_payload()
    assert case.selected_sheet["rows"] == [["03/22", "朝", "Menu A", ""]]
    assert draft_sheet_service.get_selected_sheet_draft(case.id) is None
    _assert_unowned_input_cannot_replace_selected(case, original, case.selected_sheet)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        state = workflow_state_service.project_workflow_state(case.id)
        assert current["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
        assert current["rows"] == [["03/22", "朝", "Menu A", ""]]
        assert state["draft_id"] is None
        assert "numeric_trust_low" not in state["apply_gate"]["blockers"]
    # The old fake build_initial_sheet_draft supplied Menu A after losing its
    # authoritative menu. The current boundary must not fabricate that shell.
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenu).delete()
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        state = workflow_state_service.project_workflow_state(case.id)
        assert current["rows"] == []
        assert current["draft_payload"] is None
        assert "monthly_menu_object_missing" in current["menu_diagnostics"]["order_codes"]
        assert state["apply_gate"]["can_apply"] is False
        assert state["apply_gate"]["can_confirm"] is False
        assert "monthly_menu_object_missing" in state["apply_gate"]["blockers"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) is None
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        result, error = workflow.run_bagging(case.id, expected_revision=revision)
        assert result is None and error == "saved_sheet_required"


def test_project_workflow_state_blocks_on_menu_entries_missing_without_monthly_menu_object_missing(authored_sheet_case):
    from src.models.menu import MonthlyMenu

    case = authored_sheet_case(quantity=5, include_remarks=False)
    evidence = order_service.get_ocr_evidence_run(case.evidence)
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()
        assert session.get(MonthlyMenu, "2026-03") is not None
    with case.readonly():
        workflow = workflow_state_service.project_workflow_state(case.id)
        current = order_service.get_current_sheet_context(case.id)
        assert current["menu_diagnostics"]["order_codes"] == ["menu_entries_missing"]
        assert current["rows"] == []
        assert current["draft_payload"] is None
        assert order_service.get_ocr_evidence_run(case.evidence) == evidence

    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    blockers = workflow["apply_gate"]["blockers"] or []
    assert "menu_entries_missing" in blockers
    assert "monthly_menu_object_missing" not in blockers
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False


def test_refresh_workflow_state_uses_current_sheet_context_menu_diagnostics_without_relookup(monkeypatch, authored_sheet_case):
    # Actual saved-sheet scenario: missing menu diagnostics remain warnings;
    # missing physical inputs separately prevent downstream materialization.
    case = authored_sheet_case(quantity=5, include_remarks=False)
    order = {"id": case.id}
    save_draft(case, _semantic_initial_draft(case.id, source="ocr_table"))
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()

    def _unexpected_menu_context(**_kwargs):
        raise AssertionError("workflow menu context should come from current_sheet_context diagnostics")

    monkeypatch.setattr(workflow_state_service, "_build_menu_context", _unexpected_menu_context)

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    context = order_service.get_current_sheet_context(case.id)
    assert context["menu_diagnostics"]["order_codes"] == ["menu_entries_missing"]
    assert "menu_entries_missing" in workflow["apply_gate"]["apply_warnings"]
    assert "menu_entries_missing" in workflow["apply_gate"]["confirm_warnings"]
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False


def test_refresh_workflow_state_exposes_review_summary_from_sheet_gate(authored_sheet_case):
    case = authored_sheet_case(quantity=5, include_remarks=False)
    order = {"id": case.id}
    sheet = _semantic_initial_draft(case.id, source="review_blocked")
    sheet.update(rows=[], row_ids=[], warnings=["sheet_ocr_review_required"])
    saved = save_draft(case, sheet)
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    current = order_service.get_current_sheet_context(case.id)
    assert current["draft_id"] == saved["id"]
    assert current["draft_payload"] == sheet
    # The actual selected save remains a pending draft, not an OCR-only review.
    assert workflow["ocr_review_state"] == "draft_ready"
    assert workflow["ocr_review_stage"] == "needs_human_review"
    assert workflow["ocr_has_saved_draft"] is True
    assert workflow["ocr_draft_row_count"] == 0
    assert "menu_entries_missing" in workflow["apply_gate"]["apply_warnings"]
    assert "rows_empty" in (workflow["ocr_apply_blockers"] or [])
    assert "menu_entries_missing" in workflow["apply_gate"]["confirm_warnings"]
    assert "current_sheet_source_unavailable" in workflow["ocr_apply_blockers"]
    assert "current_sheet_source_unavailable" in workflow["ocr_confirm_blockers"]
    assert workflow["ocr_can_apply_draft"] is False
    assert workflow["ocr_can_confirm"] is False


def test_project_workflow_state_uses_canonical_order_week_over_stale_current_sheet_week(
    isolated_formal_schema, tmp_path, monkeypatch,
):
    from datetime import date
    from src.models.menu import MonthlyMenuEntry
    from tests.contract.auth_support import registered_hospital_operator
    from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase
    from tests.integration.test_ocr_sheet_history import _seed_monthly_menu_custom_entries

    week = "2026-03@2026-03-22~2026-03-28"
    with registered_hospital_operator(monkeypatch) as actor:
        f = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor,
            week="2026-03", period=week, received=datetime(2026, 3, 22, 9),
            lines=[], menu=False)
        _seed_monthly_menu_custom_entries(month_id="2026-03", month_start=date(2026, 3, 1),
            entries=[(date(2026, 3, 22), "朝", "Menu A", 0)])
        with session_scope() as session:
            session.get(MonthlyMenuEntry, "seed-entry-2026-03-custom-0").diet_type = "regular"
        source = f.produce()["sheet"]
        assert source["rows"][0][:3] == ["03/22", "朝", "Menu A"]
        sheet = {
            "source": "manual_draft",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
            "warnings": [],
            "resolved_week_id": "2026-03@2026-03-01~2026-03-07",
        }
        f.save(sheet)
        with f.readonly():
            current = order_service.get_current_sheet_context(f.id)
            workflow = workflow_state_service.project_workflow_state(f.id,
                current_sheet_context=current)
            assert isinstance(workflow, dict)
            assert workflow["candidate_resolution"]["resolutions"]["week"]["resolved_value"] == week
            assert current["resolved_week_id"] == week
            for key in ("fields", "header", "rows", "row_ids"):
                assert current[key] == sheet[key]
        assert f.get()["persisted_week_value"] == week
        assert f.get("workflow-v2/sheet")["sheet"] == sheet
        f.client.close()


def test_formal_clean_saved_five_outranks_unowned_layout_diagnostics_and_stale_save_is_refused(authored_sheet_case):
    from copy import deepcopy

    case = authored_sheet_case(quantity=5, area="X")
    original = _legacy_unowned_evidence_payload(extra_payload={
            "template_resolution": {
                "resolved_template_id": None,
                "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                "confidence": 0.42,
                "blocked": True,
                "blocked_reasons": ["template_resolution_missing"],
            },
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        })
    sheet = deepcopy(case.selected_sheet)
    sheet.update(source="draft_sheet", rows=[["03/22", "朝", "Menu A", "5", ""]], row_ids=["row-1"])
    saved = save_draft(case, sheet)
    _assert_unowned_input_cannot_replace_selected(case, original, sheet)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "apply_ready"
        assert state["primary_action"] == "apply_draft"
        assert state["apply_gate"]["can_apply"] is True
        assert "semantic_shell_only" not in state["apply_gate"]["blockers"]
        assert "template_unresolved" not in state["apply_gate"]["blockers"]
        assert "numeric_trust_low" not in state["apply_gate"]["warnings"]
        assert "draft_newer_than_lines" in state["apply_gate"]["warnings"]
    stale = case.revision()
    latest = save_draft(case, sheet)
    assert latest["id"] != saved["id"]
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        response = case.client.put(f"/orders/{case.id}/workflow-v2/sheet",
            headers={**case.actor.headers, "If-Match": f'"{stale}"'},
            json={"sheet": {**sheet, "rows": [["03/22", "朝", "Menu A", "9", "stale"]]}, "edited_by": "stale-actor"})
        assert response.status_code == 409, response.text
    with case.readonly():
        assert order_service.get_current_sheet_context(case.id)["rows"] == sheet["rows"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == latest


def test_refresh_workflow_state_uses_persisted_draft_without_exact_revision(authored_sheet_case):
    case = authored_sheet_case(quantity=5, area="X")
    order = {"id": case.id}
    saved = save_draft(case, {
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [["03/22", "朝", "Menu A", "8", "manual"]],
            "row_ids": ["row-1"],
        })
    assert saved is not None

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["ocr_has_saved_draft"] is True
    assert workflow["ocr_draft_row_count"] == 1
    assert workflow["ocr_draft_updated_at"] == saved["edited_at"]


def test_project_workflow_state_does_not_mutate_saved_sheet_blockers_on_read(
    isolated_formal_schema, tmp_path, monkeypatch,
):
    from datetime import date
    from src.models.order_sheet_draft import OrderSheetDraft
    from src.models.menu import MonthlyMenuEntry
    from tests.contract.auth_support import registered_hospital_operator
    from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase
    from tests.integration.test_ocr_sheet_history import _seed_monthly_menu_custom_entries

    week = "2026-03@2026-03-22~2026-03-28"
    with registered_hospital_operator(monkeypatch) as actor:
        f = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor,
            week=week, period=week, received=datetime(2026, 3, 22, 9),
            lines=[], menu=False)
        _seed_monthly_menu_custom_entries(month_id="2026-03", month_start=date(2026, 3, 1),
            entries=[(date(2026, 3, 22), "朝", "Menu A", 0)])
        with session_scope() as session:
            session.get(MonthlyMenuEntry, "seed-entry-2026-03-custom-0").diet_type = "regular"
        f.produce()
        sheet = {
            "order_id": f.id,
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [
                ["03/22", "朝", "Menu A", "5", ""],
                ["01/01", '"', "Ghost Menu", "23", ""],
            ],
            "row_ids": ["draft-2", "draft-1"],
            "warnings": ["stale-current-warning-should-drop"],
        }
        saved = f.save(sheet)
        assert saved is not None
        # Historical blocked metadata on a genuinely captured/saved draft, not a receipt double.
        with session_scope() as session:
            state = session.get(OrderWorkflowState, f.id)
            draft = session.get(OrderSheetDraft, state.draft_id)
            draft.draft_state = "auto_apply_blocked"
            draft.blockers_json = ["sheet_canonical_mismatch"]
            draft.warnings_json = ["sheet_ocr_review_required"]
        before = draft_sheet_service.get_latest_sheet_draft(f.id)
        with f.readonly():
            workflow = workflow_state_service.project_workflow_state(f.id)
            refreshed_draft = draft_sheet_service.get_latest_sheet_draft(f.id)
            assert refreshed_draft is not None
            assert refreshed_draft == before
            assert refreshed_draft["blockers_json"] == ["sheet_canonical_mismatch"]
            assert refreshed_draft["warnings_json"] == ["sheet_ocr_review_required"]
            assert refreshed_draft["draft_sheet_json"]["rows"] == [
                ["03/22", "朝", "Menu A", "5", ""],
                ["01/01", '"', "Ghost Menu", "23", ""],
            ]
            assert refreshed_draft["draft_sheet_json"] == sheet
            assert isinstance(workflow, dict)
            assert workflow["state"] == "draft_blocked"
            assert workflow["apply_gate"]["can_apply"] is False
            assert "sheet_canonical_mismatch" in (workflow["apply_gate"]["blockers"] or [])
        assert f.get("workflow-v2/sheet")["sheet"] == sheet
        f.client.close()


@pytest.mark.parametrize("candidate_kind", ["critical_quantity_candidates", "quantity_candidates"],
    ids=["refuse_legacy_high_impact_quantity_adoption", "formal_manual_three_not_legacy_quantity_choice"])
def test_legacy_quantity_choices_are_not_authority_for_current_operator_values(authored_sheet_case, candidate_kind):
    from src.services.candidate_resolution_service import build_quantity_resolution
    from tests.contract.test_orders_ocr_sheet_history_api import LEGACY

    case = authored_sheet_case(quantity=5, include_remarks=False)
    saved_five = save_draft(case, _semantic_initial_draft(case.id))
    if candidate_kind == "critical_quantity_candidates":
        candidates = [
            {"candidate_id": "qty-a", "candidate_type": "critical_quantity_candidate",
             "value": "qty-a", "label": "3を採用", "score": 0.81,
             "reason": "high_impact_total_cell",
             "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4}, "critical": True},
            {"candidate_id": "qty-b", "candidate_type": "critical_quantity_candidate",
             "value": "qty-b", "label": "8を採用", "score": 0.76,
             "reason": "high_impact_total_cell",
             "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4}, "critical": True},
        ]
        extra = {candidate_kind: candidates}
    else:
        candidates = [{"value": "qty-a", "label": "3を採用", "score": 0.81},
                      {"value": "qty-b", "label": "8を採用", "score": 0.76}]
        extra = {"cell_issues": [{"issue_code": "merged_numeric_cell"}], candidate_kind: candidates}
    original = _legacy_unowned_evidence_payload(extra_payload=extra)
    _store_unowned_workflow_payload(case, original)
    with case.readonly():
        resolution = build_quantity_resolution(original)
        assert resolution["requires_user_choice"] is True
        assert "quantity_choice_required" in resolution["blocked_reasons"]
        assert resolution["ambiguity_scope"] == "high_impact_quantity"
        assert [candidate["value"] for candidate in resolution["candidates"]] == ["qty-a", "qty-b"]
        response = case.client.post(f"/orders/{case.id}/critical-decisions/quantity",
            headers=case.actor.headers, json={"value": "qty-a", "selected_by": "test"})
        assert response.status_code == 410
        assert response.json() == {"detail": {**LEGACY, "endpoint": "critical-decisions"}}
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved_five
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/22", "朝", "Menu A", "5"]]
    # The replacement is an explicit operator sheet value, never a guessed candidate adoption.
    edited = _semantic_initial_draft(case.id, rows=[["03/22", "朝", "Menu A", "3"]])
    saved_three = save_draft(case, edited)
    with case.readonly():
        state = workflow_state_service.project_workflow_state(case.id)
        assert state["state"] == "apply_ready"
        assert state["apply_gate"]["can_apply"] is True
        assert "quantity_review_required" not in state["apply_gate"]["warnings"]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved_three
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == case.evidence
        assert current["rows"] == [["03/22", "朝", "Menu A", "3"]]
