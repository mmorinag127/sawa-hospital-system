from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import json
from hashlib import sha256

import numpy as np
import pytest

from src.services import order_service, order_workflow_v2_service as workflow
from src.services import hakodate_fixed_quad_registration_service as raster
from src.services import storage_service
from src.services import config_service, workflow_attempt_guard_service as attempt_guard
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.services import canonical_facility_lineage as lineage
from tests.unit.test_canonical_facility_lineage import Session as ProjectionSession


@pytest.fixture
def geometry_source_events():
    return {}


@pytest.fixture
def geometry_roundtrip(monkeypatch, tmp_path, geometry_source_events):
    # External storage/render/DB adapters are doubles; receipt/hash/save producers are real.
    pdf_bytes = b"recorded-unit-test-pdf-source"
    pdf = {"uploaded_pdf_id": "pdf-record", "document_id": "document-A", "order_id": "order-A",
           "message_id": "message-A", "storage_uri": "fixture://document-A", "generation": None,
           "sha256": sha256(pdf_bytes).hexdigest()}
    # Quantity diet/area match tests.helpers.ingest_original.authored_sources.
    configured = {"facility": {"facility_id": "facility-A", "facility_name": "Synthetic geometry facility", "unknown": None,
        "areas": [{"area_id": "2F", "name": "Synthetic component second floor"}],
        "fax_template": {"template_id": "authored-geometry", "page": 1,
        "columns": [{"index": 0, "source_index": 0, "role": "quantity", "diet_type": "regular", "area_id": "2F"}]}}}
    master_path = tmp_path / "geometry-facilities.json"
    policy_path = tmp_path / "geometry-policy.json"
    policy_path.write_text(json.dumps({"ocr_retry_limit": 1, "quantity_rules": {},
                                     "backlog_policy": {"skip_ocr_for_stale": False}}))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(master_path))
    monkeypatch.setattr(config_service, "INGEST_POLICY_PATH", policy_path)
    entries = [{"name": "Authored menu", "menu_name": "Authored menu", "menu_date": "2026-10-04",
                "daypart": "breakfast", "daypart_key": "breakfast", "slot_index": 0,
                "category": None, "order": 0, "quantity": 0}]
    state = OrderWorkflowState(order_id="order-A", revision=7, state="context_confirmed", secondary_actions_json={},
                            template_version_id=None, evidence_run_id=None, draft_id=None, confirmed_snapshot_id=None,
                            headline=None, primary_action=None, blockers_json=[], warnings_json=[], last_transition_at=datetime(2026, 10, 4))
    order = Order(id="order-A", current_document_id="document-A", document_uri=pdf["storage_uri"], facility_code="facility-A", week_code="2026-10@2026-10-04~2026-10-04",
                  message_id="message-A", status="要確認", received_at=datetime(2026, 10, 4))

    document = OrderDocument(id="document-A", storage_uri=pdf["storage_uri"], source_email_id="message-A")
    uploaded = UploadedPdf(id="pdf-record", message_id="message-A", storage_uri=pdf["storage_uri"], content_sha256=pdf["sha256"])

    class Session(ProjectionSession):
        info = {}

        def flush(self):
            pass

        def get(self, model, identity, **kwargs):
            if model is OrderWorkflowState:
                assert identity == order.id
                return state
            if model is Order:
                assert identity == order.id
                return order
            if model is OrderDocument:
                return document if identity == document.id else None
            if model is UploadedPdf:
                return uploaded if identity == uploaded.id else None
            return None

        def execute(self, statement):
            if statement.is_insert or next(iter(statement.selected_columns)).table.name in {"facility_template_versions", "facilities"}:
                return super().execute(statement)
            class EmptyRows:
                def scalars(self):
                    return self

                def all(self):
                    return [uploaded] if next(iter(statement.selected_columns)).table.name == "uploaded_pdfs" else []
            return EmptyRows()

    session = Session()
    session.parent = "facility-A"

    def publish_configuration():
        authored = {"version_id": "authored-geometry-original", "valid_from": "2026-10-04", "valid_to": None,
                    "facility": deepcopy(configured["facility"])}
        master_path.write_text(json.dumps({"facilities": [{"facility_id": "facility-A", "versions": [authored]}]}))
        selection, _ = lineage.resolve(session, "facility-A", date(2026, 10, 4))
        _, projection = lineage.materialize(session, selection)
        actual = config_service.capture_authored_processing_config("facility-A", selected_authored_version=authored)
        configured.clear()
        configured.update(actual)
        return projection.id

    order.template_version_id = state.template_version_id = publish_configuration()
    geometry_source_events["publish"] = publish_configuration

    @contextmanager
    def session_scope():
        yield session

    def selection(_session, _order, document_id):
        assert document_id == pdf["document_id"]
        return pdf["storage_uri"], {"selected_version_input": None}

    monkeypatch.setattr(workflow, "session_scope", session_scope)
    monkeypatch.setattr(workflow, "_get_order_or_error", lambda *_: (order, None))
    monkeypatch.setattr(workflow, "_get_workflow_readonly", lambda *_: state)
    monkeypatch.setattr(workflow, "_get_workflow", lambda *_: state)
    monkeypatch.setattr(workflow, "_get_or_create_workflow", lambda *_: state)
    monkeypatch.setattr(order_service, "_resolve_order_document_selection", selection)
    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *_args, **_kwargs: deepcopy(entries))
    monkeypatch.setattr(storage_service, "load_bytes_from_uri", lambda *_args, **_kwargs: pdf_bytes)
    def verify_storage_source(receipt):
        assert receipt["storage_uri"] == pdf["storage_uri"]
        if receipt["sha256"] != sha256(pdf_bytes).hexdigest():
            raise ValueError("selected_pdf_bytes_changed")
    monkeypatch.setattr(storage_service, "verify_selected_pdf_source", verify_storage_source)
    monkeypatch.setattr(storage_service, "load_selected_pdf_bytes", lambda uri, receipt: pdf_bytes)
    quad = [[0, 0], [10, 0], [10, 10], [0, 10]]
    monkeypatch.setattr(raster, "estimate_edge_locked_quad_from_pdf", lambda *_args, **_kwargs: {"refined_quad_px": quad})
    monkeypatch.setattr(raster, "render_pdf_page_to_bgr", lambda *_args, **_kwargs: np.zeros((10, 10, 3), dtype=np.uint8))
    return state, order, pdf, configured, entries, quad


