from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db import Base
from src.models.menu import MenuMaster, MonthlyMenuItem
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


def upload(monkeypatch, month, fields):
    item = {"name": "Fish", "daypart": "夕食", "category": "主菜", **fields}
    monkeypatch.setattr(service, "_parse_monthly_menu", lambda *args: (
        date(2026, month, 1), None, [item], [],
    ))
    return service.create_menu(f"2026-{month:02}", b"fixture", "menu.xlsx")


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
            assert (item.unit_type, item.qty_per_serving) == (unit, quantity)
            assert (master.unit_type, master.qty_per_serving) == (unit, quantity)
            assert service._build_menu_master_checks(session, [item], []) == {"count": 0, "issues": []}


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
    master = MenuMaster(unit_type="cut", qty_per_serving=1)
    implicit = service._apply_rule_payloads_to_items([
        {"name": "Fish", "unit_type": "count", "qty_per_serving": 3},
    ], [])[0]
    assert service._monthly_item_patch_from_source(implicit, master)["qty_per_serving"] == 3


def test_master_quantity_does_not_depend_on_a_matching_default_rule():
    master = MenuMaster(unit_type="cut", qty_per_serving=1)
    source = service._apply_rule_payloads_to_items([{"name": "Fish"}], [])[0]
    patch = service._monthly_item_patch_from_source(source, master)
    assert (patch["unit_type"], patch["qty_per_serving"]) == ("cut", 1)
