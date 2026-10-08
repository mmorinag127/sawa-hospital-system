"""Capture an explicit JSON selection once; replay only its recorded receipt."""

from copy import deepcopy
from types import SimpleNamespace

from src.services import canonical_facility_lineage as lineage
from src.services.canonical_facility_store import CanonicalStoreError, canonical_date, facility_versions
from src.services.ingest_policy import require_explicit_ingest_week
from src.services.sheet_week_service import parse_sheet_week_value
from src.services.workflow_attempt_guard_service import snapshots_equal


def capture_selection(facility_id, effective_date, expected_revision, selected_version_id, week_hint):
    if not all(isinstance(value, str) and value.strip() for value in
               (facility_id, effective_date, expected_revision, selected_version_id)):
        raise CanonicalStoreError("ingest_facility_selection_required", status_code=422)
    selection, _ = lineage.resolve(None, facility_id, effective_date)
    if (selection["canonical_source_proof"]["revision"] != expected_revision
            or selection["canonical_lineage"]["authored_version_id"] != selected_version_id):
        raise CanonicalStoreError("ingest_facility_selection_changed", status_code=409)
    return validate_selection(selection, facility_id, week_hint)


def verify_initial_selection(selection, facility_id, week_hint):
    incoming = validate_selection(selection, facility_id, week_hint)
    try:
        actual = capture_selection(
            facility_id, incoming["effective_date"],
            incoming["canonical_source_proof"]["revision"],
            incoming["canonical_lineage"]["authored_version_id"], week_hint,
        )
    except CanonicalStoreError as exc:
        raise ValueError("ingest_facility_selection_initial_source_refused") from exc
    if not snapshots_equal(actual, incoming):
        raise ValueError("ingest_facility_selection_initial_source_mismatch")
    return actual


def validate_selection(selection, facility_id, week_hint):
    try:
        if not isinstance(selection, dict) or not isinstance(selection.get("canonical_lineage"), dict):
            raise ValueError("ingest_facility_selection_missing")
        recorded = selection["canonical_lineage"]
        authored = recorded.get("authored_version")
        if recorded.get("facility_id") != facility_id or recorded.get("source") != "canonical_json":
            raise ValueError("ingest_facility_selection_identity_changed")
        facility_versions({"facilities": [{"facility_id": facility_id, "versions": [authored]}]}, facility_id)
        receipt = {key: deepcopy(value) for key, value in recorded.items()
                   if key not in {"projection_id", "materialized"}}
        candidate = SimpleNamespace(**lineage.projection_values(receipt), materialized=False)
        lineage.verify_projection(candidate, receipt)
        lineage.validate_source_proof(selection.get("canonical_source_proof"), authored)
        _, start, end = parse_sheet_week_value(require_explicit_ingest_week(week_hint))
        day = canonical_date(selection.get("effective_date"), "effective_date")
        if not start <= day <= end:
            raise ValueError("ingest_facility_selection_outside_order_period")
        metadata = {"canonical_lineage", "canonical_source_proof", "facility_template_version_id",
                    "facility_template_version", "effective_date"}
        if (recorded.get("projection_id") != candidate.id
                or selection.get("facility_template_version_id") != candidate.id
                or selection.get("effective_date") != selection["canonical_source_proof"]["effective_date"]
                or not snapshots_equal({key: value for key, value in selection.items() if key not in metadata},
                                       authored["facility"])):
            raise ValueError("ingest_facility_selection_changed")
        return deepcopy(selection)
    except CanonicalStoreError as exc:
        raise ValueError("ingest_facility_selection_invalid") from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("ingest_facility_selection_invalid") from exc


def require_order_selection(session, order, selection):
    selection = validate_selection(selection, order.facility_code, order.week_code)
    if order.template_version_id != selection["facility_template_version_id"]:
        raise ValueError("ingest_order_facility_selection_changed")
    projection = lineage.read_projection(session, order.template_version_id, lock=True)
    if projection is None:
        raise ValueError("ingest_order_facility_projection_missing")
    recorded = {key: value for key, value in selection["canonical_lineage"].items()
                if key not in {"projection_id", "materialized"}}
    try:
        lineage.verify_projection(projection, recorded)
    except CanonicalStoreError as exc:
        raise ValueError("ingest_order_facility_projection_changed") from exc


def materialize_selection(session, selection):
    try:
        return lineage.materialize(session, selection)[1].id
    except CanonicalStoreError as exc:
        raise ValueError("ingest_facility_materialization_refused") from exc