def test_real_get_put_get_retains_geometry_proof_while_mutation_digest_changes(geometry_roundtrip):
    state, order, pdf, configured, _, quad = geometry_roundtrip
    before, error = workflow.get_quad_review(order.id, document_id=pdf["document_id"])
    assert error is None
    receipt = deepcopy(before["source_receipt"])
    saved, error = workflow.save_quad_review_decision(order.id, decision="manual_override", quad_px=quad,
                                                     source_receipt=receipt, expected_revision=receipt["workflow_revision"])
    assert error is None
    assert saved["order_id"] == order.id
    assert saved["revision"] == receipt["workflow_revision"] + 1
    ocr_points, ocr_source = order_service._normalize_operator_quad_override(
        workflow._workflow_meta(state)["quad_override"], selected_pdf_receipt=pdf, configured_input=configured,
        order_input_identity=receipt["order_input_identity"])
    assert ocr_source == workflow._workflow_meta(state)["quad_override"]["quad_source"]
    assert ocr_points == quad
    assert receipt["physical_menu_entries"][0]["slot_index"] == 0
    assert receipt["physical_menu_entries"][0]["quantity"] == 0
    after, error = workflow.get_quad_review(order.id, document_id=pdf["document_id"])
    assert error is None
    assert after["source_receipt"]["input_digest"] != receipt["input_digest"]
    assert after["source_receipt"]["geometry_source_digest"] == receipt["geometry_source_digest"]
    assert after["saved_override"]["source_receipt"] == receipt
    adopted, _ = order_service._normalize_operator_quad_override(after["saved_override"], selected_pdf_receipt=pdf,
                                                                 configured_input=configured,
                                                                 order_input_identity=receipt["order_input_identity"])
    assert adopted == quad
    assert state.revision == saved["revision"]


