from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.menu import MenuMaster
from src.services import menu_service as service


@pytest.fixture(autouse=True)
def database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    MenuMaster.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    monkeypatch.setattr(service, "session_scope", scope)
    monkeypatch.setattr(service, "ensure_menu_schema", lambda: None)
    yield
    engine.dispose()


@pytest.mark.parametrize("unit,quantity", [("g", 100), ("cut", 1), ("count", 2)])
def test_create_update_reload_preserves_every_editable_field(unit, quantity):
    payload = {
        "name": "  Test Fish  ", "unit_type": unit, "qty_per_serving": quantity,
        "bag_max_qty": 8, "bag_max_unit": "count", "temp_type": "hot",
        "daypart": "lunch", "category": "Side 1", "condiments": ["Sauce", "Salt"],
    }
    created = service.create_menu_master(payload)
    expected = {**payload, "name": "Test Fish", "daypart": "昼食"}
    assert {key: created[key] for key in expected} == expected
    assert created["normalized_name"] == "testfish"
    assert service.list_menu_masters() == [created]

    changed = {**expected, "name": "Test Chicken", "qty_per_serving": 3,
               "bag_max_qty": 12, "bag_max_unit": "cut", "temp_type": "cold",
               "daypart": "夕食", "category": "Main", "condiments": ["Gravy"]}
    assert service.update_menu_master(created["id"], changed) is True
    saved = service.list_menu_masters()[0]
    assert saved["id"] == created["id"]
    assert saved["normalized_name"] == "testchicken"
    assert {key: saved[key] for key in changed} == changed


def test_missing_values_remain_missing_and_explicit_zero_is_not_replaced():
    created = service.create_menu_master({"name": "Test Menu"})
    for key in ("unit_type", "qty_per_serving", "bag_max_qty", "bag_max_unit",
                "temp_type", "daypart", "category"):
        assert created[key] is None
    assert created["condiments"] == []
    assert service.update_menu_master(created["id"], {"qty_per_serving": 0, "bag_max_qty": 0})
    saved = service.list_menu_masters()[0]
    assert saved["qty_per_serving"] == saved["bag_max_qty"] == 0
    assert service.update_menu_master(created["id"], {"qty_per_serving": None, "bag_max_qty": None})
    saved = service.list_menu_masters()[0]
    assert saved["qty_per_serving"] is saved["bag_max_qty"] is None


@pytest.mark.parametrize("temperature,accessories", [("温菜", None), ("冷菜", [" 塩 "])])
def test_category_update_preserves_untouched_stored_representations(temperature, accessories):
    with service.session_scope() as session:
        master = MenuMaster(id="legacy", name=" Test Fish ", normalized_name="testfish",
                            unit_type="個", qty_per_serving=0, bag_max_qty=10,
                            bag_max_unit="個", temp_type=temperature, daypart="lunch",
                            category="主菜", condiments=accessories)
        session.add(master)
        session.flush()
        before = {column.name: getattr(master, column.name) for column in MenuMaster.__table__.columns}
    item = service.get_menu_master("legacy")
    saved = service.save_menu_master("legacy", {**item, "category": "副菜"}, expected_revision=item["revision"])
    assert saved["category"] == "副菜"
    with service.session_scope() as session:
        master = session.get(MenuMaster, "legacy")
        after = {column.name: getattr(master, column.name) for column in MenuMaster.__table__.columns}
    for field in before.keys() - {"category", "revision", "updated_at"}:
        assert after[field] == before[field], field
    assert service.get_menu_master("legacy")["category"] == "副菜"


def test_explicit_temperature_and_accessory_edits_still_apply():
    with service.session_scope() as session:
        session.add(MenuMaster(id="legacy", name="Fish", normalized_name="fish",
                               temp_type="温菜", condiments=["塩"]))
    item = service.get_menu_master("legacy")
    saved = service.save_menu_master("legacy", {"temp_type": "cold", "condiments": []},
                                     expected_revision=item["revision"])
    assert saved["temp_type"] == "cold"
    assert saved["condiments"] == []


def test_duplicate_create_returns_existing_record_without_changing_values():
    first = service.create_menu_master({"name": "Test Fish", "unit_type": "cut", "qty_per_serving": 1})
    duplicate = service.create_menu_master({"name": "testfish", "unit_type": "g", "qty_per_serving": 100})
    assert duplicate == first
    assert service.list_menu_masters() == [first]


def test_duplicate_rename_rolls_back_and_unknown_update_does_not_create():
    first = service.create_menu_master({"name": "Alpha"})
    second = service.create_menu_master({"name": "Beta", "qty_per_serving": 1})
    with pytest.raises(ValueError, match="duplicate menu name"):
        service.update_menu_master(second["id"], {"name": "alpha", "qty_per_serving": 9})
    assert service.list_menu_masters() == [first, second]
    assert service.update_menu_master("missing", {"name": "Gamma"}) is False


@pytest.mark.parametrize("name", ["", "  "])
def test_blank_name_is_rejected_without_creating_a_record(name):
    with pytest.raises(ValueError, match="name is required"):
        service.create_menu_master({"name": name})
    assert service.list_menu_masters() == []


def test_search_and_limit_preserve_existing_order():
    service.create_menu_master({"name": "Beta Fish"})
    first = service.create_menu_master({"name": "Alpha Fish"})
    service.create_menu_master({"name": "Chicken"})
    assert service.list_menu_masters(query=" fish ", limit=1) == [first]
    assert service.list_menu_masters(query="not present") == []
