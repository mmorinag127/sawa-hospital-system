"""JSON read selection and explicit immutable FK materialization are separate operations."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace

from sqlalchemy import select, insert
from sqlalchemy.exc import IntegrityError
from src.models.facility import Facility
from src.models.facility_template_version import FacilityTemplateVersion
from src.services.canonical_facility_store import CanonicalStoreError, canonical_date

SOURCE = "canonical-json-lineage-v2"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def identity(receipt):
    return {key: receipt.get(key) for key in ("facility_id", "authored_version_id", "snapshot_digest")}


def cells_presence(template):
    return "absent" if "cells" not in template else "null" if template["cells"] is None else "present"


def verify_projection(row, receipt=None):
    from src.services.workflow_attempt_guard_service import snapshots_equal

    try:
        recorded = row.validation_json.get("canonical_lineage") if isinstance(row.validation_json, dict) else None
        if row.source != SOURCE or not isinstance(recorded, dict):
            raise CanonicalStoreError("canonical_lineage_provenance_missing", status_code=409)
        if row.status != "archived":
            raise CanonicalStoreError("canonical_lineage_projection_status_mismatch", status_code=409)
        if receipt is not None and not snapshots_equal(recorded, receipt):
            raise CanonicalStoreError("canonical_lineage_receipt_mismatch", status_code=409)
        authored = recorded.get("authored_version")
        payload = {"version_id": row.version,
                   "valid_from": row.valid_from.isoformat() if row.valid_from is not None else None,
                   "valid_to": row.valid_to.isoformat() if row.valid_to is not None else None,
                   "facility": row.config_json}
        if not isinstance(authored, dict) or not snapshots_equal(payload, authored):
            raise CanonicalStoreError("canonical_lineage_snapshot_mismatch", status_code=409)
        semantic = identity(recorded)
        if not all(isinstance(value, str) and value for value in semantic.values()) or \
                semantic["facility_id"] != row.facility_id or semantic["authored_version_id"] != row.version or \
                digest(authored) != semantic["snapshot_digest"] or row.id != "CFV" + digest(semantic) or \
                row.template_digest != digest(semantic):
            raise CanonicalStoreError("canonical_lineage_digest_mismatch", status_code=409)
        template = row.config_json.get("fax_template")
        if not isinstance(template, dict) or not snapshots_equal(row.columns_json, template.get("columns")) or \
                row.template_id != template.get("template_id") or not snapshots_equal(row.cells_json, template.get("cells")) or \
                row.validation_json.get("cells_presence") != cells_presence(template) or \
                row.validation_json.get("authored_template_digest") != digest(template):
            raise CanonicalStoreError("canonical_lineage_template_mismatch", status_code=409)
        if getattr(row, "materialized", True):
            validate_source_proof(row.validation_json.get("creation_source_proof"), authored)
        return deepcopy(recorded)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise CanonicalStoreError("canonical_lineage_projection_invalid", status_code=409) from exc


def validate_source_proof(proof, authored):
    if not isinstance(proof, dict) or proof.get("source") != "canonical_json" or \
            not isinstance(proof.get("revision"), str) or not proof["revision"]:
        raise CanonicalStoreError("canonical_lineage_selection_proof_missing", status_code=409)
    day = canonical_date(proof.get("effective_date"), "effective_date")
    start = canonical_date(authored["valid_from"], "valid_from")
    end = None if authored["valid_to"] is None else canonical_date(authored["valid_to"], "valid_to")
    if day < start or (end is not None and day > end):
        raise CanonicalStoreError("canonical_lineage_selection_date_mismatch", status_code=409)


def projection_values(receipt):
    authored = receipt["authored_version"]
    facility = authored["facility"]
    template = facility["fax_template"]
    semantic = identity(receipt)
    return dict(id="CFV" + digest(semantic), facility_id=receipt["facility_id"],
                version=receipt["authored_version_id"], status="archived", source=SOURCE,
                template_id=template.get("template_id"), config_json=deepcopy(facility),
                columns_json=deepcopy(template["columns"]), cells_json=deepcopy(template.get("cells")),
                template_digest=digest(semantic),
                validation_json={"canonical_lineage": deepcopy(receipt),
                                 "cells_presence": cells_presence(template),
                                 "authored_template_digest": digest(template)},
                valid_from=canonical_date(authored["valid_from"], "valid_from"),
                valid_to=None if authored["valid_to"] is None else canonical_date(authored["valid_to"], "valid_to"),
                created_by=SOURCE, created_at=None, activated_at=None, archived_at=None)


def resolve(session, facility_id, effective_date):
    """Read-only selection. Descriptor PK is NOT proof that its FK row exists."""
    from src.services import facility_master_service
    from src.services.facility_template_version_service import serialize_template_version

    selected = facility_master_service.resolve_facility_for_date(facility_id, effective_date)
    authored = selected["version"]
    receipt = {"facility_id": facility_id, "authored_version_id": authored["version_id"],
               "snapshot_digest": digest(authored), "source": "canonical_json",
               "authored_version": deepcopy(authored)}
    row = SimpleNamespace(**projection_values(receipt), materialized=False)
    verify_projection(row, receipt)
    result = deepcopy(selected["facility"])
    metadata = {"canonical_lineage": {**receipt, "projection_id": row.id, "materialized": False},
                "canonical_source_proof": {"revision": selected["revision"], "source": selected["source"],
                                           "effective_date": selected["effective_date"]},
                "facility_template_version_id": row.id,
                "facility_template_version": serialize_template_version(row),
                "effective_date": selected["effective_date"]}
    for key, value in metadata.items():
        if key in result:
            raise CanonicalStoreError("canonical_lineage_metadata_collision", status_code=409, field=key)
        result[key] = value
    return result, row


def read_projection(session, projection_id, *, lock=False):
    """Core columns bypass ORM identity cache and do not autoflush or discard dirty objects."""
    table = FacilityTemplateVersion.__table__
    statement = select(*table.c).where(table.c.id == projection_id)
    if lock:
        statement = statement.with_for_update()
    with session.no_autoflush:
        actual = session.execute(statement).mappings().one_or_none()
    return SimpleNamespace(**dict(actual), materialized=True) if actual is not None else None


def materialize(session, selection):
    """Explicit caller-owned write using an already selected own JSON receipt, never a reread."""
    from src.services.workflow_attempt_guard_service import snapshots_equal

    if session.new or session.dirty or session.deleted:
        raise CanonicalStoreError("canonical_lineage_pending_session_changes", status_code=409)
    if not isinstance(selection, dict) or not isinstance(selection.get("canonical_lineage"), dict):
        raise CanonicalStoreError("canonical_lineage_provenance_missing", status_code=409)
    lineage = selection["canonical_lineage"]
    authored = lineage.get("authored_version")
    if not isinstance(authored, dict) or lineage.get("source") != "canonical_json" or \
            not isinstance(lineage.get("facility_id"), str):
        raise CanonicalStoreError("canonical_lineage_provenance_missing", status_code=409)
    from src.services.canonical_facility_store import facility_versions
    facility_versions({"facilities": [{"facility_id": lineage["facility_id"], "versions": [authored]}]},
                      lineage["facility_id"])
    receipt = {key: deepcopy(value) for key, value in lineage.items()
               if key not in ("projection_id", "materialized")}
    candidate = SimpleNamespace(**projection_values(receipt), materialized=False)
    verify_projection(candidate, receipt)
    validate_source_proof(selection.get("canonical_source_proof"), receipt["authored_version"])
    if lineage.get("projection_id") != candidate.id:
        raise CanonicalStoreError("canonical_lineage_selection_proof_missing", status_code=409)
    expected_facility = receipt["authored_version"]["facility"]
    metadata_keys = {"canonical_lineage", "canonical_source_proof", "facility_template_version_id",
                     "facility_template_version", "effective_date"}
    if not snapshots_equal({key: value for key, value in selection.items() if key not in metadata_keys}, expected_facility) or \
            not snapshots_equal(selection.get("effective_date"), selection["canonical_source_proof"]["effective_date"]) or \
            selection.get("facility_template_version_id") != candidate.id:
        raise CanonicalStoreError("canonical_lineage_selection_changed", status_code=409)
    parent = Facility.__table__
    # Serialize creation of missing mirrors without an active/latest query or ID-map refresh.
    with session.no_autoflush:
        exists = session.execute(select(parent.c.id).where(parent.c.id == receipt["facility_id"])
                                 .with_for_update()).scalar_one_or_none()
    if exists is None:
        raise CanonicalStoreError("canonical_lineage_parent_projection_missing", status_code=409)
    row = read_projection(session, candidate.id, lock=True)
    if row is None:
        values = projection_values(receipt)
        values["created_at"] = datetime.utcnow()
        values["validation_json"]["creation_source_proof"] = deepcopy(selection["canonical_source_proof"])
        table = FacilityTemplateVersion.__table__
        try:
            with session.no_autoflush:
                own = session.execute(insert(table).values(**values).returning(*table.c)).mappings().one()
            row = SimpleNamespace(**dict(own), materialized=True)
        except IntegrityError as exc:
            raise CanonicalStoreError("canonical_lineage_projection_conflict", status_code=409) from exc
    verify_projection(row, receipt)
    from src.services.facility_template_version_service import serialize_template_version
    result = deepcopy(selection)
    # Current read proof is retained; original DB creation proof remains separate and immutable.
    result["canonical_lineage"]["materialized"] = True
    result["facility_template_version"] = serialize_template_version(row)
    return result, row