@pytest.mark.parametrize("rotation", [0, 180])
def test_upside_down_quad_persists_through_real_ocr_normalizer(geometry_roundtrip, rotation):
    state, order, pdf, configured, _, _ = geometry_roundtrip
    review, error = workflow.get_quad_review(order.id, document_id=pdf["document_id"])
    assert error is None
    receipt = review["source_receipt"]
    points = review["suggested_quad_px"]
    original = deepcopy(pdf)
    saved, error = workflow.save_quad_review_decision(
        order.id, decision="approved_estimate", quad_px=points,
        source_receipt=receipt, rotation_degrees=rotation,
        expected_revision=receipt["workflow_revision"])
    assert error is None
    after, error = workflow.get_quad_review(order.id, document_id=pdf["document_id"])
    assert error is None
    override = after["saved_override"]
    assert override["rotation_degrees"] == rotation
    actual, _ = order_service._normalize_operator_quad_override(
        override, selected_pdf_receipt=pdf, configured_input=configured,
        order_input_identity=receipt["order_input_identity"])
    assert actual == (points[2:] + points[:2] if rotation else points)
    assert pdf == original
    assert saved["revision"] == receipt["workflow_revision"] + 1
    assert state.revision == saved["revision"]


@pytest.mark.parametrize("rotation", [-180, 90, 360, True, "180"])
def test_quad_rotation_rejects_unsupported_values_without_write(geometry_roundtrip, rotation):
    state, order, _, _, _, _ = geometry_roundtrip
    before = state.revision
    _, error = workflow.save_quad_review_decision(
        order.id, decision="manual_override", quad_px=[[0, 0]] * 4,
        source_receipt={}, rotation_degrees=rotation, expected_revision=before)
    assert error == "quad_rotation_invalid"
    assert state.revision == before


def test_half_turn_rectification_moves_both_axes_not_mirror():
    image = np.zeros((12, 12, 3), dtype=np.uint8)
    image[:6, :6] = [10, 20, 30]
    image[:6, 6:] = [40, 50, 60]
    image[6:, 6:] = [70, 80, 90]
    image[6:, :6] = [100, 110, 120]
    original = image.copy()
    result = raster.rectify_fax_to_template_grid(
        image, quad_px=[[11, 11], [0, 11], [0, 0], [11, 0]],
        table_bbox=[0, 0, 11, 11], canvas_width=12, canvas_height=12)
    np.testing.assert_array_equal(result[2, 2], image[9, 9])
    np.testing.assert_array_equal(result[2, 9], image[9, 2])
    np.testing.assert_array_equal(result[9, 2], image[2, 9])
    np.testing.assert_array_equal(result[9, 9], image[2, 2])
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize("automatic", [False, True])
def test_step1_rotation_uses_real_rectification_without_quad_review(monkeypatch, automatic):
    image = np.zeros((80, 80, 3), dtype=np.uint8)
    image[:40, :40], image[:40, 40:] = [10, 20, 30], [40, 50, 60]
    image[40:, 40:], image[40:, :40] = [70, 80, 90], [100, 110, 120]
    original = image.copy()
    points = [[0, 0], [79, 0], [79, 79], [0, 79]]
    monkeypatch.setattr(raster, "template_axis_edge_counts_from_workbook", lambda *a, **k: (2, 2))
    monkeypatch.setattr(raster, "estimate_edge_locked_quad_from_pdf", lambda *a, **k: {
        "status": "ok", "refined_quad_px": points})
    monkeypatch.setattr(raster, "render_pdf_page_to_bgr", lambda *a, **k: image)
    monkeypatch.setattr(raster, "render_template_pdf_to_canvas", lambda *a, **k: image)
    _, images = raster.build_fixed_quad_template_registration(
        facility_code="fixture", order_id="fixture", fax_pdf="fixture", template_pdf="fixture",
        quad_px=None if automatic else points, manifest_template_bbox=[0, 0, 79, 79],
        canvas_width=80, canvas_height=80, render_width=80,
        template_axes_x=[0, 79], template_axes_y=[0, 79], rotation_degrees=180)
    # Real transform output overlays the border only; inspect interior quadrants.
    np.testing.assert_array_equal(images["step2"][20, 20], image[60, 60])
    np.testing.assert_array_equal(images["step2"][65, 65], image[15, 15])
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize("change", ["pdf", "config", "week", "menu"])
def test_real_geometry_producer_changes_for_actual_geometry_source_not_workflow_meta(geometry_roundtrip, change):
    _, order, pdf, configured, entries, _ = geometry_roundtrip
    identity = {"facility_code": order.facility_code, "week_code": order.week_code}
    before = order_service.geometry_source_digest(pdf, configured, identity)
    if change == "pdf":
        pdf["document_id"] = "document-B"
    elif change == "config":
        configured["facility"]["fax_template"]["columns"][0]["index"] = 1
    elif change == "week":
        identity["week_code"] = "2026-10-11"
    else:
        entries.append({"menu_date": "2026-10-04", "daypart_key": "breakfast", "slot_index": 1, "quantity": 0})
    assert order_service.geometry_source_digest(pdf, configured, identity) != before


