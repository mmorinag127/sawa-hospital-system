"""Source contract cases for read selection and explicit immutable materialization."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from src.services import canonical_facility_store as store, facility_master_service
from src.services import facility_template_version_service as service
from src.services.canonical_facility_lineage import verify_projection, read_projection

def authored(identity="human-A", start="2026-01-01", end="2026-01-31"):
    return {"version_id": identity, "valid_from": start, "valid_to": end, "unknown_version": None,
            "facility": {"facility_id": "F", "facility_name": "Recorded", "areas": None,
                         "unknown": {"zero": 0, "null": None}, "quantity_setting": 0,
                         "fax_template": {"template_id": "human-template", "units": "normalized",
                             "table_box": [0, 0, 1, 1], "header_rows": 0,
                             "columns": [{"index": 0, "source_index": 0, "role": "quantity",
                                          "diet_type": "regular", "area_id": "X"}], "unknown": None}}}



class Result:
    def __init__(self, value):
        self.value = value
    def mappings(self):
        return self
    def one_or_none(self):
        return deepcopy(self.value)
    def one(self):
        assert self.value is not None
        return deepcopy(self.value)
    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self):
        self.rows = {}
        self.cached = {}
        self.new, self.dirty, self.deleted = set(), set(), set()
        self.statements = []
        self.parent = "F"
        self.conflict = False
    @property
    def no_autoflush(self):
        return nullcontext()
    def execute(self, statement):
        self.statements.append(statement)
        params = statement.compile().params
        if statement.is_insert:
            if self.conflict:
                raise IntegrityError("insert", {}, Exception("conflict"))
            assert not self.new and not self.dirty and not self.deleted
            self.rows[params["id"]] = deepcopy(params)
            return Result(params)
        assert statement.is_select
        table = next(iter(statement.selected_columns)).table.name
        if table == "facilities":
            assert statement._for_update_arg is not None
            return Result(self.parent)
        return Result(self.rows.get(params["id_1"]))
    def get(self, *_args):
        pytest.fail("Identity-map lookup is forbidden")
    def flush(self, *_args):
        pytest.fail("No autoflush of caller mutations")
    def commit(self):
        pytest.fail("Caller owns commit")
    def rollback(self):
        pytest.fail("Do not discard caller mutations")


def install(monkeypatch, records, revision="fixed-canonical-revision"):
    snapshot = store.MasterSnapshot({"fax_template_base": {"must_not_merge": True},
        "facilities": [{"facility_id": "F", "facility_name": "Reference", "versions": records}]}, revision, "test")
    reads = []
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: reads.append(snapshot) or snapshot)
    return snapshot, reads



@pytest.mark.parametrize("day", ["2026-01-01", "2026-01-31", date(2026, 1, 15)])
def test_read_selection_has_exact_snapshot_and_no_db_access(monkeypatch, day):
    record = authored()
    snapshot, reads = install(monkeypatch, [record])
    session = Session()
    result, row, error = service.resolve_facility_config_for_date(session, facility_id="F", effective_date=day)
    assert error is None and len(reads) == 1 and session.statements == []
    assert row.config_json == record["facility"] and row.version == record["version_id"]
    assert row.id != row.version and not row.materialized
    assert result["fax_template"] == record["facility"]["fax_template"]
    assert result["unknown"] == {"zero": 0, "null": None}
    assert result["areas"] is None and result["quantity_setting"] == 0
    assert result["canonical_source_proof"]["revision"] == snapshot.revision
    assert result["canonical_lineage"]["materialized"] is False
    assert result["facility_template_version"]["reference_status"] == "canonical_selection"
    assert "cells" not in result["facility_template_version"]


def selected(monkeypatch, revision="A", record=None):
    install(monkeypatch, [record or authored()], revision)
    value, row, error = service.resolve_facility_config_for_date(None, facility_id="F", effective_date="2026-01-15")
    assert error is None
    return value, row


def test_unrelated_root_revision_preserves_lineage_and_actual_read_proof(monkeypatch):
    first, a = selected(monkeypatch, "A")
    second, b = selected(monkeypatch, "B")
    assert a.id == b.id and a.template_digest == b.template_digest
    assert first["canonical_lineage"] == second["canonical_lineage"]
    assert first["canonical_source_proof"]["revision"] == "A"
    assert second["canonical_source_proof"]["revision"] == "B"
    session = Session()
    saved, original = service.materialize_facility_version_lineage(session, first)
    assert saved["canonical_lineage"]["materialized"]
    current, reused = service.materialize_facility_version_lineage(session, second)
    assert reused.id == original.id
    assert current["canonical_source_proof"]["revision"] == "B"
    assert reused.validation_json["creation_source_proof"]["revision"] == "A"
    assert sum(statement.is_insert for statement in session.statements) == 1


def test_changed_selected_content_has_new_semantic_identity(monkeypatch):
    _, old = selected(monkeypatch)
    changed = authored()
    changed["facility"]["facility_name"] = "Explicit changed name"
    _, new = selected(monkeypatch, "B", changed)
    assert new.id != old.id and new.template_digest != old.template_digest
    assert old.config_json["facility_name"] == "Recorded"


@pytest.mark.parametrize("presence,value", [("absent", None), ("null", None), ("present", [])])
def test_cells_absence_null_empty_are_exact_in_receipt_and_projection(monkeypatch, presence, value):
    record = authored()
    if presence != "absent":
        record["facility"]["fax_template"]["cells"] = value
    selection, descriptor = selected(monkeypatch, record=record)
    assert descriptor.validation_json["cells_presence"] == presence
    session = Session()
    saved, row = service.materialize_facility_version_lineage(session, selection)
    assert row.validation_json["cells_presence"] == presence
    assert row.config_json == record["facility"]
    assert saved["canonical_lineage"]["authored_version"] == record
    serialized = saved["facility_template_version"]
    if presence == "absent":
        assert "cells" not in serialized and "cells" not in row.config_json["fax_template"]
    else:
        assert serialized["cells"] == value and row.cells_json == value


def test_cells_states_have_distinct_content_lineage(monkeypatch):
    ids = []
    for value in ("absent", None, []):
        record = authored()
        if value != "absent":
            record["facility"]["fax_template"]["cells"] = value
        _, row = selected(monkeypatch, record=record)
        ids.append(row.id)
    assert len(set(ids)) == 3


@pytest.mark.parametrize("day", [None, "", "2026-02-30", "2026-01-01T00:00:00Z", "2025-12-31", "2026-02-01"])
def test_invalid_missing_unrecorded_date_stops_without_db(monkeypatch, day):
    install(monkeypatch, [authored()])
    session = Session()
    config, row, error = service.resolve_facility_config_for_date(session, facility_id="F", effective_date=day)
    assert config is None and row is None and error and session.statements == []


@pytest.mark.parametrize("records", [[], [authored(), authored("B", "2026-01-31", None)],
    [{**authored(), "facility": {"facility_id": "F", "facility_name": "Bad", "areas": None,
                                "fax_template": {"template_id": "X"}}}]])
def test_overlapping_missing_incomplete_source_stops(monkeypatch, records):
    install(monkeypatch, records)
    config, row, error = service.resolve_facility_config_for_date(None, facility_id="F", effective_date="2026-01-15")
    assert config is None and row is None and error


@pytest.mark.parametrize("field,value", [("template_digest", "wrong"), ("source", "legacy"),
    ("config_json", {"wrong": True}), ("columns_json", []), ("validation_json", {}), ("status", "active")])
def test_fresh_actual_projection_mismatch_beats_matching_identity_cache(monkeypatch, field, value):
    selection, _ = selected(monkeypatch)
    session = Session()
    _, good = service.materialize_facility_version_lineage(session, selection)
    session.cached[good.id] = deepcopy(good)
    session.rows[good.id][field] = value
    with pytest.raises(store.CanonicalStoreError, match="canonical_lineage_"):
        service.materialize_facility_version_lineage(session, selection)
    assert sum(statement.is_insert for statement in session.statements) == 1
    assert session.statements[-1]._for_update_arg is not None


@pytest.mark.parametrize("kind", ["new", "dirty", "deleted"])
def test_pending_mutations_never_autoflush_or_discard(monkeypatch, kind):
    selection, _ = selected(monkeypatch)
    session = Session()
    marker = object()
    getattr(session, kind).add(marker)
    with pytest.raises(store.CanonicalStoreError, match="pending_session_changes"):
        service.materialize_facility_version_lineage(session, selection)
    assert marker in getattr(session, kind) and session.statements == []
    # Read-only JSON selection still does not touch the Session.
    _, _, error = service.resolve_facility_config_for_date(session, facility_id="F", effective_date="2026-01-15")
    assert error is None and marker in getattr(session, kind) and session.statements == []


def test_core_read_is_fresh_without_mutating_dirty_identity_object(monkeypatch):
    selection, _ = selected(monkeypatch)
    session = Session()
    _, row = service.materialize_facility_version_lineage(session, selection)
    cached = deepcopy(row)
    cached.config_json["facility_name"] = "Pending working value"
    session.cached[row.id] = cached
    session.dirty.add(object())
    actual = read_projection(session, row.id)
    verify_projection(actual)
    assert actual.config_json["facility_name"] == "Recorded"
    assert cached.config_json["facility_name"] == "Pending working value"
    assert session.dirty


def test_conflict_no_retry_and_parent_missing_stop(monkeypatch):
    selection, _ = selected(monkeypatch)
    session = Session()
    session.parent = None
    with pytest.raises(store.CanonicalStoreError, match="parent_projection_missing"):
        service.materialize_facility_version_lineage(session, selection)
    assert not session.rows
    session.parent, session.conflict = "F", True
    with pytest.raises(store.CanonicalStoreError, match="projection_conflict"):
        service.materialize_facility_version_lineage(session, selection)
    assert sum(statement.is_insert for statement in session.statements) == 1


def test_legacy_unknown_and_lazy_backfill_remain_blocked():
    session = Session()
    row = SimpleNamespace(source="legacy", validation_json={})
    assert not service.template_version_applies_on(row, "2026-01-15")
    with pytest.raises(store.CanonicalStoreError, match="provenance_missing"):
        verify_projection(row)
    for action in (lambda: service.ensure_active_template_version_from_resolved_config(session, facility_id="F"),
                   lambda: service.backfill_facility_template_version_lineage(session)):
        with pytest.raises(store.CanonicalStoreError, match="disabled"):
            action()
    assert session.statements == []


def test_authored_id_remains_facility_scoped(monkeypatch):
    first = authored()
    second = deepcopy(first)
    second["facility"]["facility_id"] = "G"
    snapshot = store.MasterSnapshot({"facilities": [
        {"facility_id": "F", "versions": [first]}, {"facility_id": "G", "versions": [second]}]}, "A", "test")
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: snapshot)
    _, f, error = service.resolve_facility_config_for_date(None, facility_id="F", effective_date="2026-01-15")
    assert error is None
    _, g, error = service.resolve_facility_config_for_date(None, facility_id="G", effective_date="2026-01-15")
    assert error is None and f.id != g.id and f.version == g.version


@pytest.mark.parametrize("change", ["revision_missing", "date_changed", "working_input_changed", "pk_changed"])
def test_changed_selection_or_missing_source_proof_stops_before_write(monkeypatch, change):
    selection, _ = selected(monkeypatch)
    if change == "revision_missing":
        selection["canonical_source_proof"]["revision"] = ""
    elif change == "date_changed":
        selection["canonical_source_proof"]["effective_date"] = "2025-12-31"
    elif change == "working_input_changed":
        selection["quantity_setting"] = 99
    else:
        selection["facility_template_version_id"] = "wrong"
    session = Session()
    with pytest.raises(store.CanonicalStoreError):
        service.materialize_facility_version_lineage(session, selection)
    assert session.statements == [] and not session.rows


@pytest.mark.parametrize("value", [True, 0.0, None])
def test_projected_literal_mutation_remains_rejected(monkeypatch, value):
    selection, _ = selected(monkeypatch)
    session = Session()
    _, row = service.materialize_facility_version_lineage(session, selection)
    row.config_json["quantity_setting"] = value
    with pytest.raises(store.CanonicalStoreError, match="snapshot_mismatch"):
        verify_projection(row)


def test_projected_bool_to_int_mutation_remains_rejected(monkeypatch):
    record = authored()
    record["facility"]["raw_bool"] = True
    selection, _ = selected(monkeypatch, record=record)
    _, row = service.materialize_facility_version_lineage(Session(), selection)
    row.config_json["raw_bool"] = 1
    with pytest.raises(store.CanonicalStoreError, match="snapshot_mismatch"):
        verify_projection(row)


@pytest.mark.parametrize("change", ["alter", "remove", "add"])
def test_full_unknown_version_receipt_remains_authoritative(monkeypatch, change):
    selection, _ = selected(monkeypatch)
    _, row = service.materialize_facility_version_lineage(Session(), selection)
    original = deepcopy(row.validation_json["canonical_lineage"])
    version = row.validation_json["canonical_lineage"]["authored_version"]
    if change == "alter":
        version["unknown_version"] = 0
    elif change == "remove":
        del version["unknown_version"]
    else:
        version["extra_raw"] = None
    with pytest.raises(store.CanonicalStoreError, match="receipt_mismatch"):
        verify_projection(row, original)
    with pytest.raises(store.CanonicalStoreError, match="digest_mismatch"):
        verify_projection(row)


def test_full_unknown_version_metadata_and_key_order_preserved(monkeypatch):
    from src.services.workflow_attempt_guard_service import snapshots_equal
    record = authored()
    record["unknown_version"] = {"bool": True, "int": 1, "float": 1.0, "null": None}
    selection, _ = selected(monkeypatch, record=record)
    _, row = service.materialize_facility_version_lineage(Session(), selection)
    receipt = deepcopy(row.validation_json["canonical_lineage"])
    row.validation_json["canonical_lineage"]["authored_version"] = dict(reversed(list(record.items())))
    assert snapshots_equal(verify_projection(row, receipt)["authored_version"], record)
    assert snapshots_equal(row.validation_json["creation_source_proof"], selection["canonical_source_proof"])
