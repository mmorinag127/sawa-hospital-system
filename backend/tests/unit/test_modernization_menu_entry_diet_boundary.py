"""Owned SQLite entry/master identity components, not HTTP/native/live proof."""

import pytest
import sqlalchemy as sa

from test_modernization_monthly_item_identity import store as store
from src import db
from src.models.menu import MonthlyMenuItem
from src.monthly_menu_identity import IDENTITY_INDEX
from src.services import menu_service as service
from src.services.menu_vocabulary import normalize_diet_type


MONTH = "2026-10"
NAME = "Dish"


def seed(identifier, **fields):
    values = dict(id=identifier, monthly_menu_id=MONTH, name=NAME,
                  menu_master_id="master", qty_per_serving=0)
    values.update(fields)
    with db.session_scope() as session:
        session.add(MonthlyMenuItem(**values))


def items(session):
    return [dict(row) for row in session.execute(
        sa.select(MonthlyMenuItem.__table__).order_by(MonthlyMenuItem.id)
    ).mappings()]


def entry(session, **fields):
    values = dict(month_id=MONTH, name=NAME, facility_override="facility-a",
                  diet_type="regular", daypart="昼食", category="主菜")
    values.update(fields)
    return service._find_best_monthly_item_for_entry(session, **values)


@pytest.mark.parametrize("raw", [None, "", " \t ", "-", "()", "/"],
                         ids=["missing", "blank", "whitespace", "dash", "brackets", "slash"])
def test_unresolved_source_diet_stops_before_any_sql(raw):
    # Punctuation is discarded by the actual vocabulary; arbitrary unknown
    # text is intentionally NOT assumed invalid by this contract.
    assert normalize_diet_type(raw) is None

    class NoQuery:
        def execute(self, *args, **kwargs):
            pytest.fail("unresolved entry diet must stop before SQL")

    assert entry(NoQuery(), diet_type=raw) is None


@pytest.mark.parametrize("raw,canonical", [
    ("regular", "regular"), (" 常食 ", "regular"),
    ("soft", "soft"), ("軟菜", "soft"),
    ("mixer", "mixer"), ("ミキサー", "mixer"),
])
def test_explicit_normalized_diet_selects_only_exact_row_and_keeps_zero(store, raw, canonical):
    assert normalize_diet_type(raw) == canonical
    for diet in ("regular", "soft", "mixer", None):
        seed(diet or "unresolved", diet_type=diet, facility_override="facility-a",
             daypart="昼食", category="主菜")
    with db.session_scope() as session:
        before = items(session)
        found = entry(session, diet_type=raw)
        assert found is session.get(MonthlyMenuItem, canonical)
        assert (found.monthly_menu_id, found.name, found.facility_override,
                found.daypart, found.category, found.diet_type, found.qty_per_serving) == (
                    MONTH, NAME, "facility-a", "昼食", "主菜", canonical, 0)
        assert items(session) == before


@pytest.mark.parametrize("field,neighbor", [
    ("monthly_menu_id", "2026-11"), ("name", "Other dish"),
    ("facility_override", "facility-b"), ("daypart", "夕食"), ("category", "副菜1"),
])
def test_entry_never_substitutes_neighbor_identity(store, field, neighbor):
    values = dict(diet_type="regular", facility_override="facility-a",
                  daypart="昼食", category="主菜")
    seed("neighbor", **{**values, field: neighbor})
    with db.session_scope() as session:
        before = items(session)
        assert entry(session) is None
        requested = {"month_id" if field == "monthly_menu_id" else field: neighbor}
        assert entry(session, **requested) is session.get(MonthlyMenuItem, "neighbor")
        assert items(session) == before
    seed("own", **values)
    with db.session_scope() as session:
        before = items(session)
        assert entry(session).id == "own"
        assert entry(session).qty_per_serving == 0
        assert items(session) == before


def test_nullable_master_save_still_reuses_exact_null_identity(store):
    seed("null", unit_type="g", bag_max_qty=4, bag_max_unit="g")
    seed("regular", diet_type="regular", qty_per_serving=12)
    with db.session_scope() as session:
        before = items(session)
        found = service._find_existing_monthly_item(session, MONTH, NAME, None)
        assert found is session.get(MonthlyMenuItem, "null")
        assert found.diet_type is None and found.qty_per_serving == 0
    first = service.create_item_stub(MONTH, NAME)
    second = service.create_item_stub(MONTH, NAME)
    assert first == second
    assert (first["id"], first["diet_type"], first["qty_per_serving"],
            first["unit_type"], first["bag_max_qty"], first["bag_max_unit"]) == (
                "null", None, 0, "g", 4, "g")
    with db.session_scope() as session:
        assert items(session) == before


def test_nullable_master_alias_duplicate_is_still_rejected_by_owned_schema(store):
    seed("null")
    with db.session_scope() as session:
        before = items(session)
    with pytest.raises(sa.exc.IntegrityError):
        seed("empty", daypart="", category="", diet_type="", facility_override="")
    with db.session_scope() as session:
        assert items(session) == before


@pytest.mark.parametrize("diet", [None, "regular"], ids=["nullable-master", "resolved-entry"])
def test_legacy_duplicate_stops_without_choosing_first_or_changing_rows(store, diet):
    # Only this test's tmp_path-owned SQLite index is removed to represent
    # legacy corruption. No shared DB or production metadata is altered.
    with store.begin() as connection:
        connection.exec_driver_sql(f'DROP INDEX "{IDENTITY_INDEX}"')
    seed("duplicate-a", diet_type=diet)
    seed("duplicate-b", diet_type=diet)
    with db.session_scope() as session:
        before = items(session)
        assert len(before) == 2
        with pytest.raises(ValueError, match="^monthly_menu_item_identity_ambiguous$"):
            service._find_existing_monthly_item(session, MONTH, NAME, None, diet_type=diet)
        if diet is not None:
            with pytest.raises(ValueError, match="^monthly_menu_item_identity_ambiguous$"):
                entry(session, facility_override=None, daypart=None, category=None, diet_type=diet)
        assert items(session) == before
        assert [row["qty_per_serving"] for row in before] == [0, 0]