@pytest.mark.parametrize("axis", ["header", "row"])
def test_real_axis_receipt_get_put_get_and_normalizer(geometry_roundtrip, monkeypatch, axis):
    from tests.helpers.authored_row_axis import review_geometry, operator_input

    state, order, pdf, configured, _, _ = geometry_roundtrip
    normalizer = getattr(order_service, f"_normalize_operator_{axis}_axis_override")
    key = "corrected_xs" if axis == "header" else "corrected_ys"

    def render_adapter(order_id, *, source_receipt, start_input):
        assert order_id == order.id
        assert start_input["configured_input"] == configured
        stored = workflow._workflow_meta(state).get(f"{axis}_axis_override")
        saved = normalizer(stored, selected_pdf_receipt=pdf, configured_input=configured,
                           order_input_identity=source_receipt["order_input_identity"]) if stored is not None else None
        return {"order_id": order_id, "status": "ok", "saved_override": saved,
                **(review_geometry(source_receipt, start_input) if axis == "row" else {})}

    # Only heavy axis render/manifest adapter is doubled; GET/PUT receipt producers remain real.
    monkeypatch.setattr(order_service, f"build_hakodate_{axis}_axis_review", render_adapter)
    read = getattr(workflow, f"get_{axis}_axis_review")
    save = getattr(workflow, f"save_{axis}_axis_review_decision")
    before, error = read(order.id, document_id=pdf["document_id"])
    assert error is None
    receipt = deepcopy(before["source_receipt"])
    values = operator_input(before) if axis == "row" else {
        key: [0, 5, 10], "coordinate_space": {"mode": "template_canvas", "width": 10, "height": 10},
    }
    saved, error = save(order.id, **values, source_receipt=receipt,
                        expected_revision=receipt["workflow_revision"])
    assert error is None
    assert saved["order_id"] == order.id
    assert saved["revision"] > receipt["workflow_revision"]
    ocr_override = normalizer(workflow._workflow_meta(state)[f"{axis}_axis_override"],
                              selected_pdf_receipt=pdf, configured_input=configured,
                              order_input_identity=receipt["order_input_identity"])
    assert ocr_override[key] == values[key]
    assert ocr_override["source_receipt"] == receipt
    after, error = read(order.id, document_id=pdf["document_id"])
    assert error is None
    assert after["source_receipt"]["input_digest"] != receipt["input_digest"]
    assert after["source_receipt"]["geometry_source_digest"] == receipt["geometry_source_digest"]
    assert after["saved_override"]["source_receipt"] == receipt
    assert after["saved_override"][key] == values[key]
    assert state.revision == saved["revision"]


