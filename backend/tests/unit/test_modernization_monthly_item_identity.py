"""Owned SQLite/model and HTTP-boundary regressions, not live/operator proof.

Identity/index authority: src.monthly_menu_identity and src.models.menu.
Stub provenance: integration/test_menu_override.py. Exception body/CAS authority:
api.menus.upsert_menu_entry_exceptions and menu_service.monthly_editor_command.
Only the router role dependency is replaced with synthetic unit authentication.
"""

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from src import db
from src.api import menus
from src.models.facility import Facility, FacilityConfig
from src.models.menu import (
    BaseMenuCycleItem, MenuEditRevision, MenuFacilityOverride, MenuMaster,
    MenuRule, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuImport, MonthlyMenuItem,
)
from src.models.user import AuditLog
from src.monthly_menu_identity import IDENTITY_INDEX
from src.services import menu_service as service
from src.services.menu_vocabulary import normalize_diet_type


MONTH = "2026-10"
NAME = "Dish"


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "identity.sqlite3")),
        connect_args={"check_same_thread": False})
    metadata = sa.MetaData()
    for model in (MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, MonthlyMenuImport,
                  MenuEditRevision, MenuMaster, MenuFacilityOverride, MenuRule,
                  BaseMenuCycleItem, Facility, FacilityConfig, AuditLog):
        model.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(engine, autoflush=False, future=True))
    monkeypatch.setattr(service, "engine", engine)
    monkeypatch.setattr(service, "_MENU_SCHEMA_INITIALIZED", False)
    with db.session_scope() as session:
        session.add_all([
            MonthlyMenu(id=MONTH, month_start=date(2026, 10, 1)),
            MonthlyMenu(id="2026-11", month_start=date(2026, 11, 1)),
            MenuEditRevision(scope="monthly-menu:" + MONTH, revision=1),
            Facility(id="facility-a", name="Unit facility A"),
            Facility(id="facility-b", name="Unit facility B"),
            MenuMaster(id="master", name=NAME, normalized_name="dish", unit_type="g",
                qty_per_serving=12, bag_max_qty=3, bag_max_unit="g",
                condiments=[]),
        ])
    # The actual schema guard is exercised, not bypassed by a mocked success.
    service.ensure_menu_schema()
    with engine.connect() as connection:
        assert connection.scalar(sa.text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=:name"
        ), {"name": IDENTITY_INDEX}) == IDENTITY_INDEX
    yield engine
    engine.dispose()


def seed(item_id="item", **fields):
    values = dict(id=item_id, monthly_menu_id=MONTH, name=NAME, menu_master_id="master")
    values.update(fields)
    with db.session_scope() as session:
        session.add(MonthlyMenuItem(**values))


def lookup(session, **fields):
    values = dict(month_id=MONTH, name=NAME, facility_override=None)
    values.update(fields)
    return service._find_existing_monthly_item(session, **values)


def test_retry_stub_reuses_exact_row_and_preserves_stored_values(store):
    seed(unit_type="g", qty_per_serving=0, bag_max_qty=4, bag_max_unit="g")
    with db.session_scope() as session:
        # Opaque persisted JSON preservation only, not a resolved condiment claim.
        session.get(MenuMaster, "master").condiments = {"operator_extension": {"raw": [0, None, "0.125"]}}
        session.flush()
        before = dict(session.execute(sa.select(MenuMaster.__table__).where(MenuMaster.id == "master")).mappings().one())
    first = service.create_item_stub(MONTH, NAME)
    second = service.create_item_stub(MONTH, NAME)
    assert first == second
    assert first["id"] == "item" and first["menu_master_id"] == "master"
    assert first["qty_per_serving"] == 0 and first["unit_type"] == "g"
    assert first["bag_max_qty"] == 4 and first["diet_type"] is None
    with db.session_scope() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuItem)) == 1
        after = dict(session.execute(sa.select(MenuMaster.__table__).where(MenuMaster.id == "master")).mappings().one())
        assert after == before
        assert session.get(MonthlyMenuItem, "item").diet_type is None


