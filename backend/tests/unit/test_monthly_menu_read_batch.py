"""Frozen b5dfd52 algorithm parity; no runtime Git history dependency."""
from datetime import date
from types import FunctionType

import pytest
from sqlalchemy import MetaData, UniqueConstraint, create_engine, event
from sqlalchemy.orm import Session

from src.services import menu_service as service
from src.models.menu import MenuMaster, MenuFacilityOverride, MonthlyMenuItem, MonthlyMenuEntry
from src.models.facility import FacilityConfig


def test_card_summary_matches_full_read_identity_count_without_quantity_reads(engine, monkeypatch):
    from contextlib import contextmanager
    from src.models.menu import MonthlyMenu
    from src.models.user import AuditLog
    metadata = MetaData()
    for model in (MenuMaster, MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, AuditLog):
        model.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    month = "2026-09"
    with Session(engine) as session:
        session.add(MonthlyMenu(id=month, month_start=date(2026, 9, 1), filename="owned-summary.xlsx"))
        session.flush()
        session.add(MonthlyMenuItem(id="base-summary", monthly_menu_id=month, name="base", daypart="昼",
            category="main", diet_type="regular", qty_per_serving=0))
        session.add(MonthlyMenuItem(id="scoped-summary", monthly_menu_id=month, name="scoped", daypart="昼",
            category="main", diet_type="regular", facility_override="A", qty_per_serving=999))
        for index in range(3):
            session.add(MonthlyMenuEntry(id=f"summary-entry-{index}", monthly_menu_id=month,
                name="entry-only", menu_date=date(2026, 9, index + 1), daypart="昼", diet_type="regular"))
        session.commit()
    @contextmanager
    def owned_read():
        with Session(engine) as session, session.no_autoflush:
            yield session
            assert not session.new and not session.dirty and not session.deleted
    monkeypatch.setattr(service, "ensure_menu_schema", lambda: None)
    monkeypatch.setattr(service, "_menu_read_session", owned_read)
    with Session(engine) as session:
        rows = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.facility_override.is_(None)).all()
        entries = session.query(MonthlyMenuEntry).all()
        full = service._normalize_menu_payload_items({"items": original(session, month, rows, entries)})
    statements = []
    def observe(_conn, _cursor, sql, *_args):
        statements.append(sql)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        values = service.get_menu_summaries([month, "2026-10"])
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert values[month]["item_count"] == len(full["items"]) == 2
    assert values[month]["menu"]["filename"] == "owned-summary.xlsx"
    assert values["2026-10"] == {"month_id": "2026-10", "status": "missing", "menu": None, "item_count": 0}
    assert len(statements) == 4
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not any("qty_per_serving" in sql or "menu_masters" in sql or "bag_max_qty" in sql for sql in statements)