@pytest.mark.parametrize("axis", ["quad", "header", "row"])
@pytest.mark.parametrize("page", [None, 0, -1, True, "1", 1.5])
def test_real_review_blocks_unauthored_or_invalid_page(geometry_roundtrip, geometry_source_events, axis, page):
    state, order, pdf, configured, _, _ = geometry_roundtrip
    template = configured["facility"]["fax_template"]
    if page is None:
        del template["page"]
    else:
        template["page"] = page
    # Explicitly publish/select this test's changed source, never repair during GET.
    order.template_version_id = state.template_version_id = geometry_source_events["publish"]()
    before = deepcopy(workflow._workflow_meta(state))
    review, error = getattr(workflow, f"get_{axis}_review" if axis == "quad" else f"get_{axis}_axis_review")(
        order.id, document_id=pdf["document_id"])
    assert review is None
    assert error == "geometry_template_page_required"
    assert state.revision == 7
    assert workflow._workflow_meta(state) == before


@pytest.mark.parametrize("axis", ["quad", "header", "row"])
@pytest.mark.parametrize("missing", ["empty", "null", "null_slot", "absent_slot"])
def test_real_review_blocks_unresolved_physical_source(geometry_roundtrip, monkeypatch, axis, missing):
    state, order, pdf, _, entries, _ = geometry_roundtrip
    if missing == "empty":
        entries.clear()
    elif missing == "null":
        monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *_args, **_kwargs: None)
    elif missing == "null_slot":
        entries[0]["slot_index"] = None
    else:
        del entries[0]["slot_index"]
    before = deepcopy(workflow._workflow_meta(state))
    read = getattr(workflow, "get_quad_review" if axis == "quad" else f"get_{axis}_axis_review")
    review, error = read(order.id, document_id=pdf["document_id"])
    assert review is None
    assert error == ("geometry_physical_menu_missing" if missing in ("empty", "null") else "physical_menu_slot_identity_required")
    assert state.revision == 7
    assert workflow._workflow_meta(state) == before


def test_fixture_uses_complete_actual_capture_schema(geometry_roundtrip):
    _, order, _, configured, entries, _ = geometry_roundtrip
    with workflow.session_scope() as session:
        captured = workflow.capture_reparse_read_input(session, order)
        projection = lineage.read_projection(session, order.template_version_id)
    assert set(captured) == {"order_exists", "configured_input", "physical_menu_entries", "order", "lines",
                             "workflow_exists", "workflow_revision", "workflow", "evidence", "draft", "cache", "document", "decisions",
                             "original_template_projection", "original_template_lineage", "original_template_source_proof"}
    assert captured["original_template_projection"]["id"] == order.template_version_id
    assert attempt_guard.snapshots_equal(captured["original_template_projection"], {
        key: getattr(projection, key) for key in captured["original_template_projection"]
    })
    assert captured["original_template_lineage"] == lineage.verify_projection(projection)
    assert captured["original_template_source_proof"] == captured["original_template_projection"]["validation_json"]["creation_source_proof"]
    assert captured["order_exists"] is True
    assert captured["workflow_exists"] is True
    assert captured["configured_input"] == configured
    assert "unknown" in captured["configured_input"]["facility"]
    assert captured["configured_input"]["facility"]["unknown"] is None
    assert captured["physical_menu_entries"] == entries
    assert captured["physical_menu_entries"][0]["slot_index"] == 0
    assert captured["physical_menu_entries"][0]["category"] is None
    assert captured["physical_menu_entries"][0]["quantity"] == 0
    assert set(captured["order"]) == {column.key for column in Order.__table__.columns}
    assert set(captured["workflow"]) == {column.key for column in OrderWorkflowState.__table__.columns}