@pytest.mark.parametrize("stored,requested", [(None, ""), ("", None)])
def test_null_empty_aliases_match_the_actual_coalesced_index(store, stored, requested):
    seed(daypart=stored, category=stored, diet_type=stored, facility_override=stored)
    with db.session_scope() as session:
        found = lookup(session, daypart=requested, category=requested,
            diet_type=requested, facility_override=requested)
        assert found is session.get(MonthlyMenuItem, "item")
    # The schema itself rejects the alias duplicate; the helper agrees with it.
    with pytest.raises(sa.exc.IntegrityError):
        seed("alias-duplicate", daypart=requested, category=requested,
            diet_type=requested, facility_override=requested)


@pytest.mark.parametrize("field,value", [
    ("monthly_menu_id", "2026-11"), ("name", "Other dish"),
    ("daypart", "昼食"), ("category", "主菜"), ("facility_override", "facility-a"),
])
def test_each_other_identity_axis_remains_separate(store, field, value):
    seed("other", **{field: value})
    with db.session_scope() as session:
        assert lookup(session) is None
        requested = {"month_id" if field == "monthly_menu_id" else field: value}
        assert lookup(session, **requested).id == "other"
    own = service.create_item_stub(MONTH, NAME)
    assert own["id"] != "other" and own["diet_type"] is None
    with db.session_scope() as session:
        assert lookup(session).id == own["id"]
        assert getattr(session.get(MonthlyMenuItem, "other"), field) == value
        assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuItem)) == 2


def test_regular_soft_mixer_and_unresolved_are_distinct_identities(store):
    for diet in ("regular", "soft", "mixer"):
        seed(diet, diet_type=diet, qty_per_serving={"regular": 12, "soft": 3, "mixer": 4}[diet])
    seed("unresolved", qty_per_serving=0)
    with db.session_scope() as session:
        assert lookup(session).id == "unresolved"
        for diet in ("regular", "soft", "mixer"):
            assert lookup(session, diet_type=diet) is session.get(MonthlyMenuItem, diet)
            assert lookup(session, diet_type=diet, exclude_id=diet) is None
        assert lookup(session, diet_type="軟菜").id == "soft"
        assert lookup(session, diet_type="ミキサー").id == "mixer"
        assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuItem)) == 4


def test_resolved_item_is_not_adopted_as_unresolved_stub(store):
    seed("regular", diet_type="regular", qty_per_serving=12)
    result = service.create_item_stub(MONTH, NAME)
    assert result["id"] != "regular" and result["diet_type"] is None
    assert result["qty_per_serving"] is None
    assert service.create_item_stub(MONTH, NAME)["id"] == result["id"]
    with db.session_scope() as session:
        regular = session.get(MonthlyMenuItem, "regular")
        assert regular.diet_type == "regular" and regular.qty_per_serving == 12
        assert session.get(MonthlyMenuItem, result["id"]).diet_type is None
        assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuItem)) == 2


def test_nonempty_normalized_away_diet_never_aliases_null(store):
    seed("unresolved")
    seed("unknown-text", diet_type="operator-specific")
    with db.session_scope() as session:
        for raw in ("-", "()", "【】", "_", "/"):
            assert raw.strip() and normalize_diet_type(raw) is None
            assert lookup(session, diet_type=raw) is None
        assert lookup(session, diet_type="operator-specific").id == "unknown-text"
        assert lookup(session).id == "unresolved"
        assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuItem)) == 2


def test_legacy_corrupt_duplicate_identity_explicitly_stops(tmp_path):
    # Independent pre-index/corrupt database only. The canonical fixture/index
    # above and the production schema are never dropped or weakened.
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "legacy.sqlite3")))
    metadata = sa.MetaData()
    for model in (MonthlyMenu, MenuMaster, MonthlyMenuItem):
        model.__table__.to_metadata(metadata)
    metadata.tables["monthly_menu_items"].indexes.clear()
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(MonthlyMenu.__table__.insert(), {"id": MONTH})
            connection.execute(MonthlyMenuItem.__table__.insert(), [
                {"id": "legacy-null", "monthly_menu_id": MONTH, "name": NAME, "diet_type": None},
                {"id": "legacy-empty", "monthly_menu_id": MONTH, "name": NAME, "diet_type": ""},
            ])
        with Session(engine) as session:
            with pytest.raises(ValueError, match="^monthly_menu_item_identity_ambiguous$"):
                lookup(session)
            assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuItem)) == 2
    finally:
        engine.dispose()


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(menus.router, prefix="/monthly-menus")
    # Synthetic authentication only; actual router, CAS and domain service run.
    for route in menus.router.routes:
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = lambda: None
    with TestClient(app) as transport:
        yield transport