def _original_monthly_items(session, month_id, items, entries, facility_id=None):
    payloads = [serialize_item(item) for item in items]
    keys = {_monthly_item_identity_key_from_item(item) for item in items}
    for entry in entries:
        key = _monthly_item_identity_key_from_entry(entry)
        # Facility selection already chose the authoritative scope for each identity.
        matched = key in keys or (facility_id and any(candidate[:4] == key[:4] for candidate in keys))
        generic_key = (key[0], "", "", key[3], key[4])
        if matched or (not key[2] and generic_key in keys):
            continue
        keys.add(key)
        payloads.append({
            "id": f"{_REPAIRED_MENU_ITEM_ID_PREFIX}{entry.id}",
            "month_id": month_id, "menu_master_id": None, "name": entry.name,
            "unit_type": None, "qty_per_serving": None, "temp_type": None,
            "daypart": _coerce_master_field_value("daypart", entry.daypart),
            "category": entry.category, "diet_type": normalize_diet_type(entry.diet_type),
            "facility_override": entry.facility_override, "master_resolution_mode": "month_only",
            "bag_max_qty": None, "bag_max_unit": None,
        })
    scope_ids = _resolve_override_scope_ids(session, facility_id) if facility_id else []
    for payload in payloads:
        payload_scope_ids = scope_ids if facility_id else _resolve_override_scope_ids(
            session, payload.get("facility_override")
        )
        missing = []
        master_id = payload.get("menu_master_id")
        if master_id:
            master = session.get(MenuMaster, master_id)
            if master is None:
                missing.append("menu_master_id")
            else:
                overrides = session.query(MenuFacilityOverride).filter(
                    MenuFacilityOverride.menu_master_id == master_id,
                    MenuFacilityOverride.facility_id.in_(payload_scope_ids),
                ).all() if payload_scope_ids else []
                candidates = []
                if overrides:
                    best = min(payload_scope_ids.index(row.facility_id) for row in overrides)
                    candidates = [row for row in overrides if payload_scope_ids.index(row.facility_id) == best]
                if len(candidates) > 1:
                    missing.append("ambiguous_master_override")
                else:
                    override = candidates[0] if candidates else None
                    for field in ("qty_per_serving", "unit_type", "temp_type", "bag_max_qty", "bag_max_unit", "condiments"):
                        if not _is_blank_value(payload.get(field)):
                            continue
                        value = getattr(override, field, None) if override is not None else None
                        if _is_blank_value(value):
                            value = getattr(master, field, None)
                        if not _is_blank_value(value):
                            payload[field] = value
        missing.extend(_unresolved_menu_fields(payload))
        if missing:
            payload["confirmation_required"] = True
            payload["unresolved_fields"] = missing
    return payloads


# Use unchanged production primitives for both algorithms, only the old loop is frozen.
original = FunctionType(_original_monthly_items.__code__, service.__dict__)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "batch.sqlite"))
    metadata = MetaData()
    for model in (MenuMaster, MenuFacilityOverride, FacilityConfig):
        table = model.__table__.to_metadata(metadata)
        # Represent a legacy overlapping override to exercise the existing blocker.
        for constraint in list(table.constraints):
            if isinstance(constraint, UniqueConstraint):
                table.constraints.remove(constraint)
    from src.models.facility import Facility
    Facility.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            FacilityConfig(facility_id="A", config_json={"menu_override_tag": "shared"}),
            FacilityConfig(facility_id="B", config_json={"menu_override_tag": "shared"}),
            MenuMaster(id="m", name="menu", normalized_name="menu", qty_per_serving=10,
                       unit_type="g", temp_type="hot", bag_max_qty=50, condiments=[{"name": "x"}]),
            MenuFacilityOverride(id="a", menu_master_id="m", facility_id="A", qty_per_serving=0),
            MenuFacilityOverride(id="tag", menu_master_id="m",
                                 facility_id=service._MENU_OVERRIDE_TAG_PREFIX + "shared",
                                 qty_per_serving=20),
        ])
        session.commit()
    yield engine
    engine.dispose()


def items(count=3):
    return [MonthlyMenuItem(id=str(i), monthly_menu_id="2026-09", name="menu",
            menu_master_id="m", diet_type="regular", daypart="昼", category="main",
            facility_override=("A" if i % 2 == 0 else "B"),
            qty_per_serving=(0 if i == 2 else None), bag_max_qty=(0 if i == 2 else None))
            for i in range(count)]


