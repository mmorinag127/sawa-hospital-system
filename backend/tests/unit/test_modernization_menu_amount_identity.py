"""Real owned SQLite menu reads; namespace inputs are serializer-boundary only.

No menu selection, identity guard, or master read is mocked. This is not HTTP,
PostgreSQL, operator, or full-workflow proof and does not waive original failures.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from src import db
from src.models.menu import MenuMaster, MonthlyMenuEntry, MonthlyMenuItem
from src.services import menu_service
from src.services import order_serialization_service as serialization
from test_modernization_monthly_item_identity import MONTH, NAME, store


DAY = date(2026, 10, 4)
DAYPART = "朝食"


def _seed_item(item_id, *, quantity, category="主菜", master_id="master",
               diet="regular", menu_date=DAY, slot=0):
    with db.session_scope() as session:
        session.add(MonthlyMenuItem(
            id=item_id, monthly_menu_id=MONTH, menu_master_id=master_id,
            name=NAME, daypart=DAYPART, category=category, diet_type=diet,
            facility_override=None, qty_per_serving=quantity, unit_type="g",
        ))
        session.add(MonthlyMenuEntry(
            id=item_id + "-entry", monthly_menu_id=MONTH, menu_date=menu_date,
            name=NAME, daypart=DAYPART, category=category, diet_type=diet,
            slot_index=slot, facility_override=None,
        ))


def _order(diet="regular"):
    # None explicitly selects the all-facilities monthly base, not a guessed unit.
    return SimpleNamespace(week_code=MONTH, facility_code=None, lines=[
        SimpleNamespace(line_id="line", menu_name=NAME, date=DAY,
                        daypart=DAYPART, diet_type=diet, area_id=None,
                        quantity_original=0, quantity_corrected=None,
                        remarks=""),
    ])


def _snapshot(engine, metadata):
    with engine.connect() as connection:
        return {
            table.name: deepcopy([dict(row) for row in connection.execute(
                sa.select(table).order_by(*table.primary_key.columns)
            ).mappings()])
            for table in metadata.sorted_tables
        }


@contextmanager
def _readonly(engine, order):
    metadata = sa.MetaData()
    metadata.reflect(bind=engine)
    before = _snapshot(engine, metadata)
    original = deepcopy(order)
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().split(None, 1)[0].upper() in {"SELECT", "PRAGMA"}, statement

    sa.event.listen(engine, "before_cursor_execute", observe)
    try:
        yield
        assert statements
        assert _snapshot(engine, metadata) == before
        assert order == original
        assert order.lines[0].quantity_original == 0
        assert order.lines[0].quantity_corrected is None
        assert order.lines[0].remarks == ""
    finally:
        sa.event.remove(engine, "before_cursor_execute", observe)


def _assert_resolution(order, *, quantity=None, fields=None):
    line = order.lines[0]
    affected = [] if fields is None else [{
        "line_id": "line", "date": DAY.isoformat(), "daypart": DAYPART,
        "menu_name": NAME, "diet_type": line.diet_type, "area_id": None,
        "unresolved_fields": fields,
    }]
    expected = {
        "status": "resolved" if fields is None else "unresolved",
        "blocker": None if fields is None else "menu_amount_unresolved",
        "message": None if fields is None else serialization.MENU_AMOUNT_MESSAGE,
        "affected_lines": affected,
    }
    meta = {} if fields is not None else {
        serialization._line_menu_identity(line): {"qty_per_serving": quantity, "unit_type": "g"},
    }
    for _ in range(2):
        assert serialization._resolve_menu_amounts(order) == (meta, expected)
        if fields is None:
            assert serialization._build_menu_amount_meta(order) == meta
        else:
            with pytest.raises(menu_service.MenuReadUnresolved) as caught:
                serialization._build_menu_amount_meta(order)
            assert caught.value.code == "menu_amount_unresolved"
            assert caught.value.items == affected


@pytest.mark.parametrize("side_quantity,side_master,side_date,fields,quantity", [
    pytest.param(41, "master", DAY, ["menu_identity"], None, id="different-amount-authorities"),
    pytest.param(37, "master", DAY, ["menu_identity"], None, id="same-amount-authorities"),
    pytest.param(37, "second-master", DAY, ["menu_identity"], None, id="same-amount-distinct-master-fks"),
    pytest.param(41, "master", date(2026, 10, 5), None, 37.0, id="other-date-not-authority"),
])
def test_dated_category_authority_requires_unique_identity(
    store, side_quantity, side_master, side_date, fields, quantity,
):
    if side_master == "second-master":
        with db.session_scope() as session:
            session.add(MenuMaster(
                id=side_master, name="Second explicit master", normalized_name="second-explicit-master",
                qty_per_serving=12, unit_type="g", condiments=[],
            ))
    _seed_item("main", quantity=37)
    _seed_item("side", quantity=side_quantity, category="副菜1",
               master_id=side_master, menu_date=side_date, slot=1)
    order = _order()
    with _readonly(store, order):
        _assert_resolution(order, quantity=quantity, fields=fields)


@pytest.mark.parametrize("stored_quantity,master_id,quantity,fields", [
    pytest.param(0, "master", 0.0, None, id="explicit-zero-not-master-twelve"),
    pytest.param(None, "master", 12.0, None, id="null-with-explicit-fk-uses-master-twelve"),
    pytest.param(None, None, None, ["qty_per_serving"], id="null-without-fk-unresolved"),
])
def test_monthly_zero_and_null_have_distinct_master_semantics(
    store, stored_quantity, master_id, quantity, fields,
):
    _seed_item("main", quantity=stored_quantity, master_id=master_id)
    order = _order()
    with _readonly(store, order):
        _assert_resolution(order, quantity=quantity, fields=fields)
        for _ in range(2):
            item = menu_service.get_item("main")
            assert item["menu_master_id"] == master_id
            assert item["qty_per_serving"] == quantity
        with store.connect() as connection:
            assert connection.scalar(sa.select(MonthlyMenuItem.qty_per_serving).where(
                MonthlyMenuItem.id == "main"
            )) == stored_quantity
            assert connection.scalar(sa.select(MenuMaster.qty_per_serving).where(
                MenuMaster.id == "master"
            )) == 12


@pytest.mark.parametrize("item_diet,quantity,fields", [
    pytest.param("regular", 37.0, None, id="explicit-regular-matches-japanese-alias"),
    pytest.param(None, None, ["qty_per_serving", "unit_type"], id="missing-item-diet-not-regular"),
])
def test_explicit_diet_alias_does_not_supply_missing_item_identity(store, item_diet, quantity, fields):
    _seed_item("main", quantity=37, diet=item_diet)
    order = _order("常食")
    with _readonly(store, order):
        _assert_resolution(order, quantity=quantity, fields=fields)
