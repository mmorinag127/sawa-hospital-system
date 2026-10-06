"""Explicit seed validation against disposable SQLite fixtures only."""

from contextlib import contextmanager
import copy
import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.db import Base
from src.models.facility import Facility, FacilityArea, FacilityConfig


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/seed_facilities.py"


def load_script():
    spec = importlib.util.spec_from_file_location("owned_seed_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed_database(tmp_path, monkeypatch):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "owned-seed.sqlite3"))
    Base.metadata.create_all(engine, tables=[Facility.__table__, FacilityArea.__table__, FacilityConfig.__table__])
    module = load_script()
    entered = []

    @contextmanager
    def owned_session():
        entered.append(True)
        with Session(engine) as session:
            with session.begin():
                yield session

    monkeypatch.setattr(module, "session_scope", owned_session)
    with Session(engine) as session, session.begin():
        session.add(Facility(id="preserved", name="Preserved"))
        session.add(FacilityArea(id="existing-area", facility_id="preserved", name="Existing"))
        session.add(FacilityConfig(facility_id="preserved", config_json={"quantity": 3.25}))
    try:
        yield module, engine, entered
    finally:
        engine.dispose()


def snapshot(engine):
    with engine.connect() as connection:
        return {
            table.name: sorted(repr(tuple(row)) for row in connection.execute(sa.select(table)))
            for table in (Facility.__table__, FacilityArea.__table__, FacilityConfig.__table__)
        }


def valid_master():
    return {"facilities": [{"facility_id": "owned", "facility_name": "Owned", "areas": [
        {"area_id": "2F", "name": "Second floor"}
    ]}]}


@pytest.mark.parametrize("mutation", (
    "invalid-json", "root-list", "missing-facilities", "empty-facilities", "facilities-object",
    "facility-not-object", "missing-facility_id", "empty-facility_id", "missing-facility_name",
    "empty-facility_name", "whitespace-name", "non-string-id", "missing-areas", "areas-object",
    "area-not-object", "missing-area_id", "empty-area_id", "missing-area-name", "empty-area-name",
    "duplicate-facility", "duplicate-area", "late-invalid-facility",
))
def test_invalid_master_stops_before_session_and_preserves_db(seed_database, tmp_path, mutation):
    module, engine, entered = seed_database
    master = valid_master()
    facility = master["facilities"][0]
    area = facility["areas"][0]
    if mutation == "root-list":
        master = []
    elif mutation == "missing-facilities":
        master = {}
    elif mutation == "empty-facilities":
        master["facilities"] = []
    elif mutation == "facilities-object":
        master["facilities"] = {}
    elif mutation == "facility-not-object":
        master["facilities"] = [None]
    elif mutation.startswith("missing-facility"):
        del facility[mutation.removeprefix("missing-")]
    elif mutation.startswith("empty-facility"):
        facility[mutation.removeprefix("empty-")] = ""
    elif mutation == "whitespace-name":
        facility["facility_name"] = " \t\n"
    elif mutation == "non-string-id":
        facility["facility_id"] = 7
    elif mutation == "missing-areas":
        del facility["areas"]
    elif mutation == "areas-object":
        facility["areas"] = {}
    elif mutation == "area-not-object":
        facility["areas"] = [None]
    elif mutation in ("missing-area_id", "missing-area-name"):
        del area["area_id" if mutation.endswith("area_id") else "name"]
    elif mutation in ("empty-area_id", "empty-area-name"):
        area["area_id" if mutation.endswith("area_id") else "name"] = ""
    elif mutation == "duplicate-facility":
        master["facilities"].append(copy.deepcopy(facility))
    elif mutation == "duplicate-area":
        facility["areas"].append(copy.deepcopy(area))
    elif mutation == "late-invalid-facility":
        master["facilities"].append({"facility_id": "later", "areas": []})
    path = tmp_path / "master.json"
    path.write_text("{" if mutation == "invalid-json" else json.dumps(master))
    before = snapshot(engine)
    with pytest.raises(ValueError):
        module.seed_from_master(path)
    assert entered == []
    assert snapshot(engine) == before


def test_current_17_facilities_empty_areas_preserve_exact_values(seed_database):
    module, engine, entered = seed_database
    path = ROOT / "src/data/facility_master.template.json"
    master = json.loads(path.read_text())
    assert len(master["facilities"]) == 17
    assert all(facility["areas"] == [] for facility in master["facilities"])
    assert module._load_master(path) == master
    assert module.seed_from_master(path) == 17
    assert entered == [True]
    with Session(engine) as session:
        for facility in master["facilities"]:
            actual = session.get(Facility, facility["facility_id"])
            assert actual.name == facility["facility_name"]
            assert actual.areas == []
            expected = module._extract_config(facility)
            if expected:
                assert actual.config.config_json == expected
        assert session.get(FacilityConfig, "preserved").config_json == {"quantity": 3.25}


def test_area_id_is_scoped_to_facility_and_text_is_not_rewritten(seed_database, tmp_path):
    module, engine, _ = seed_database
    master = valid_master()
    master["facilities"][0]["facility_name"] = " Owned "
    other = copy.deepcopy(master["facilities"][0])
    other["facility_id"] = "other"
    master["facilities"].append(other)
    path = tmp_path / "scoped.json"
    path.write_text(json.dumps(master))
    assert module.seed_from_master(path) == 2
    assert module.seed_from_master(path) == 2
    with Session(engine) as session:
        for facility_id in ("owned", "other"):
            assert session.get(Facility, facility_id).name == " Owned "
            assert session.get(FacilityArea, ("2F", facility_id)).name == "Second floor"


def test_import_never_connects_creates_schema_or_seeds(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("import must not access a database")

    monkeypatch.setattr(sa.engine.Engine, "connect", forbidden)
    monkeypatch.setattr(Base.metadata, "create_all", forbidden)
    import src.db
    monkeypatch.setattr(src.db, "session_scope", forbidden)
    module = load_script()
    assert callable(module.seed_from_master)


@pytest.mark.parametrize("value", (None, "", " ", "\t\n"))
def test_explicit_seed_entry_requires_master_path_before_session(seed_database, monkeypatch, value):
    module, engine, entered = seed_database
    if value is None:
        monkeypatch.delenv("FACILITY_MASTER_PATH", raising=False)
    else:
        monkeypatch.setenv("FACILITY_MASTER_PATH", value)
    before = snapshot(engine)
    with pytest.raises(ValueError, match="FACILITY_MASTER_PATH is required"):
        module.main()
    assert entered == []
    assert snapshot(engine) == before


def test_explicit_seed_entry_uses_only_supplied_json(seed_database, tmp_path, monkeypatch):
    module, engine, entered = seed_database
    path = tmp_path / "explicit.json"
    path.write_text(json.dumps(valid_master()))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    assert module.main() == 1
    before = snapshot(engine)
    assert module.main() == 1
    assert snapshot(engine) == before
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        module.main()
    assert entered == [True, True]
    assert snapshot(engine) == before
