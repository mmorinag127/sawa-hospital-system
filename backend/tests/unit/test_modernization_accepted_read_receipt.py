"""Owned SQLite service components, not native OCR, HTTP/UI or live proof.

Evidence is manually seeded by unchanged fixtures. Bagging/confirmation are real.
Faults below are deliberate direct DB drift, not claims about normal public saves.
Bagged orders have no OrderLines: public daily/totals lists exclude them naturally.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json
import re

import pytest
from sqlalchemy import event, select

from test_modernization_real_bagging_confirmation import (
    actual_bagging,
    owned_bagging as owned_bagging,
)
from test_modernization_total_facility_receipt import confirmed_receipt as confirmed_receipt
from test_modernization_workflow_surface_parity import database_snapshot, surface as surface
from src.models.order import OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import config_service, menu_service, output_builder, total_service
from src.services import order_service as orders
from src.services.order_output_artifact_service import payload_digest


@pytest.fixture(params=["bagged", "confirmed"])
def accepted_receipt(request):
    if request.param == "confirmed":
        fixture, candidate = request.getfixturevalue("confirmed_receipt")
    else:
        fixture = request.getfixturevalue("owned_bagging")
        candidate = actual_bagging(fixture)["bagging_result"]["materialization_candidate"]
    return fixture, candidate, request.param


@contextmanager
def read_only(fixture, monkeypatch):
    """Observe actual SQL; forbidden recaptures are sentinels, not value doubles."""
    with fixture.session_scope() as session:
        engine = session.get_bind()
    writes = []

    def observe(_conn, _cursor, statement, _parameters, context, _executemany):
        if (context.isinsert or context.isupdate or context.isdelete
                or re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE)\b", statement, re.I)):
            writes.append(statement)

    def forbidden(*_args, **_kwargs):
        pytest.fail("accepted read recaptured current policy/menu or write adoption input")

    before = database_snapshot()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(orders, "_capture_synchronous_cache_write_input", forbidden)
            patch.setattr(config_service, "capture_authored_processing_config", forbidden)
            patch.setattr(config_service, "load_ingest_policy", forbidden)
            patch.setattr(orders, "_build_position_menu_entries_safe", forbidden)
            patch.setattr(menu_service, "get_menu_entries_for_facility", forbidden)
            yield
        assert database_snapshot() == before
        assert writes == []
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def candidate_read(fixture):
    return output_builder._accepted_output_candidate(orders.get_order_by_id(fixture.order_id))


def public_readers(candidate):
    # Actual accepted menu/date, not an invented target to force a route to run.
    line = next(line for line in candidate["output_lines"] if line["quantity_original"] == 7)
    target = line["date"]
    if not isinstance(target, date):
        target = date.fromisoformat(target)
    return (
        ("totals", lambda: total_service.build_totals(target, target, include_order_refs=True)),
        ("daily", lambda: orders.get_daily_bag_summary(target)),
        ("detail", lambda: orders.list_daily_output_override_editor_rows(
            target, daypart=line["daypart"], menu_name=line["menu_name"],
            menu_category=line.get("menu_category"),
        )),
    )


def assert_rejected(fixture, candidate, phase, error, monkeypatch):
    # The intentional mutation has already committed. Reads must preserve that
    # exact corrupted DB, not repair it or claim equality to the pristine DB.
    readers = [("candidate", lambda: candidate_read(fixture))]
    if phase == "confirmed":
        readers.extend(public_readers(candidate))
    for name, read in readers:
        with read_only(fixture, monkeypatch):
            with pytest.raises(ValueError) as raised:
                read()
            assert str(raised.value) == error, name


@pytest.mark.parametrize("rename_root", [False, True], ids=["root-unchanged", "root-renamed"])
def test_valid_accepted_reads_preserve_full_candidate_and_owned_quantities(
    accepted_receipt, rename_root, monkeypatch,
):
    fixture, candidate, phase = accepted_receipt
    original = fixture.master_path.read_bytes()
    saved = fixture.saved()
    assert [row[4] for row in saved["rows"]] == ["7", "", "", "0"]
    assert [(line["source_row_index"], line["source_col_index"], line["quantity_original"])
            for line in candidate["lines"]] == [(0, 4, 7), (3, 4, 0)]
    try:
        root = json.loads(original)
        versions = deepcopy(root["facilities"][0]["versions"])
        if rename_root:
            root["facilities"][0]["facility_name"] = "Different root only"
            fixture.master_path.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")
        assert json.loads(fixture.master_path.read_bytes())["facilities"][0]["versions"] == versions
        if phase == "confirmed":
            with fixture.session_scope() as session:
                state = session.get(OrderWorkflowState, fixture.order_id)
                snapshot = session.get(OrderConfirmedSnapshot, state.confirmed_snapshot_id)
                top = deepcopy(snapshot.snapshot_json["materialization_candidate"])
                receipt = top.pop("materialization_input_receipt")
                assert top == candidate
                assert snapshot.snapshot_json["bagging_result"]["materialization_candidate"] == candidate
                assert "materialization_input_receipt" not in candidate
                assert set(receipt) == {"workflow_revision", "evidence_run_id", "draft_id", "selected_pdf_receipt"}
                assert receipt["draft_id"] == fixture.draft_id
                assert receipt["evidence_run_id"] == fixture.evidence_id
                assert type(receipt["workflow_revision"]) is int
                assert isinstance(receipt["selected_pdf_receipt"], dict) and receipt["selected_pdf_receipt"]
                assert snapshot.snapshot_digest == payload_digest(snapshot.snapshot_json)
        with read_only(fixture, monkeypatch):
            assert candidate_read(fixture) == candidate
            results = {name: read() for name, read in public_readers(candidate)}
            if phase == "bagged":
                assert results["totals"] == []
                assert results["daily"]["groups"] == []
                assert results["daily"]["order_count"] == 0
                assert results["detail"]["rows"] == []
            else:
                expected = [("選択献立0", 7)]
                if not candidate["configured_input"]["policy"].get("quantity_rules", {}).get("zero_as_empty", True):
                    expected.append(("選択献立3", 0))
                assert sorted((row["menu_name"], row["quantity"]) for row in results["totals"]) == expected
                for row in results["totals"]:
                    assert len(row["order_refs"]) == 1
                    ref = row["order_refs"][0]
                    assert (ref["order_id"], ref["facility_id"], ref["facility_name"], ref["quantity"]) == (
                        fixture.order_id, "F", "保存済み施設", row["quantity"],
                    )
                assert results["daily"]["order_count"] == 1
                groups = results["daily"]["groups"]
                assert groups
                assert sum(diet["total_quantity"] for group in groups for diet in group["diet_groups"]) == 7
                refs = [ref for group in groups for diet in group["diet_groups"]
                        for bag in diet["bag_type_groups"] for split in bag["breakdowns"] for ref in split["order_refs"]]
                assert refs
                assert all((ref["order_id"], ref["facility_label"]) == (fixture.order_id, "保存済み施設 (F)") for ref in refs)
                assert sum(ref["quantity"] for ref in refs) == 7
                rows = results["detail"]["rows"]
                assert len(rows) == 1
                assert (rows[0]["facility_id"], rows[0]["facility_label"], rows[0]["order_count"], rows[0]["total_quantity"]) == (
                    "F", "保存済み施設 (F)", 1, 7,
                )
                with fixture.session_scope() as session:
                    lines = session.execute(select(OrderLine).where(OrderLine.order_id == fixture.order_id).order_by(OrderLine.menu_name)).scalars().all()
                    assert [(line.menu_name, line.quantity_original, line.quantity_corrected) for line in lines] == [
                        ("選択献立0", 7, None), ("選択献立3", 0, None),
                    ]
            assert fixture.saved() == saved
    finally:
        fixture.master_path.write_bytes(original)
    assert fixture.master_path.read_bytes() == original


@pytest.mark.parametrize("fault", ["same-id-draft-quantity", "evidence-template", "artifact-source"])
def test_direct_database_sheet_or_source_drift_is_rejected(accepted_receipt, fault, monkeypatch):
    fixture, candidate, phase = accepted_receipt
    pristine = database_snapshot()
    saved = fixture.saved()
    with fixture.session_scope() as session:
        if fault == "same-id-draft-quantity":
            draft = session.get(OrderSheetDraft, fixture.draft_id)
            payload = deepcopy(draft.draft_sheet_json)
            payload["rows"][0][4] = "9"
            draft.draft_sheet_json = payload
        elif fault == "evidence-template":
            session.get(OrderOcrEvidenceRun, fixture.evidence_id).template_version_id = None
        else:
            state = session.get(OrderWorkflowState, fixture.order_id)
            artifact = session.get(OrderBaggingResult, state.secondary_actions_json["workflow_v2"]["bagging_result_id"])
            payload = deepcopy(artifact.payload_json)
            payload["source_ocr_result_id"] = None
            artifact.source_ocr_result_id = None
            artifact.payload_json = payload
            artifact.payload_digest = payload_digest(payload)
    assert database_snapshot() != pristine
    if fault == "same-id-draft-quantity":
        expected_saved = deepcopy(saved)
        expected_saved["rows"][0][4] = "9"
        assert fixture.saved() == expected_saved
        error = "output original bagging input mismatch" if phase == "bagged" else "output original confirmed sheet mismatch"
    else:
        assert fixture.saved() == saved
        error = "output original saved sheet identity mismatch"
    assert_rejected(fixture, candidate, phase, error, monkeypatch)


@pytest.mark.parametrize("fault,error", [
    ("revision", "output original bagging proof missing"),
    ("start-sheet", "output original bagging input mismatch"),
    ("proof-digest", "output original bagging proof mismatch"),
    ("candidate-digest", "saved-sheet acceptance receipt mismatch"),
])
def test_bagging_receipt_drift_stops_without_recapture(owned_bagging, fault, error, monkeypatch):
    fixture = owned_bagging
    candidate = actual_bagging(fixture)["bagging_result"]["materialization_candidate"]
    pristine = database_snapshot()
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        meta = deepcopy(state.secondary_actions_json)
        proof = meta["workflow_v2"]["bagging_input_proof"]
        if fault == "revision":
            proof["owned_revision"] -= 1
        elif fault == "start-sheet":
            proof["start_input"]["input_versions"]["draft"]["draft_sheet_json"]["rows"][0][4] = "9"
        elif fault == "proof-digest":
            proof["materialization_digest"] = "0" * 64
        else:
            artifact = session.get(OrderBaggingResult, meta["workflow_v2"]["bagging_result_id"])
            payload = deepcopy(artifact.payload_json)
            payload["materialization_candidate"]["lines"][0]["quantity_original"] = 9
            artifact.payload_json = payload
            artifact.payload_digest = payload_digest(payload)
        state.secondary_actions_json = meta
    assert database_snapshot() != pristine
    assert_rejected(fixture, candidate, "bagged", error, monkeypatch)
    assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]


@pytest.mark.parametrize("fault,error", [
    ("missing-pointer", "output original confirmed receipt missing"),
    ("snapshot-digest", "output original confirmed receipt mismatch"),
    ("snapshot-sheet", "output original confirmed sheet mismatch"),
    ("artifact-lineage", "output original confirmed artifact mismatch"),
    ("order-line", "output original confirmed lines mismatch"),
])
def test_confirmed_receipt_drift_never_falls_back_to_bagging(confirmed_receipt, fault, error, monkeypatch):
    fixture, candidate = confirmed_receipt
    pristine = database_snapshot()
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        snapshot = session.get(OrderConfirmedSnapshot, state.confirmed_snapshot_id)
        if fault == "missing-pointer":
            state.confirmed_snapshot_id = None
        elif fault == "snapshot-digest":
            snapshot.snapshot_digest = "0" * 64
        elif fault == "snapshot-sheet":
            payload = deepcopy(snapshot.snapshot_json)
            payload["saved_sheet"]["rows"][0][4] = "9"
            snapshot.snapshot_json = payload
            snapshot.snapshot_digest = payload_digest(payload)
        elif fault == "artifact-lineage":
            artifact = session.get(OrderOutputBundle, snapshot.output_bundle_id)
            payload = deepcopy(artifact.payload_json)
            payload["confirmed_snapshot_id"] = "OTHER-CONFIRMED-SNAPSHOT"
            artifact.payload_json = payload
            artifact.payload_digest = payload_digest(payload)
        else:
            line = session.execute(select(OrderLine).where(
                OrderLine.order_id == fixture.order_id, OrderLine.quantity_original == 7,
            )).scalar_one()
            line.quantity_original = 9
    assert database_snapshot() != pristine
    assert_rejected(fixture, candidate, "confirmed", error, monkeypatch)
    assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]
