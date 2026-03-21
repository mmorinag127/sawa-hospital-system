import pathlib
import sys
from datetime import date
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, MenuMaster, MenuFacilityOverride
from src.models.facility import Facility, FacilityConfig
from src.models.user import AuditLog


def test_menu_upload_records_menu():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    content = b"menu\nMenuA\nMenuB\n"
    menu_service.create_menu("2025-12", content, "file.csv")
    items = menu_service.get_menu_items("2025-12")
    assert len(items) == 2
    assert all(item.get("menu_master_id") for item in items)

    first_name = items[0]["name"]
    with session_scope() as session:
        rows = session.query(MonthlyMenuItem).all()
        assert len(rows) == 2
        assert all(row.menu_master_id for row in rows)
        assert all(row.unit_type is None for row in rows)
        assert all(row.qty_per_serving is None for row in rows)
        master = session.get(MenuMaster, rows[0].menu_master_id)
        assert master is not None
        master.unit_type = "g"
        master.qty_per_serving = 80

    merged = menu_service.get_menu_items("2025-12")
    index = {item["name"]: item for item in merged}
    assert index[first_name]["unit_type"] == "g"
    assert index[first_name]["qty_per_serving"] == 80


def test_menu_upload_history_lists_and_downloads_saved_file(tmp_path):
    archive_path = tmp_path / "menu.csv"
    archive_path.write_bytes(b"menu-bytes")
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    content = b"menu\nMenuA\n"
    menu_service.create_menu(
        "2025-12",
        content,
        "file.csv",
        actor="tester",
        upload_metadata={"file_uri": str(archive_path), "content_sha256": "abc123"},
    )

    uploads = menu_service.list_menu_uploads("2025-12")
    assert len(uploads) == 1
    assert uploads[0]["filename"] == "file.csv"
    assert uploads[0]["download_available"] is True

    downloaded = menu_service.get_menu_upload_download("2025-12", uploads[0]["id"])
    assert downloaded is not None
    assert downloaded["download_available"] is True
    assert downloaded["bytes"] == b"menu-bytes"


def test_get_menu_uses_latest_upload_time_as_display_name():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    content = b"menu\nMenuA\n"
    menu_service.create_menu("2025-12", content, "file.csv")
    payload = menu_service.get_menu("2025-12")
    assert payload is not None
    menu = payload["menu"]
    assert menu["id"] == "2025-12"
    assert menu["filename"] == "file.csv"
    assert menu["uploaded_at"]
    assert menu["display_name"]
    assert "アップロード" in menu["display_name"]
    datetime.fromisoformat(menu["uploaded_at"])


def test_create_menu_scope_upload_replaces_only_target_scope(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    def _parse_base(*_args, **_kwargs):
        return None, None, [{"name": "BaseA"}, {"name": "BaseB"}], [
            {"menu_date": date(2026, 3, 22), "daypart": "朝", "name": "BaseA", "slot_index": 0},
            {"menu_date": date(2026, 3, 22), "daypart": "朝", "name": "BaseB", "slot_index": 1},
        ]

    def _parse_scoped(*_args, **_kwargs):
        return None, None, [{"name": "ScopedA"}, {"name": "ScopedB"}], [
            {"menu_date": date(2026, 3, 22), "daypart": "朝", "name": "ScopedA", "slot_index": 0},
            {"menu_date": date(2026, 3, 22), "daypart": "朝", "name": "ScopedB", "slot_index": 1},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_base)
    menu_service.create_menu("2026-03", b"dummy", "base.xlsx")
    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_scoped)
    menu_service.create_menu("2026-03", b"dummy", "facility.xlsx", scope_override="FAC00008")

    payload = menu_service.get_menu("2026-03")
    assert payload is not None
    all_entries = payload["entries"]
    assert any(entry["name"] == "BaseA" and not entry.get("facility_override") for entry in all_entries)
    assert any(entry["name"] == "ScopedA" and entry.get("facility_override") == "FAC00008" for entry in all_entries)


def test_get_menu_for_facility_resolves_tag_and_entry_scope():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(FacilityConfig).delete()
        session.query(Facility).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

        session.add(Facility(id="FACTAG01", name="Tag Facility"))
        session.add(
            FacilityConfig(
                facility_id="FACTAG01",
                config_json={"menu_override_tags": ["special-group"]},
            )
        )
        session.add(MonthlyMenu(id="2026-03", filename="base.xlsx"))
        session.add_all(
            [
                MonthlyMenuItem(
                    id="MMI_BASE",
                    monthly_menu_id="2026-03",
                    name="共通メニュー",
                    facility_override=None,
                ),
                MonthlyMenuItem(
                    id="MMI_TAG",
                    monthly_menu_id="2026-03",
                    name="タグ差分メニュー",
                    facility_override="TAG:special-group",
                ),
                MonthlyMenuEntry(
                    id="MME_BASE_1",
                    monthly_menu_id="2026-03",
                    menu_date=date(2026, 3, 22),
                    daypart="朝",
                    name="共通主菜",
                    slot_index=0,
                    facility_override=None,
                ),
                MonthlyMenuEntry(
                    id="MME_BASE_2",
                    monthly_menu_id="2026-03",
                    menu_date=date(2026, 3, 22),
                    daypart="朝",
                    name="共通副菜",
                    slot_index=1,
                    facility_override=None,
                ),
                MonthlyMenuEntry(
                    id="MME_TAG_1",
                    monthly_menu_id="2026-03",
                    menu_date=date(2026, 3, 22),
                    daypart="朝",
                    name="タグ主菜",
                    slot_index=0,
                    facility_override="TAG:special-group",
                ),
            ]
        )

    payload = menu_service.get_menu_for_facility("2026-03", "FACTAG01")
    assert payload is not None
    names = [entry["name"] for entry in payload["entries"]]
    assert names == ["タグ主菜", "共通副菜"]


