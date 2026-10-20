from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db import Base
from src.models.menu import MenuMaster, MonthlyMenuItem, MenuFacilityOverride
from src.services import menu_service as service


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    monkeypatch.setattr(service, "session_scope", scope)
    monkeypatch.setattr(service, "ensure_menu_schema", lambda: None)
    monkeypatch.setattr(service, "record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.menu_rule_service, "ensure_default_rules", lambda: None)
    monkeypatch.setattr(service.menu_rule_service, "list_active_rules", lambda: [
        {"rule_type": "global", "daypart": "夕食", "category": "主菜",
         "unit_type": "g", "qty_per_serving": 100},
    ])
    yield scope
    engine.dispose()


def upload(monkeypatch, month, fields, resolutions=None):
    item = {"name": "Fish", "daypart": "夕食", "category": "主菜", **fields}
    monkeypatch.setattr(service, "_parse_monthly_menu", lambda *args: (
        date(2026, month, 1), None, [item], [],
    ))
    return service.create_menu(f"2026-{month:02}", b"fixture", "menu.xlsx",
                               menu_master_resolutions=resolutions)


@pytest.mark.parametrize("unit,quantity", [("cut", 1), ("count", 2)])
def test_repeated_upload_preserves_registered_master(database, monkeypatch, unit, quantity):
    with database() as session:
        session.add(MenuMaster(id="master", name="Fish", normalized_name="fish",
                               unit_type=unit, qty_per_serving=quantity,
                               temp_type="hot", category="主菜",
                               bag_max_qty=20, bag_max_unit=unit))
    for month in (9, 10, 11, 12):
        upload(monkeypatch, month, {})
        with database() as session:
            item = session.query(MonthlyMenuItem).filter_by(monthly_menu_id=f"2026-{month:02}").one()
            master = session.get(MenuMaster, "master")
            assert (item.unit_type, item.qty_per_serving) == (None, None)
            assert (master.unit_type, master.qty_per_serving) == (unit, quantity)
            assert service._build_menu_master_checks(session, [item], []) == {"count": 0, "issues": []}
        merged = service.get_menu_items(f"2026-{month:02}", allow_unresolved=True)[0]
        assert (merged["unit_type"], merged["qty_per_serving"]) == (unit, quantity)


def test_explicit_original_quantity_still_produces_diff(database, monkeypatch):
    with database() as session:
        session.add(MenuMaster(id="master", name="Fish", normalized_name="fish",
                               unit_type="g", qty_per_serving=140, temp_type="hot", category="主菜"))
    upload(monkeypatch, 12, {"unit_type": "g", "qty_per_serving": 100})
    with database() as session:
        item = session.query(MonthlyMenuItem).one()
        issue = service._build_menu_master_checks(session, [item], [])["issues"][0]
        assert issue["field_diffs"] == [
            {"field": "qty_per_serving", "label": "量", "monthly_value": 100.0, "master_value": 140.0},
        ]
        assert session.get(MenuMaster, "master").qty_per_serving == 140


def test_missing_master_value_is_not_filled_from_rule(database, monkeypatch):
    with database() as session:
        session.add(MenuMaster(id="master", name="Fish", normalized_name="fish"))
    upload(monkeypatch, 12, {})
    with database() as session:
        item = session.query(MonthlyMenuItem).one()
        master = session.get(MenuMaster, "master")
        assert item.qty_per_serving is None
        assert item.unit_type is None
        assert master.qty_per_serving is None
        assert master.unit_type is None


def test_source_patch_preserves_explicit_values_and_does_not_infer_missing():
    implicit = service._apply_rule_payloads_to_items([
        {"name": "Fish", "unit_type": "count", "qty_per_serving": 3},
    ], [])[0]
    assert service._monthly_item_patch_from_source(implicit)["qty_per_serving"] == 3


def test_master_quantity_does_not_depend_on_a_matching_default_rule():
    source = service._apply_rule_payloads_to_items([{"name": "Fish"}], [])[0]
    patch = service._monthly_item_patch_from_source(source)
    assert "unit_type" not in patch
    assert "qty_per_serving" not in patch


@pytest.mark.parametrize("action", ["create", "update"])
def test_operator_resolution_overrides_inferred_values(database, monkeypatch, action):
    if action == "update":
        with database() as session:
            session.add(MenuMaster(id="master", name="Fish", normalized_name="fish",
                                   unit_type="g", qty_per_serving=100))
    upload(monkeypatch, 10, {}, [{
        "source_name": "Fish", "action": action, "unit_type": "cut", "qty_per_serving": 1,
        "bag_max_qty": 16, "bag_max_unit": "cut",
    }])
    with database() as session:
        item = session.query(MonthlyMenuItem).one()
        assert (item.unit_type, item.qty_per_serving) == (None, None)
        assert service._build_menu_master_checks(session, [item], [])["issues"] == []
    merged = service.get_menu_items("2026-10", allow_unresolved=True)[0]
    assert (merged["unit_type"], merged["qty_per_serving"]) == ("cut", 1)


def test_inherited_quantity_tracks_master_and_facility_updates(database, monkeypatch):
    with database() as session:
        session.add(MenuMaster(id="master", name="Fish", normalized_name="fish",
                               unit_type="cut", qty_per_serving=1, category="主菜",
                               bag_max_qty=20, bag_max_unit="cut"))
    upload(monkeypatch, 10, {"diet_type": "regular"})
    assert service.update_menu_master("master", {"unit_type": "count", "qty_per_serving": 2})
    items = service.get_menu_items("2026-10", allow_unresolved=True)
    assert (items[0]["unit_type"], items[0]["qty_per_serving"]) == ("count", 2)
    with database() as session:
        item = session.query(MonthlyMenuItem).one()
        assert item.qty_per_serving is None and item.unit_type is None
        assert service._build_menu_master_checks(session, [item], [])["count"] == 0
        assert service.get_item(item.id)["qty_per_serving"] == 2
        session.add(MenuFacilityOverride(id="override", menu_master_id="master", facility_id="FAC1",
                                        qty_per_serving=3))
    monkeypatch.setattr(service, "_resolve_override_scope_ids", lambda *args: ["FAC1"])
    assert service.get_menu_items_for_facility("2026-10", "FAC1", allow_unresolved=True)[0]["qty_per_serving"] == 3


@pytest.mark.parametrize("quantity", [75, 0])
def test_explicit_monthly_quantity_survives_master_update(database, monkeypatch, quantity):
    with database() as session:
        session.add(MenuMaster(id="master", name="Fish", normalized_name="fish",
                               unit_type="g", qty_per_serving=100, category="主菜"))
    upload(monkeypatch, 10, {"unit_type": "g", "qty_per_serving": quantity})
    assert service.update_menu_master("master", {"qty_per_serving": 120})
    assert service.get_menu_items("2026-10", allow_unresolved=True)[0]["qty_per_serving"] == quantity
    with database() as session:
        item = session.query(MonthlyMenuItem).one()
        assert item.qty_per_serving == quantity
        assert service._build_menu_master_checks(session, [item], [])["count"] == 1
