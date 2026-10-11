"""Canonical producer/consumer component contracts; not operator-flow proof."""
from copy import deepcopy
from types import SimpleNamespace
import json
import hashlib
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import update

from tests.unit.test_canonical_facility_lineage import Session, selected, authored
from src.services import facility_template_version_service as templates
from src.services import order_workflow_v2_service as workflow


@pytest.fixture(autouse=True)
def canonical_authored_source(monkeypatch, tmp_path):
    path = tmp_path / "canonical-workflow-facilities.json"
    path.write_text(json.dumps({"facilities": [{"facility_id": "F", "facility_name": "Reference",
                                             "versions": [authored()]}]}), encoding="utf-8")
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_candidate_nonfinite_number_stops(value):
    with pytest.raises(ValueError, match="Out of range float values"):
        workflow._canonical_payload_digest({"nested": [{"quantity": value}]})


def test_json_compatible_digest_matches_original_serialization():
    payload = {"nested": [{"unicode": "施設・ソース", "raw": "0003 / null / false",
                           "zero": 0, "null": None, "false": False,
                           "floats": [0.0, -0.0, 1.25, -3.5, 1e20]}]}
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    assert workflow._canonical_payload_digest(payload) == expected


@pytest.mark.parametrize("boundary", ["context", "existing"])
def test_missing_selected_id_stops_without_source_selection(monkeypatch, boundary):
    session = Session()
    order = SimpleNamespace(id="own-order", facility_code="F", received_at=None,
                            template_version_id=None, week_code="2026-01@2026-01-15~2026-01-15")
    state = SimpleNamespace(template_version_id=None)
    meta = {"facility_id": "F", "week_start": "2026-01-15", "week_end": "2026-01-15",
            "effective_facility_config_date": "2026-01-15", "template_version_id": ""}
    def forbidden(*args, **kwargs):
        pytest.fail("Missing original ID must not select or materialize a replacement")
    monkeypatch.setattr(templates, "resolve_single_active_template_version", forbidden)
    monkeypatch.setattr(templates, "materialize_facility_version_lineage", forbidden)
    if boundary == "context":
        # Confirmed context without a selected ID is still an explicit stop.
        actual = workflow._require_workflow_template_version_from_context(session, order=order, workflow=state, meta=meta)
    else:
        actual = workflow._require_existing_workflow_template_version(session, order=order, workflow=state, meta=meta)
    assert actual == (None, "template_version_required")
    assert session.statements == [] and session.rows == {}


def test_unexpected_projection_database_error_propagates():
    class FailedSession(Session):
        def execute(self, statement):
            raise RuntimeError("physical database read failed")
    with pytest.raises(RuntimeError, match="physical database read failed"):
        workflow._canonical_workflow_template_version(FailedSession(), "explicit-original", "F")


@pytest.mark.parametrize("day,allowed", [("2026-01-01", True), ("2026-01-31", True),
                                        ("2025-12-31", False), ("2026-02-01", False)])
def test_existing_context_date_applicability(monkeypatch, day, allowed):
    selection, _ = selected(monkeypatch)
    session = Session()
    _, projection = templates.materialize_facility_version_lineage(session, selection)
    order = SimpleNamespace(facility_code="F", received_at=None,
                            template_version_id=projection.id, week_code=f"{day[:7]}@{day}~{day}")
    state = SimpleNamespace(template_version_id=projection.id)
    meta = {"facility_id": "F", "week_start": day, "week_end": day}
    actual = workflow._require_workflow_template_version_from_context(session, order=order, workflow=state, meta=meta)
    assert actual == ((projection.id, None) if allowed else (None, "template_version_mismatch"))


@pytest.mark.parametrize("value", [date(2025, 1, 6), datetime(2025, 2, 18, 12, 0), datetime(2025, 2, 18, 12, 0, tzinfo=timezone.utc)])
def test_candidate_date_digest_persistence_parity(value):
    original = {"lines": [{"date": value, "quantity_original": 0, "quantity_corrected": None}],
                "unknown": {"false": False, "raw": "0003"}}
    persisted = json.loads(workflow._canonical_payload_json(original))
    assert persisted["lines"][0]["date"] == value.isoformat()
    assert persisted["lines"][0]["quantity_original"] == 0
    assert persisted["lines"][0]["quantity_corrected"] is None
    assert persisted["unknown"] == original["unknown"]
    assert workflow._canonical_payload_digest(original) == workflow._canonical_payload_digest(persisted)
    assert original["lines"][0]["date"] is value