def test_create_menu_dedupes_duplicate_names_within_single_upload(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    def _parse_duplicate(*_args, **_kwargs):
        return None, None, [{"name": "筑前煮"}, {"name": "筑前煮"}, {"name": "ほうれん草和え"}], [
            {"menu_date": date(2026, 3, 22), "daypart": "昼", "name": "筑前煮", "slot_index": 0},
            {"menu_date": date(2026, 3, 22), "daypart": "昼", "name": "筑前煮", "slot_index": 1},
            {"menu_date": date(2026, 3, 22), "daypart": "昼", "name": "ほうれん草和え", "slot_index": 2},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_duplicate)

    payload, replaced, item_count = menu_service.create_menu("2026-03", b"dummy", "dup.xlsx")

    assert replaced is False
    assert item_count == 2
    assert payload["id"] == "2026-03"

    with session_scope() as session:
        masters = session.query(MenuMaster).order_by(MenuMaster.name.asc()).all()
        items = session.query(MonthlyMenuItem).order_by(MonthlyMenuItem.name.asc()).all()
        assert [master.name for master in masters] == ["ほうれん草和え", "筑前煮"]
        assert [item.name for item in items] == ["ほうれん草和え", "筑前煮"]


def test_create_menu_normalizes_temp_type_from_japanese(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    def _parse_temp_type(*_args, **_kwargs):
        return None, None, [{"name": "筑前煮", "temp_type": "温"}], [
            {"menu_date": date(2026, 3, 22), "daypart": "昼", "name": "筑前煮", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_temp_type)

    menu_service.create_menu("2026-03", b"dummy", "temp.xlsx")
    payload = menu_service.get_menu("2026-03")

    assert payload is not None
    assert payload["items"][0]["temp_type"] == "hot"

    with session_scope() as session:
        master = session.query(MenuMaster).filter(MenuMaster.name == "筑前煮").first()
        assert master is not None
        assert master.temp_type == "hot"


def test_create_menu_normalizes_diet_type_from_japanese(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    def _parse_diet_type(*_args, **_kwargs):
        return None, None, [{"name": "筑前煮", "diet_type": "軟菜"}], [
            {
                "menu_date": date(2026, 3, 22),
                "daypart": "昼",
                "name": "筑前煮",
                "slot_index": 0,
                "diet_type": "軟菜",
            },
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_diet_type)

    menu_service.create_menu("2026-03", b"dummy", "diet.xlsx")
    payload = menu_service.get_menu("2026-03")

    assert payload is not None
    assert payload["items"][0]["diet_type"] == "soft"
    assert payload["entries"][0]["diet_type"] == "soft"


def test_menu_master_create_and_update_normalize_temp_type():
    with session_scope() as session:
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()

    created = menu_service.create_menu_master({"name": "冷やしトマト", "temp_type": "冷"})
    assert created["temp_type"] == "cold"

    updated = menu_service.update_menu_master(created["id"], {"temp_type": "温"})
    assert updated is True

    rows = menu_service.list_menu_masters(query="冷やしトマト")
    assert rows[0]["temp_type"] == "hot"


def test_list_menu_scope_options_collects_facilities_and_tags():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(FacilityConfig).delete()
        session.query(Facility).delete()
        session.add(Facility(id="FAC00008", name="池袋"))
        session.add(Facility(id="FAC00014", name="湘南さくら病院"))
        session.add(
            FacilityConfig(
                facility_id="FAC00008",
                config_json={"menu_override_tags": ["special-group", "west"]},
            )
        )
        session.add(
            FacilityConfig(
                facility_id="FAC00014",
                config_json={"menu_override_tags": ["special-group"]},
            )
        )

    payload = menu_service.list_menu_scope_options()
    facility_ids = [facility["id"] for facility in payload["facilities"]]
    assert facility_ids == ["FAC00008", "FAC00014"]
    tags = {item["value"]: item for item in payload["tags"]}
    assert sorted(tags.keys()) == ["special-group", "west"]
    assert tags["special-group"]["facility_count"] == 2
    assert tags["special-group"]["facility_ids"] == ["FAC00008", "FAC00014"]
