from copy import deepcopy
from datetime import date
import importlib.util
from pathlib import Path

import pytest

from src.services import canonical_facility_lineage as lineage
from src.services import canonical_facility_store as store
from src.services import config_service
from tests.unit.test_canonical_facility_lineage import authored, install, Session


@pytest.fixture(autouse=True)
def fixed_change_day(monkeypatch):
    monkeypatch.setattr(store, "facility_change_today", lambda: date(2026, 10, 14))


def initial():
    return store.initial_facility_version(authored()["facility"], "a" * 64)


def master(versions):
    return {"facilities": [{"facility_id": "F", "versions": versions}]}


def test_initial_import_requires_no_historical_date_and_does_not_mutate_source():
    facility = authored()["facility"]
    original = deepcopy(facility)
    record = store.initial_facility_version(facility, "a" * 64)
    assert record["valid_from"] is None and record["valid_to"] is None
    source = store.MasterSnapshot(master([record]), "initial", "test")
    for day in ("2000-01-01", "2026-10-14", "2027-01-01"):
        assert store.resolve_facility_version(source, "F", day)["facility"] == facility
    assert facility == original
    assert store._decode(store._encode(source.master)) == source.master


@pytest.mark.parametrize("fault", ["no-proof", "source-hash", "content", "id", "missing-start", "dated-start"])
def test_initial_snapshot_is_explicit_not_a_null_date_fallback(fault):
    record = initial()
    if fault == "no-proof":
        record.pop("initial_snapshot")
    elif fault == "source-hash":
        record["initial_snapshot"]["source_sha256"] = "missing"
    elif fault == "content":
        record["facility"]["facility_name"] = "changed"
    elif fault == "id":
        record["version_id"] = "arbitrary"
    elif fault == "missing-start":
        record.pop("valid_from")
    else:
        record["valid_from"] = "2026-01-01"
    with pytest.raises(store.CanonicalStoreError):
        store._encode(master([record]))


def test_new_change_applies_only_from_boundary_and_keeps_selected_original(monkeypatch):
    before = initial()
    later = authored("new", "2026-11-01", None)
    later["facility"]["facility_name"] = "new name"
    versions = store.append_facility_version([before], later)
    assert before["valid_to"] is None
    assert versions[0]["valid_to"] == "2026-10-31"
    store.validate_prospective_update(master([before]), master(versions))
    snapshot = store.MasterSnapshot(master(versions), "next", "test")
    assert store.resolve_facility_version(snapshot, "F", "2026-10-31")["facility"] == before["facility"]
    assert store.resolve_facility_version(snapshot, "F", "2026-11-01")["facility"] == later["facility"]
    monkeypatch.setattr(config_service, "load_facility_master", lambda: snapshot.master)
    assert config_service.validate_selected_authored_facility_config("F", before) == before["facility"]


@pytest.mark.parametrize("fault", ["replace-content", "remove", "change-start", "close-without-new-version", "reopen"])
def test_common_cas_gate_prevents_retroactive_history_changes(fault):
    record = authored()
    previous = master([record])
    changed = deepcopy(previous)
    target = changed["facilities"][0]["versions"][0]
    if fault == "replace-content":
        target["facility"]["facility_name"] = "changed"
    elif fault == "remove":
        changed["facilities"][0]["versions"] = []
    elif fault == "change-start":
        target["valid_from"] = "2026-01-02"
    elif fault == "close-without-new-version":
        target["valid_to"] = "2026-01-30"
    else:
        target["valid_to"] = None
    with pytest.raises(store.CanonicalStoreError, match="requires_new_version"):
        store.validate_prospective_update(previous, changed)


def test_initial_lineage_materializes_without_inventing_start_or_changing_old_row(monkeypatch):
    record = initial()
    install(monkeypatch, [record])
    selected, _ = lineage.resolve(None, "F", "2026-10-14")
    session = Session()
    old = {"id": "existing-old", "config_json": {"keep": "original"}}
    session.rows[old["id"]] = deepcopy(old)
    _, row = lineage.materialize(session, selected)
    assert row.valid_from is None
    assert lineage.verify_projection(row)["authored_version"] == record
    assert session.rows[old["id"]] == old


def test_unbounded_initial_and_new_version_without_closing_overlap_and_stop():
    with pytest.raises(store.CanonicalStoreError, match="overlap"):
        store._encode(master([initial(), authored("new", "2026-11-01", None)]))


@pytest.mark.parametrize("existing", [[], [initial()]])
def test_retroactive_new_version_is_rejected_by_command_and_common_cas(existing):
    backdated = authored("retroactive", "2020-01-01", None)
    with pytest.raises(store.CanonicalStoreError, match="retroactive_change_forbidden"):
        store.append_facility_version(existing, backdated)
    with pytest.raises(store.CanonicalStoreError, match="retroactive_change_forbidden"):
        store.validate_prospective_update(master(existing), master([backdated]))


def test_offline_preparation_preserves_original_orders_and_raw_bindings():
    script = Path(__file__).resolve().parents[3] / "scripts/prepare_initial_facility_master.py"
    spec = importlib.util.spec_from_file_location("initial_migration", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inventory = {"read_only": True, "source": {"facilities": [{"id": "F"}],
        "active_order_bindings": [{"order_id": "saved", "template_version_id": "old"},
                                  {"order_id": "uploaded", "template_version_id": None}]}}
    capture = {"inventory_sha256": "inventory", "facilities": [{"facility_id": "F", "prepared_facility": authored()["facility"]}]}
    reference = {"facilities": [{"facility_id": "F", "facility_name": "reference"}], "unknown_root": 0}
    original = deepcopy((reference, capture, inventory))
    result = module.prepare_initial_master(reference, capture, inventory, capture_sha256="a" * 64, inventory_sha256="inventory")
    assert (reference, capture, inventory) == original
    assert result["unknown_root"] == 0
    assert result["facilities"][0]["versions"][0]["valid_from"] is None
    with pytest.raises(ValueError, match="already_exist"):
        module.prepare_initial_master(result, capture, inventory, capture_sha256="a" * 64, inventory_sha256="inventory")