def run(engine, algorithm, rows, facility, entries=()):
    statements = []
    def track(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", track)
    try:
        with Session(engine) as session, session.no_autoflush:
            # Pending writes must remain pending throughout the read.
            session.add(MenuMaster(id="pending", name="pending", normalized_name="pending"))
            result = algorithm(session, "2026-09", rows, entries, facility)
            assert session.new
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        return result, statements
    finally:
        event.remove(engine, "before_cursor_execute", track)


@pytest.mark.parametrize("facility", ["A", "B", None])
@pytest.mark.parametrize("count", [3, 80])
def test_parity_and_bounded_queries(engine, facility, count):
    rows = items(count)
    entry = MonthlyMenuEntry(id="entry", monthly_menu_id="2026-09",
            menu_date=date(2026, 9, 13), name="new", daypart="昼", diet_type="regular")
    expected, old_sql = run(engine, original, rows, facility, [entry])
    actual, sql = run(engine, service._readonly_monthly_items, rows, facility, [entry])
    assert actual == expected
    assert len(sql) <= (3 if facility else 4)
    assert len(sql) < len(old_sql)
    assert actual[2]["qty_per_serving"] == 0
    assert actual[2]["bag_max_qty"] == 0
    assert actual[0]["qty_per_serving"] == (20 if facility == "B" else 0)
    assert actual[-1]["confirmation_required"]
    with pytest.raises(service.MenuReadUnresolved):
        service.require_resolved_menu_items(actual)


def test_missing_master_and_empty(engine):
    rows = items(1)
    rows[0].menu_master_id = "missing"
    expected, _ = run(engine, original, rows, None)
    actual, _ = run(engine, service._readonly_monthly_items, rows, None)
    assert actual == expected
    assert "menu_master_id" in actual[0]["unresolved_fields"]
    with pytest.raises(service.MenuReadUnresolved):
        service.require_resolved_menu_items(actual)
    assert run(engine, service._readonly_monthly_items, [], None) == ([], [])


def test_overlapping_best_scope_blocks(engine):
    with Session(engine) as session:
        session.add(MenuFacilityOverride(id="a2", menu_master_id="m", facility_id="A",
                                        qty_per_serving=999))
        session.commit()
    expected, _ = run(engine, original, items(), "A")
    actual, _ = run(engine, service._readonly_monthly_items, items(), "A")
    assert actual == expected
    assert "ambiguous_master_override" in actual[0]["unresolved_fields"]
    with pytest.raises(service.MenuReadUnresolved):
        service.require_resolved_menu_items(actual)


def test_duplicate_identity_blocker_retained(engine):
    rows = items(2)
    rows[1].facility_override = "A"
    with Session(engine) as session, session.no_autoflush:
        actual = service._readonly_monthly_items_with_scope_ambiguity(
            session, "2026-09", rows, [], "A")
    assert len(actual) == 1
    assert actual[0]["ambiguity"]["candidate_ids"] == ["0", "1"]
    with pytest.raises(service.MenuReadUnresolved):
        service.require_resolved_menu_items(actual)


def test_distinct_masters_and_next_call_reads_updates(engine):
    rows = items(80)
    with Session(engine) as session:
        for index, row in enumerate(rows):
            row.menu_master_id = f"m{index}"
            session.add(MenuMaster(id=row.menu_master_id, name=row.menu_master_id,
                                  normalized_name=row.menu_master_id,
                                  qty_per_serving=index, unit_type="g", temp_type="",
                                  bag_max_unit=None))
        session.commit()
    expected, _ = run(engine, original, rows, None)
    actual, sql = run(engine, service._readonly_monthly_items, rows, None)
    assert actual == expected
    assert len(sql) == 4
    assert [row["id"] for row in actual] == [str(i) for i in range(80)]
    with Session(engine) as session:
        session.get(MenuMaster, "m1").qty_per_serving = 42
        session.commit()
    updated, _ = run(engine, service._readonly_monthly_items, rows, None)
    expected_updated, _ = run(engine, original, rows, None)
    assert updated == expected_updated
    assert updated[1]["qty_per_serving"] == 42
    assert actual[1]["qty_per_serving"] == 1


@pytest.mark.parametrize("state", ["preloaded_deleted", "dirty", "expired_deleted", "expired", "pending", "deleted"])
def test_session_identity_semantics_match_original(engine, state):
    from sqlalchemy import delete, update

    # Separate sessions share the same database state but retain their own objects.
    with Session(engine) as baseline, Session(engine) as batched:
        held = []
        for session in (baseline, batched):
            if state == "pending":
                master = MenuMaster(id="new", name="new", normalized_name="new",
                                    qty_per_serving=17, unit_type="g")
                session.add(master)
            else:
                master = session.get(MenuMaster, "m")
                if state == "dirty":
                    master.qty_per_serving = 17
                elif state.startswith("expired"):
                    session.expire(master)
                elif state == "deleted":
                    session.delete(master)
            held.append(master)
        if state in {"preloaded_deleted", "expired_deleted"}:
            with engine.begin() as connection:
                connection.execute(delete(MenuMaster).where(MenuMaster.id == "m"))
        elif state == "expired":
            with engine.begin() as connection:
                connection.execute(update(MenuMaster).where(MenuMaster.id == "m").values(qty_per_serving=23))
        rows = items(1)
        rows[0].facility_override = None
        if state == "pending":
            rows[0].menu_master_id = "new"
        with baseline.no_autoflush, batched.no_autoflush:
            expected = original(baseline, "2026-09", rows, [], None)
            actual = service._readonly_monthly_items(batched, "2026-09", rows, [], None)
        assert actual == expected
        if state in {"expired_deleted", "pending"}:
            assert "menu_master_id" in actual[0]["unresolved_fields"]
        else:
            assert actual[0]["qty_per_serving"] == {"dirty": 17, "expired": 23}.get(state, 10)


@pytest.mark.parametrize("expired", [False, True])
def test_identity_map_batch_query_bound(engine, expired):
    rows = items(40)
    with Session(engine) as session:
        for index, row in enumerate(rows):
            row.menu_master_id = f"bound{index}"
            session.add(MenuMaster(id=row.menu_master_id, name=row.menu_master_id,
                                  normalized_name=row.menu_master_id,
                                  qty_per_serving=10, unit_type="g"))
        session.commit()
    with Session(engine) as session:
        held = session.query(MenuMaster).all()
        if expired:
            session.expire_all()
        statements = []
        def track(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(engine, "before_cursor_execute", track)
        try:
            with session.no_autoflush:
                actual = service._readonly_monthly_items(session, "2026-09", rows, [], "A")
        finally:
            event.remove(engine, "before_cursor_execute", track)
        assert len(actual) == 40
        assert len(held) == 41
        assert len(statements) == (3 if expired else 2)
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


@pytest.mark.parametrize("mutation", ["master", "facility_outside", "facility_other_payload", "facility_priority", "values"])
def test_dirty_override_sql_membership_matches_original(engine, mutation):
    with Session(engine) as session:
        session.add(MenuMaster(id="other", name="other", normalized_name="other",
                               qty_per_serving=77, unit_type="g"))
        session.commit()
    rows = items(3)
    rows[2].menu_master_id = "other"
    rows[2].qty_per_serving = None
    outcomes = []
    for algorithm in (original, service._readonly_monthly_items):
        with Session(engine) as session, session.no_autoflush:
            override = session.get(MenuFacilityOverride, "a")
            if mutation == "master":
                override.menu_master_id = "other"
            elif mutation == "facility_outside":
                override.facility_id = "outside"
            elif mutation == "facility_other_payload":
                override.facility_id = "B"
            elif mutation == "facility_priority":
                override.facility_id = service._MENU_OVERRIDE_TAG_PREFIX + "shared"
            else:
                override.qty_per_serving = 31
                override.unit_type = "count"
            try:
                outcomes.append((algorithm(session, "2026-09", rows, [], None), None))
            except Exception as exc:
                outcomes.append((None, type(exc)))
            assert override in session.dirty
    assert outcomes[0] == outcomes[1]
    result, error = outcomes[0]
    if mutation in {"facility_outside", "facility_other_payload"}:
        assert error is ValueError
    elif mutation == "facility_priority":
        assert error is None
        assert "ambiguous_master_override" in result[0]["unresolved_fields"]
    else:
        assert error is None
        assert result[0]["qty_per_serving"] == (31 if mutation == "values" else 0)
        assert result[1]["qty_per_serving"] == 20
        assert result[2]["qty_per_serving"] == 77