@pytest.mark.parametrize("value", [object(), {1, 2}, b"raw"])
def test_candidate_unknown_type_stops(value):
    with pytest.raises(TypeError, match="Unsupported canonical payload type"):
        workflow._canonical_payload_digest({"lines": [{"unknown": value}]})


@pytest.mark.parametrize("boundary", ["context", "existing", "lineage"])
@pytest.mark.parametrize("change", [None, "missing", "active", "foreign", "columns", "proof", "digest"])
def test_canonical_workflow_boundaries(monkeypatch, boundary, change):
    selection, _ = selected(monkeypatch)
    session = Session()
    _, projection = templates.materialize_facility_version_lineage(session, selection)
    physical = session.rows[projection.id]
    if change == "missing":
        del session.rows[projection.id]
    elif change == "active":
        physical["status"] = "active"
    elif change == "foreign":
        physical["facility_id"] = "other"
    elif change == "columns":
        physical["columns_json"] = []
    elif change == "proof":
        physical["validation_json"].pop("creation_source_proof")
    elif change == "digest":
        physical["template_digest"] = "tampered"
    # A cached old ORM value must not override the physical producer row.
    session.cached[projection.id] = deepcopy(projection)
    order = SimpleNamespace(id="own-order", facility_code="F", received_at=None,
                            template_version_id=projection.id, week_code="2026-01@2026-01-12~2026-01-18")
    state = SimpleNamespace(template_version_id=projection.id, state="template_version_required")
    meta = {"facility_id": "F", "facility_config_date": "2026-01-15",
            "week_start": "2026-01-12", "week_end": "2026-01-18"}
    if boundary == "context":
        actual = workflow._require_workflow_template_version_from_context(session, order=order, workflow=state, meta=meta)
        assert actual == ((projection.id, None) if change is None else (None, "template_version_mismatch"))
    elif boundary == "existing":
        actual = workflow._require_existing_workflow_template_version(session, order=order, workflow=state, meta=meta)
        assert actual == ((projection.id, None) if change is None else (None, "template_version_mismatch"))
    else:
        # Exercise lineage's template boundary without fabricating OCR evidence.
        state.state = "sheet_saved"
        state.evidence_run_id = None
        error = workflow._workflow_lineage_error(session, order=order, workflow=state, meta_override=meta)
        assert error == ("selected_ocr_required" if change is None else "template_version_mismatch")


def test_physical_canonical_projection_ignores_stale_identity_map(isolated_formal_schema, monkeypatch, tmp_path):
    from datetime import date
    from src.db import session_scope
    from src.models.facility_template_version import FacilityTemplateVersion
    from src.services import canonical_facility_lineage as lineage, facility_service
    from tests.helpers.ingest_original import authored_sources

    authored_sources(monkeypatch, tmp_path, facility_id="own-bagging", menu_date=date(2026, 10, 7))
    with session_scope() as session:
        facility_service.ensure_facility_materialized(session, "own-bagging")
        selection, _ = lineage.resolve(session, "own-bagging", date(2026, 10, 7))
        _, projection = lineage.materialize(session, selection)
        projection_id = projection.id
    with session_scope() as session:
        cached = session.get(FacilityTemplateVersion, projection_id)
        assert cached.status == "archived"
        actual, error = workflow._canonical_workflow_template_version(session, projection_id, "own-bagging")
        assert error is None and actual.id == projection_id
        session.execute(update(FacilityTemplateVersion.__table__).where(
            FacilityTemplateVersion.id == projection_id).values(status="active"))
        assert cached.status == "archived"
        assert workflow._canonical_workflow_template_version(session, projection_id, "own-bagging") == (None, "template_version_mismatch")