def seed_exception_siblings():
    seed("base-soft", daypart="昼食", category="主菜", diet_type="soft", unit_type="g", qty_per_serving=3)
    seed("scoped-regular", daypart="昼食", category="主菜", diet_type="regular",
        facility_override="facility-a", unit_type="g", qty_per_serving=12, master_resolution_mode="month_only")
    seed("scoped-soft", daypart="昼食", category="主菜", diet_type="soft",
        facility_override="facility-a", unit_type="g", qty_per_serving=3, master_resolution_mode="month_only")
    with db.session_scope() as session:
        session.add_all([
            MonthlyMenuEntry(id="base-entry", monthly_menu_id=MONTH, menu_date=date(2026, 10, 1),
                daypart="昼食", category="主菜", diet_type="soft", slot_index=0, name=NAME),
            MonthlyMenuEntry(id="scoped-entry", monthly_menu_id=MONTH, menu_date=date(2026, 10, 1),
                daypart="昼食", category="主菜", diet_type="soft", slot_index=0,
                facility_override="facility-a", name=NAME),
        ])


def test_public_cas_exception_updates_only_scoped_same_diet(client):
    seed_exception_siblings()
    read = client.get(f"/monthly-menus/{MONTH}?editor=true")
    assert read.status_code == 200
    token = read.json()["revision"]
    body = {"facility_ids": ["facility-a"], "diet_type": "soft", "qty_per_serving": 0,
        "unit_type": "g", "bag_max_qty": 4, "bag_max_unit": "g"}
    response = client.post(f"/monthly-menus/{MONTH}/entries/base-entry/exceptions",
        headers={"If-Match": token}, json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["updated"] is True and saved["snapshot"]["revision"] != token
    assert [item["id"] for item in saved["items"]] == ["scoped-soft"]
    assert saved["items"][0]["qty_per_serving"] == 0
    with db.session_scope() as session:
        assert session.get(MonthlyMenuItem, "scoped-regular").qty_per_serving == 12
        assert session.get(MonthlyMenuItem, "base-soft").qty_per_serving == 3
        soft = session.get(MonthlyMenuItem, "scoped-soft")
        assert (soft.diet_type, soft.facility_override, soft.qty_per_serving, soft.bag_max_qty) == (
            "soft", "facility-a", 0, 4)
        assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuItem)) == 3
        assert session.scalar(sa.select(sa.func.count()).select_from(MonthlyMenuEntry)) == 2


def test_public_exception_missing_and_stale_cas_stop_without_mutation(client):
    seed_exception_siblings()
    token = client.get(f"/monthly-menus/{MONTH}?editor=true").json()["revision"]
    path = f"/monthly-menus/{MONTH}/entries/base-entry/exceptions"
    body = {"facility_ids": ["facility-a"], "diet_type": "soft", "qty_per_serving": 0}
    assert client.post(path, json=body).status_code == 428
    assert client.post(path, json=body, headers={"If-Match": token}).status_code == 200
    with db.session_scope() as session:
        before = [dict(row) for row in session.execute(sa.select(MonthlyMenuItem.__table__).order_by(MonthlyMenuItem.id)).mappings()]
        revision = session.get(MenuEditRevision, "monthly-menu:" + MONTH).revision
    stale = client.post(path, json={**body, "qty_per_serving": 99}, headers={"If-Match": token})
    assert stale.status_code == 409
    with db.session_scope() as session:
        after = [dict(row) for row in session.execute(sa.select(MonthlyMenuItem.__table__).order_by(MonthlyMenuItem.id)).mappings()]
        assert after == before
        assert session.get(MenuEditRevision, "monthly-menu:" + MONTH).revision == revision
