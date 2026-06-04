import pathlib
import sys
from io import BytesIO
from datetime import date
from datetime import datetime

import pytest
from openpyxl import Workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.services import output_builder
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, MenuMaster, MenuFacilityOverride
from src.models.facility import Facility, FacilityConfig
from src.models.user import AuditLog


def test_infer_temp_type_covers_daily_label_main_dishes():
    assert menu_service._infer_temp_type("ポークチャップ") == "hot"  # noqa: SLF001
    assert menu_service._infer_temp_type("オムレツのカニ玉風") == "hot"  # noqa: SLF001


def test_resolve_menu_defaults_infers_temp_when_master_temp_is_blank():
    with session_scope() as session:
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.add(
            MenuMaster(
                id="master-pork-chap",
                name="ポークチャップ",
                normalized_name=menu_service._normalize_menu_name("ポークチャップ"),  # noqa: SLF001
                unit_type="g",
                qty_per_serving=100,
                temp_type=None,
            )
        )

    defaults = menu_service.resolve_menu_defaults(["ポークチャップ"])

    assert defaults["ポークチャップ"]["temp_type"] == "hot"
    assert defaults["ポークチャップ"]["unit_type"] == "g"


def _create_resolution(name: str, *, unit_type: str = "g", qty_per_serving: float = 40):
    return {
        "source_name": name,
        "action": "create",
        "unit_type": unit_type,
        "qty_per_serving": qty_per_serving,
    }


def test_menu_upload_records_menu():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    content = b"menu\nMenuA\nMenuB\n"
    menu_service.create_menu(
        "2025-12",
        content,
        "file.csv",
        menu_master_resolutions=[
            _create_resolution("MenuA"),
            _create_resolution("MenuB"),
        ],
    )
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


def test_list_recent_menus_returns_latest_registered_months():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenu).delete()
        session.add_all(
            [
                MonthlyMenu(id="2026-04", filename="april.xlsx", month_start=date(2026, 4, 1)),
                MonthlyMenu(id="2026-05", filename="may.xlsx", month_start=date(2026, 5, 1)),
                MonthlyMenu(id="2026-06", filename="june.xlsx", month_start=date(2026, 6, 1)),
            ]
        )

    rows = menu_service.list_recent_menus(limit=2)

    assert [row["id"] for row in rows] == ["2026-06", "2026-05"]
    assert rows[0]["month_start"] == "2026-06-01"
    assert rows[0]["filename"] == "june.xlsx"


def test_create_menu_blocks_file_month_mismatch(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    def _parse_april_menu(*_args, **_kwargs):
        return date(2026, 4, 1), None, [{"name": "April Menu"}], [
            {"menu_date": date(2026, 4, 26), "daypart": "朝", "name": "April Menu", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_april_menu)

    with pytest.raises(ValueError, match="menu_month_mismatch:2026-03!=2026-04"):
        menu_service.create_menu(
            "2026-03",
            b"dummy",
            "menu-2026-04.xlsx",
            menu_master_resolutions=[_create_resolution("April Menu")],
        )

    with session_scope() as session:
        assert session.get(MonthlyMenu, "2026-03") is None
        assert session.query(MonthlyMenuEntry).count() == 0


def test_monthly_menu_keeps_same_name_daypart_quantities_independent(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(
            MenuMaster(
                id="MNU_GERMAN_POTATO",
                name="ジャーマンポテト",
                normalized_name=menu_service._normalize_menu_name("ジャーマンポテト"),
                unit_type="g",
                qty_per_serving=40,
                daypart="昼食",
                category="副菜",
            )
        )

    def _parse_same_name_menu(*_args, **_kwargs):
        return date(2026, 6, 1), None, [
            {
                "name": "ジャーマンポテト",
                "daypart": "朝食",
                "category": "主菜",
                "unit_type": "g",
                "qty_per_serving": 70,
            },
            {
                "name": "ジャーマンポテト",
                "daypart": "昼食",
                "category": "副菜",
                "unit_type": "g",
                "qty_per_serving": 40,
            },
        ], [
            {
                "menu_date": date(2026, 6, 1),
                "daypart": "朝食",
                "name": "ジャーマンポテト",
                "category": "主菜",
                "slot_index": 0,
            },
            {
                "menu_date": date(2026, 6, 1),
                "daypart": "昼食",
                "name": "ジャーマンポテト",
                "category": "副菜",
                "slot_index": 1,
            },
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_same_name_menu)

    menu_service.create_menu("2026-06", b"dummy", "menu.xlsx")

    items = sorted(
        menu_service.get_menu_items("2026-06"),
        key=lambda item: ({"朝食": 0, "昼食": 1, "夕食": 2}.get(item["daypart"], 99), item["category"] or ""),
    )
    assert len(items) == 2
    assert [(item["daypart"], item["category"], item["qty_per_serving"]) for item in items] == [
        ("朝食", "主菜", 70.0),
        ("昼食", "副菜", 40.0),
    ]

    morning = next(item for item in items if item["daypart"] == "朝食")
    lunch = next(item for item in items if item["daypart"] == "昼食")
    assert menu_service.update_item_status(
        "2026-06",
        morning["id"],
        {
            "name": "ジャーマンポテト",
            "unit_type": "g",
            "qty_per_serving": 75,
            "daypart": "朝食",
            "category": "主菜",
        },
    ) == "updated"

    refreshed = {item["id"]: item for item in menu_service.get_menu_items("2026-06")}
    assert refreshed[morning["id"]]["qty_per_serving"] == 75
    assert refreshed[lunch["id"]]["qty_per_serving"] == 40


def test_get_menu_repairs_legacy_entries_without_matching_same_name_items():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(
            MenuMaster(
                id="MNU_LEGACY_GERMAN_POTATO",
                name="ジャーマンポテト",
                normalized_name=menu_service._normalize_menu_name("ジャーマンポテト"),
                unit_type="g",
                qty_per_serving=70,
                temp_type="hot",
                daypart="朝食",
                category="主菜",
            )
        )
        session.add(MonthlyMenu(id="2099-06", month_start=date(2099, 6, 1), filename="legacy.xlsx"))
        session.add(
            MonthlyMenuItem(
                id="MMI_LEGACY_MORNING",
                monthly_menu_id="2099-06",
                menu_master_id="MNU_LEGACY_GERMAN_POTATO",
                name="ジャーマンポテト",
                unit_type="g",
                qty_per_serving=70,
                temp_type="hot",
                daypart="朝食",
                category="主菜",
                diet_type="regular",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="MME_LEGACY_MORNING",
                    monthly_menu_id="2099-06",
                    menu_date=date(2099, 6, 14),
                    daypart="朝食",
                    name="ジャーマンポテト",
                    category="主菜",
                    diet_type="regular",
                    slot_index=1,
                ),
                MonthlyMenuEntry(
                    id="MME_LEGACY_DINNER",
                    monthly_menu_id="2099-06",
                    menu_date=date(2099, 6, 24),
                    daypart="夕食",
                    name="ジャーマンポテト",
                    category="副菜",
                    diet_type="regular",
                    slot_index=2,
                ),
            ]
        )

    payload = menu_service.get_menu("2099-06")

    assert payload is not None
    german_items = sorted(
        [item for item in payload["items"] if item["name"] == "ジャーマンポテト"],
        key=lambda item: (item["daypart"], item["category"]),
    )
    assert [(item["daypart"], item["category"], item["qty_per_serving"]) for item in german_items] == [
        ("夕食", "副菜", 40.0),
        ("朝食", "主菜", 70.0),
    ]
    repaired = next(item for item in german_items if item["daypart"] == "夕食")
    assert repaired["master_resolution_mode"] == "month_only"

    with session_scope() as session:
        rows = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == "2099-06").all()
        assert len(rows) == 2


def test_output_menu_overrides_select_same_name_by_daypart_and_category():
    lines = [
        {
            "date": date(2026, 6, 1),
            "daypart": "朝",
            "menu_name": "ジャーマンポテト",
            "menu_category": "主菜",
        },
        {
            "date": date(2026, 6, 1),
            "daypart": "昼",
            "menu_name": "ジャーマンポテト",
            "menu_category": "副菜",
        },
    ]
    menu_items = [
        {
            "id": "MMI_MORNING",
            "name": "ジャーマンポテト",
            "daypart": "朝食",
            "category": "主菜",
            "unit_type": "g",
            "qty_per_serving": 70,
        },
        {
            "id": "MMI_LUNCH",
            "name": "ジャーマンポテト",
            "daypart": "昼食",
            "category": "副菜",
            "unit_type": "g",
            "qty_per_serving": 40,
        },
    ]

    updated = output_builder._apply_menu_overrides(lines, menu_items)

    assert updated[0]["menu_qty_per_serving"] == 70
    assert updated[0]["_menu_qty_source_daypart"] == "朝食"
    assert updated[1]["menu_qty_per_serving"] == 40
    assert updated[1]["_menu_qty_source_daypart"] == "昼食"


def test_parse_monthly_menu_handles_single_day_final_week():
    workbook = Workbook()
    ws = workbook.active
    ws.title = "掲示用（料理・食品）"
    ws.append(["2026年5月"])
    ws.append(["", "日", "月", "火", "水", "木", "金", "土"])
    ws.append(["", 24, 25, 26, 27, 28, 29, 30])
    ws.append(["朝食", "ごはん"])
    ws.append(["", "いんげんと卵のソテー"])
    ws.append(["", "オニオンサラダ"])
    ws.append(["昼食", "ごはん"])
    ws.append(["", "豚肉の生姜炒め"])
    ws.append(["", "厚揚げの煮物"])
    ws.append(["", "オクラのおろし和え"])
    ws.append(["夕食", "ごはん"])
    ws.append(["", "豆腐ﾊﾝﾊﾞｰｸﾞ和風あん"])
    ws.append(["", "れんこんの甘辛煮"])
    ws.append(["", "南瓜サラダ"])
    ws.append(["", "ｴﾈﾙｷﾞｰ", "", 1292, "kcal"])
    ws.append(["", 31, "", "", "", "", "", ""])
    ws.append(["朝食", "ごはん"])
    ws.append(["", "さつま芋のスープ煮"])
    ws.append(["", "豆サラダ"])
    ws.append(["昼食", "ごはん"])
    ws.append(["", "照焼きハンバーグ　添)ﾌﾞﾛｯｺﾘｰ"])
    ws.append(["", "ジャーマンポテト"])
    ws.append(["", "ほうれん草の和え物"])
    ws.append(["夕食", "ごはん"])
    ws.append(["", "鶏すき焼き風"])
    ws.append(["", "ピーマンのじゃこ炒め"])
    ws.append(["", "大根なます"])

    content = BytesIO()
    workbook.save(content)

    _month_start, _diet_type, _items, entries = menu_service._parse_monthly_menu(  # noqa: SLF001
        content.getvalue(),
        "献立表(月間)2026.5月.xlsm",
        None,
        "2026-05",
    )

    evening_names_by_date = {
        target_date: [
            entry["name"]
            for entry in entries
            if entry["menu_date"] == target_date and entry["daypart"] == "夕食"
        ]
        for target_date in (date(2026, 5, 24), date(2026, 5, 31))
    }
    assert evening_names_by_date[date(2026, 5, 24)] == [
        "豆腐ﾊﾝﾊﾞｰｸﾞ和風あん",
        "れんこんの甘辛煮",
        "南瓜サラダ",
    ]
    assert evening_names_by_date[date(2026, 5, 31)] == [
        "鶏すき焼き風",
        "ピーマンのじゃこ炒め",
        "大根なます",
    ]


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
        menu_master_resolutions=[_create_resolution("MenuA")],
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
    menu_service.create_menu(
        "2025-12",
        content,
        "file.csv",
        menu_master_resolutions=[_create_resolution("MenuA")],
    )
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
    menu_service.create_menu(
        "2026-03",
        b"dummy",
        "base.xlsx",
        menu_master_resolutions=[_create_resolution("BaseA"), _create_resolution("BaseB")],
    )
    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_scoped)
    menu_service.create_menu(
        "2026-03",
        b"dummy",
        "facility.xlsx",
        scope_override="FAC00008",
        menu_master_resolutions=[_create_resolution("ScopedA"), _create_resolution("ScopedB")],
    )

    payload = menu_service.get_menu("2026-03")
    assert payload is not None
    base_entries = payload["entries"]
    assert any(entry["name"] == "BaseA" and not entry.get("facility_override") for entry in base_entries)
    assert not any(entry["name"] == "ScopedA" for entry in base_entries)

    scoped_payload = menu_service.get_menu_for_facility("2026-03", "FAC00008")
    assert scoped_payload is not None
    scoped_entries = scoped_payload["entries"]
    assert any(entry["name"] == "ScopedA" and entry.get("facility_override") == "FAC00008" for entry in scoped_entries)


def test_monthly_menu_review_upload_rejects_files_without_daily_entries(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    with pytest.raises(ValueError, match="monthly_menu_entries_missing"):
        menu_service.create_menu(
            "2026-06",
            b"menu\nMenuA\n",
            "menu.csv",
            require_menu_master_review=True,
        )

    assert menu_service.get_menu("2026-06") is None


def test_scoped_upload_does_not_pollute_base_menu_display_or_recent_list(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    menu_service.create_menu(
        "2099-06",
        b"menu\nScopedOnly\n",
        "scoped-only.csv",
        scope_override="TAG:codex-review",
        menu_master_resolutions=[_create_resolution("ScopedOnly")],
    )

    assert menu_service.get_menu("2099-06") is None
    assert [row["id"] for row in menu_service.list_recent_menus(limit=12)] == []


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


def test_get_menu_for_facility_preserves_multiple_base_entries_in_same_slot():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(FacilityConfig).delete()
        session.query(Facility).delete()
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == "2099-05").delete()
        session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == "2099-05").delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == "2099-05").delete()
        session.add(MonthlyMenu(id="2099-05", filename="base.xlsx"))
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="MME_DUP_SLOT_A",
                    monthly_menu_id="2099-05",
                    menu_date=date(2099, 5, 24),
                    daypart="夕",
                    name="豆腐ﾊﾝﾊﾞｰｸﾞ和風あん",
                    category="主菜",
                    diet_type="regular",
                    slot_index=1,
                ),
                MonthlyMenuEntry(
                    id="MME_DUP_SLOT_B",
                    monthly_menu_id="2099-05",
                    menu_date=date(2099, 5, 24),
                    daypart="夕",
                    name="鶏すき焼き風",
                    category="主菜",
                    diet_type="regular",
                    slot_index=1,
                ),
                MonthlyMenuEntry(
                    id="MME_DUP_SLOT_C",
                    monthly_menu_id="2099-05",
                    menu_date=date(2099, 5, 24),
                    daypart="夕",
                    name="南瓜サラダ",
                    category="副菜",
                    diet_type="regular",
                    slot_index=2,
                ),
            ]
        )

    payload = menu_service.get_menu_for_facility("2099-05", "FAC00008")

    assert payload is not None
    names = [
        entry["name"]
        for entry in payload["entries"]
        if entry["menu_date"] == "2099-05-24" and entry["daypart"] == "夕食"
    ]
    assert names == ["豆腐ﾊﾝﾊﾞｰｸﾞ和風あん", "鶏すき焼き風", "南瓜サラダ"]


def test_get_menu_for_facility_returns_entries_in_canonical_daypart_order():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == "2026-04").delete()
        session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == "2026-04").delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == "2026-04").delete()
        session.add(MonthlyMenu(id="2026-04", filename="base.xlsx"))
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="MME_ORDER_1",
                    monthly_menu_id="2026-04",
                    menu_date=date(2026, 4, 5),
                    daypart="夕",
                    name="夕メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="MME_ORDER_2",
                    monthly_menu_id="2026-04",
                    menu_date=date(2026, 4, 5),
                    daypart="昼",
                    name="昼メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="MME_ORDER_3",
                    monthly_menu_id="2026-04",
                    menu_date=date(2026, 4, 5),
                    daypart="朝",
                    name="朝メニューA",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="MME_ORDER_4",
                    monthly_menu_id="2026-04",
                    menu_date=date(2026, 4, 5),
                    daypart="朝",
                    name="朝メニューB",
                    slot_index=1,
                ),
            ]
        )

    payload = menu_service.get_menu_for_facility("2026-04", None)
    assert payload is not None
    rows = [(entry["daypart"], entry["name"]) for entry in payload["entries"]]
    assert rows == [
        ("朝食", "朝メニューA"),
        ("朝食", "朝メニューB"),
        ("昼食", "昼メニュー"),
        ("夕食", "夕メニュー"),
    ]


def test_get_menu_canonicalizes_requested_month_when_resolved_from_previous_month():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id.in_(["2099-03", "2099-04"])).delete()
        session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id.in_(["2099-03", "2099-04"])).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id.in_(["2099-03", "2099-04"])).delete()
        session.add(MonthlyMenu(id="2099-03", filename="seed-2099-03.xlsx"))
        session.add(
            MonthlyMenuEntry(
                id="MME_CANONICAL_1",
                monthly_menu_id="2099-03",
                menu_date=date(2099, 4, 5),
                daypart="朝",
                name="Covered Menu A",
                slot_index=0,
            )
        )

    payload = menu_service.get_menu("2099-04")
    assert payload is not None
    assert payload["menu"]["id"] == "2099-04"
    assert payload["menu"]["requested_month_id"] == "2099-04"
    assert payload["menu"]["source_month_id"] == "2099-03"
    assert payload["entries"][0]["month_id"] == "2099-04"
    assert payload["entries"][0]["requested_month_id"] == "2099-04"
    assert payload["entries"][0]["source_month_id"] == "2099-03"
    assert payload["entries"][0]["menu_date"] == "2099-04-05"


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

    payload, replaced, item_count = menu_service.create_menu(
        "2026-03",
        b"dummy",
        "dup.xlsx",
        menu_master_resolutions=[_create_resolution("筑前煮"), _create_resolution("ほうれん草和え")],
    )

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

    menu_service.create_menu(
        "2026-03",
        b"dummy",
        "temp.xlsx",
        menu_master_resolutions=[_create_resolution("筑前煮")],
    )
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

    menu_service.create_menu(
        "2026-03",
        b"dummy",
        "diet.xlsx",
        menu_master_resolutions=[_create_resolution("筑前煮")],
    )
    payload = menu_service.get_menu("2026-03")

    assert payload is not None
    assert payload["items"][0]["diet_type"] == "soft"
    assert payload["entries"][0]["diet_type"] == "soft"


def test_create_menu_requires_review_for_unknown_menu_candidates(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(
            MenuMaster(
                id="MNUFISH01",
                name="白身魚フライ",
                normalized_name=menu_service._normalize_menu_name("白身魚フライ"),
                unit_type="count",
                qty_per_serving=1,
            )
        )

    def _parse_unknown(*_args, **_kwargs):
        return None, None, [{"name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ"}], [
            {"menu_date": date(2026, 3, 24), "daypart": "夕", "name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_unknown)

    try:
        menu_service.create_menu("2026-03", b"dummy", "menu.xlsx", require_menu_master_review=True)
    except menu_service.MenuMasterResolutionRequired as exc:
        assert exc.issues[0]["source_name"] == "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ"
        assert exc.issues[0]["reason"] == "candidate_review_required"
        assert exc.issues[0]["candidates"][0]["id"] == "MNUFISH01"
    else:
        raise AssertionError("expected MenuMasterResolutionRequired")


def test_create_menu_allows_existing_resolution_without_duplicate_master(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(
            MenuMaster(
                id="MNUFISH02",
                name="白身魚フライ",
                normalized_name=menu_service._normalize_menu_name("白身魚フライ"),
                unit_type="count",
                qty_per_serving=1,
            )
        )

    def _parse_unknown(*_args, **_kwargs):
        return None, None, [{"name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ"}], [
            {"menu_date": date(2026, 3, 24), "daypart": "夕", "name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_unknown)

    menu_service.create_menu(
        "2026-03",
        b"dummy",
        "menu.xlsx",
        menu_master_resolutions=[
            {
                "source_name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ",
                "action": "existing",
                "menu_master_id": "MNUFISH02",
            }
        ],
    )

    with session_scope() as session:
        masters = session.query(MenuMaster).order_by(MenuMaster.name.asc()).all()
        items = session.query(MonthlyMenuItem).all()
        assert len(masters) == 1
        assert masters[0].id == "MNUFISH02"
        assert len(items) == 1
        assert items[0].menu_master_id == "MNUFISH02"
        assert items[0].name == "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ"


def test_create_menu_allows_create_resolution_with_unit_and_qty(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    def _parse_unknown(*_args, **_kwargs):
        return None, None, [{"name": "タラのムニエル", "daypart": "夕", "category": "主菜"}], [
            {"menu_date": date(2026, 3, 24), "daypart": "夕", "name": "タラのムニエル", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_unknown)

    menu_service.create_menu(
        "2026-03",
        b"dummy",
        "menu.xlsx",
        menu_master_resolutions=[
            {
                "source_name": "タラのムニエル",
                "action": "create",
                "unit_type": "cut",
                "qty_per_serving": 2,
            }
        ],
    )

    with session_scope() as session:
        master = session.query(MenuMaster).filter(MenuMaster.name == "タラのムニエル").first()
        item = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.name == "タラのムニエル").first()
        assert master is not None
        assert master.unit_type == "cut"
        assert master.qty_per_serving == 2
        assert master.daypart == "夕食"
        assert master.category == "主菜"
        assert item is not None
        assert item.menu_master_id == master.id


def test_get_menu_exposes_menu_master_missing_and_diff_checks():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(MonthlyMenu(id="2026-03", filename="menu.xlsx"))
        session.add(
            MenuMaster(
                id="MNU_EXIST",
                name="白身魚フライ",
                normalized_name=menu_service._normalize_menu_name("白身魚フライ"),
                unit_type="g",
                qty_per_serving=100,
                daypart="夕食",
                category="主菜",
            )
        )
        session.add_all(
            [
                MonthlyMenuItem(
                    id="MMI_DIFF",
                    monthly_menu_id="2026-03",
                    menu_master_id="MNU_EXIST",
                    name="白身魚フライ",
                    unit_type="count",
                    qty_per_serving=1,
                    daypart="夕食",
                    category="主菜",
                ),
                MonthlyMenuItem(
                    id="MMI_MISSING",
                    monthly_menu_id="2026-03",
                    name="タラのムニエル",
                    unit_type="cut",
                    qty_per_serving=2,
                    daypart="夕食",
                    category="主菜",
                ),
                MonthlyMenuEntry(
                    id="MME_DIFF",
                    monthly_menu_id="2026-03",
                    menu_date=date(2026, 3, 24),
                    daypart="夕",
                    name="白身魚フライ",
                    category="主菜",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="MME_MISSING",
                    monthly_menu_id="2026-03",
                    menu_date=date(2026, 3, 24),
                    daypart="夕",
                    name="タラのムニエル",
                    category="主菜",
                    slot_index=1,
                ),
            ]
        )

    payload = menu_service.get_menu("2026-03")

    assert payload is not None
    master_checks = payload["master_checks"]
    assert master_checks["count"] == 2
    issues = {issue["item_id"]: issue for issue in master_checks["issues"]}
    assert issues["MMI_DIFF"]["issue_type"] == "bagging_settings_missing"
    assert issues["MMI_DIFF"]["current_master"]["id"] == "MNU_EXIST"
    assert {diff["field"] for diff in issues["MMI_DIFF"]["field_diffs"]} == {"bag_max_qty", "bag_max_unit"}
    assert issues["MMI_MISSING"]["issue_type"] == "missing"
    assert issues["MMI_MISSING"]["suggested_patch"]["unit_type"] == "cut"
    assert issues["MMI_MISSING"]["suggested_patch"]["qty_per_serving"] == 2


def test_get_menu_ignores_daypart_and_category_variants_in_master_checks():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(MonthlyMenu(id="2026-04", filename="menu.xlsx"))
        session.add(
            MenuMaster(
                id="MNU_VARIANT",
                name="ほうれん草の和え物",
                normalized_name=menu_service._normalize_menu_name("ほうれん草の和え物"),
                unit_type="g",
                qty_per_serving=40,
                daypart="夕食",
                category="副菜（冷菜）",
            )
        )
        session.add_all(
            [
                MonthlyMenuItem(
                    id="MMI_VARIANT",
                    monthly_menu_id="2026-04",
                    menu_master_id="MNU_VARIANT",
                    name="ほうれん草の和え物",
                    unit_type="g",
                    qty_per_serving=40,
                    daypart="昼食",
                    category="副菜",
                ),
                MonthlyMenuEntry(
                    id="MME_VARIANT",
                    monthly_menu_id="2026-04",
                    menu_date=date(2026, 4, 1),
                    daypart="昼",
                    name="ほうれん草の和え物",
                    category="副菜",
                    slot_index=0,
                ),
            ]
        )

    payload = menu_service.get_menu("2026-04")

    assert payload is not None
    master_checks = payload["master_checks"]
    assert master_checks["count"] == 0
    assert master_checks["issues"] == []


def test_resolve_menu_master_check_can_link_existing_create_update_month_only_and_category_only():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(MonthlyMenu(id="2026-03", filename="menu.xlsx"))
        session.add_all(
            [
                MenuMaster(
                    id="MNU_LINK",
                    name="白身魚フライ",
                    normalized_name=menu_service._normalize_menu_name("白身魚フライ"),
                    unit_type="count",
                    qty_per_serving=1,
                    daypart="夕食",
                    category="主菜",
                ),
                MenuMaster(
                    id="MNU_UPDATE",
                    name="サバの塩焼き",
                    normalized_name=menu_service._normalize_menu_name("サバの塩焼き"),
                    unit_type="g",
                    qty_per_serving=100,
                    daypart="夕食",
                    category="主菜",
                ),
                MonthlyMenuItem(
                    id="MMI_LINK",
                    monthly_menu_id="2026-03",
                    name="白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ",
                ),
                MonthlyMenuItem(
                    id="MMI_CREATE",
                    monthly_menu_id="2026-03",
                    name="タラのムニエル",
                    unit_type="cut",
                    qty_per_serving=2,
                    daypart="夕食",
                    category="主菜",
                ),
                MonthlyMenuItem(
                    id="MMI_UPDATE",
                    monthly_menu_id="2026-03",
                    menu_master_id="MNU_UPDATE",
                    name="サバの塩焼き",
                    unit_type="cut",
                    qty_per_serving=2,
                    daypart="夕食",
                    category="主菜",
                ),
                MonthlyMenuItem(
                    id="MMI_MONTH_ONLY",
                    monthly_menu_id="2026-03",
                    menu_master_id="MNU_UPDATE",
                    name="サバの塩焼き",
                    unit_type="cut",
                    qty_per_serving=2,
                    daypart="夕食",
                    category="主菜",
                    facility_override="FAC_MONTH_ONLY",
                ),
                MonthlyMenuItem(
                    id="MMI_CATEGORY_ONLY",
                    monthly_menu_id="2026-03",
                    menu_master_id="MNU_UPDATE",
                    name="サバの塩焼き",
                    unit_type="g",
                    qty_per_serving=100,
                    daypart="夕食",
                    category="主菜（焼魚）",
                    facility_override="FAC_CATEGORY_ONLY",
                ),
            ]
        )

    linked = menu_service.resolve_menu_master_check(
        "2026-03",
        "MMI_LINK",
        {
            "action": "existing",
            "menu_master_id": "MNU_LINK",
        },
    )
    created = menu_service.resolve_menu_master_check(
        "2026-03",
        "MMI_CREATE",
        {
            "action": "create",
            "name": "タラのムニエル",
            "unit_type": "cut",
            "qty_per_serving": 2,
            "bag_max_qty": 20,
            "bag_max_unit": "cut",
            "daypart": "夕食",
            "category": "主菜",
        },
    )
    updated = menu_service.resolve_menu_master_check(
        "2026-03",
        "MMI_UPDATE",
        {
            "action": "update",
            "bag_max_qty": 20,
            "bag_max_unit": "cut",
        },
    )
    month_only = menu_service.resolve_menu_master_check(
        "2026-03",
        "MMI_MONTH_ONLY",
        {
            "action": "month_only",
            "unit_type": "cut",
            "qty_per_serving": 2,
            "bag_max_qty": 20,
            "bag_max_unit": "cut",
            "category": "主菜",
        },
    )
    category_only = menu_service.resolve_menu_master_check(
        "2026-03",
        "MMI_CATEGORY_ONLY",
        {
            "action": "category_only",
            "category": "主菜（焼魚）",
        },
    )

    assert linked == {"resolved": True, "mode": "existing", "menu_master_id": "MNU_LINK"}
    assert created["resolved"] is True
    assert created["mode"] == "create"
    assert updated == {"resolved": True, "mode": "update", "menu_master_id": "MNU_UPDATE"}
    assert month_only == {"resolved": True, "mode": "month_only", "menu_master_id": "MNU_UPDATE"}
    assert category_only == {"resolved": True, "mode": "category_only", "menu_master_id": "MNU_UPDATE"}

    with session_scope() as session:
        item_link = session.get(MonthlyMenuItem, "MMI_LINK")
        item_create = session.get(MonthlyMenuItem, "MMI_CREATE")
        item_update = session.get(MonthlyMenuItem, "MMI_UPDATE")
        item_month_only = session.get(MonthlyMenuItem, "MMI_MONTH_ONLY")
        item_category_only = session.get(MonthlyMenuItem, "MMI_CATEGORY_ONLY")
        created_master = session.get(MenuMaster, created["menu_master_id"])
        updated_master = session.get(MenuMaster, "MNU_UPDATE")
        assert item_link is not None and item_link.menu_master_id == "MNU_LINK"
        assert item_create is not None and item_create.menu_master_id == created["menu_master_id"]
        assert created_master is not None
        assert created_master.name == "タラのムニエル"
        assert created_master.unit_type == "cut"
        assert created_master.qty_per_serving == 2
        assert created_master.bag_max_qty == 20
        assert created_master.bag_max_unit == "cut"
        assert updated_master is not None
        assert updated_master.unit_type == "cut"
        assert updated_master.qty_per_serving == 2
        assert updated_master.bag_max_qty == 20
        assert updated_master.bag_max_unit == "cut"
        assert updated_master.category == "主菜（焼魚）"
        assert item_update is not None and item_update.menu_master_id == "MNU_UPDATE"
        assert item_month_only is not None
        assert item_month_only.menu_master_id == "MNU_UPDATE"
        assert item_month_only.master_resolution_mode == "month_only"
        assert item_month_only.unit_type == "cut"
        assert item_month_only.qty_per_serving == 2
        assert item_month_only.bag_max_qty == 20
        assert item_month_only.bag_max_unit == "cut"
        assert item_month_only.category == "主菜"
        assert item_category_only is not None
        assert item_category_only.master_resolution_mode is None

    payload = menu_service.get_menu("2026-03")
    assert payload is not None
    issues = {issue["item_id"]: issue for issue in payload["master_checks"]["issues"]}
    assert "MMI_MONTH_ONLY" not in issues


def test_resolve_menu_master_check_update_keeps_reference_daypart_and_category_variant():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(MonthlyMenu(id="2026-04", filename="menu.xlsx"))
        session.add(
            MenuMaster(
                id="MNU_KEEP_REF",
                name="ほうれん草の和え物",
                normalized_name=menu_service._normalize_menu_name("ほうれん草の和え物"),
                unit_type="g",
                qty_per_serving=40,
                daypart="夕食",
                category="副菜（冷菜）",
            )
        )
        session.add(
            MonthlyMenuItem(
                id="MMI_KEEP_REF",
                monthly_menu_id="2026-04",
                menu_master_id="MNU_KEEP_REF",
                name="ほうれん草の和え物",
                unit_type="g",
                qty_per_serving=50,
                daypart="昼食",
                category="副菜",
            )
        )

    updated = menu_service.resolve_menu_master_check(
        "2026-04",
        "MMI_KEEP_REF",
        {
            "action": "update",
        },
    )

    assert updated == {"resolved": True, "mode": "update", "menu_master_id": "MNU_KEEP_REF"}

    with session_scope() as session:
        master = session.get(MenuMaster, "MNU_KEEP_REF")
        assert master is not None
        assert master.qty_per_serving == 50
        assert master.daypart == "夕食"
        assert master.category == "副菜（冷菜）"


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


def test_menu_master_create_and_update_normalize_count_and_cut_units():
    with session_scope() as session:
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()

    created = menu_service.create_menu_master(
        {
            "name": "白身魚フライ",
            "unit_type": "切れ",
            "qty_per_serving": 1,
            "bag_max_qty": 5,
            "bag_max_unit": "個",
        }
    )
    assert created["unit_type"] == "cut"
    assert created["bag_max_unit"] == "count"

    updated = menu_service.update_menu_master(
        created["id"],
        {
            "unit_type": "個数",
            "bag_max_unit": "枚",
        },
    )
    assert updated is True

    rows = menu_service.list_menu_masters(query="白身魚フライ")
    assert rows[0]["unit_type"] == "count"
    assert rows[0]["bag_max_unit"] == "cut"


def test_monthly_menu_item_update_normalizes_count_and_cut_units():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    menu_service.create_menu(
        "2026-03",
        "menu\n白身魚フライ\n".encode("utf-8"),
        "menu.csv",
        menu_master_resolutions=[_create_resolution("白身魚フライ", unit_type="count", qty_per_serving=1)],
    )
    item = menu_service.get_menu_items("2026-03")[0]

    result = menu_service.update_item_status(
        "2026-03",
        item["id"],
        {
            "unit_type": "切れ",
            "qty_per_serving": 1,
        },
    )
    assert result == "updated"

    updated_items = menu_service.get_menu_items("2026-03")
    assert updated_items[0]["unit_type"] == "cut"
    assert updated_items[0]["qty_per_serving"] == 1

    with session_scope() as session:
        refreshed_item = session.get(MonthlyMenuItem, item["id"])
        assert refreshed_item is not None
        master = session.get(MenuMaster, refreshed_item.menu_master_id)
        assert master is not None
        assert master.unit_type == "cut"


def test_menu_master_create_and_update_normalize_unit_type():
    with session_scope() as session:
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()

    created = menu_service.create_menu_master(
        {
            "name": "白身魚のフライ",
            "unit_type": "切",
            "bag_max_unit": "個",
        }
    )
    assert created["unit_type"] == "cut"
    assert created["bag_max_unit"] == "count"

    updated = menu_service.update_menu_master(
        created["id"],
        {
            "unit_type": "枚",
            "bag_max_unit": "count",
        },
    )
    assert updated is True

    rows = menu_service.list_menu_masters(query="白身魚のフライ")
    assert rows[0]["unit_type"] == "cut"
    assert rows[0]["bag_max_unit"] == "count"

    with session_scope() as session:
        master = session.get(MenuMaster, created["id"])
        assert master is not None
        assert master.unit_type == "cut"
        assert master.bag_max_unit == "count"


def test_resolve_menu_master_check_month_only_defaults_missing_qty_to_master_value():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(MonthlyMenu(id="2026-05", filename="menu.xlsx"))
        session.add(
            MenuMaster(
                id="MNU_MONTH_DEFAULT",
                name="じゃが芋のコンソメ煮",
                normalized_name=menu_service._normalize_menu_name("じゃが芋のコンソメ煮"),
                unit_type="g",
                qty_per_serving=40,
                daypart="朝食",
                category="副菜",
            )
        )
        session.add(
            MonthlyMenuItem(
                id="MMI_MONTH_DEFAULT",
                monthly_menu_id="2026-05",
                menu_master_id="MNU_MONTH_DEFAULT",
                name="じゃが芋のコンソメ煮",
                daypart="朝食",
                category="主菜",
                facility_override="FAC_MONTH_DEFAULT",
            )
        )

    resolved = menu_service.resolve_menu_master_check(
        "2026-05",
        "MMI_MONTH_DEFAULT",
        {
            "action": "month_only",
            "unit_type": "g",
            "category": "主菜",
        },
    )

    assert resolved == {"resolved": True, "mode": "month_only", "menu_master_id": "MNU_MONTH_DEFAULT"}

    with session_scope() as session:
        item = session.get(MonthlyMenuItem, "MMI_MONTH_DEFAULT")
        assert item is not None
        assert item.master_resolution_mode == "month_only"
        assert item.unit_type == "g"
        assert item.qty_per_serving == 40
        assert item.category == "主菜"


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


def test_upsert_entry_exceptions_creates_facility_scoped_override_entries():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(FacilityConfig).delete()
        session.query(Facility).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(Facility(id="FACEX001", name="例外施設A"))
        session.add(Facility(id="FACEX002", name="例外施設B"))
        session.add(MonthlyMenu(id="2026-06", filename="menu.xlsx"))
        session.add(
            MenuMaster(
                id="MNU_BASE_EXCEPTION",
                name="鶏の照焼き",
                normalized_name=menu_service._normalize_menu_name("鶏の照焼き"),
                unit_type="g",
                qty_per_serving=80,
                daypart="昼",
                category="主菜",
            )
        )
        session.add(
            MonthlyMenuItem(
                id="MMI_BASE_EXCEPTION",
                monthly_menu_id="2026-06",
                menu_master_id="MNU_BASE_EXCEPTION",
                name="鶏の照焼き",
                unit_type="g",
                qty_per_serving=80,
                daypart="昼",
                category="主菜",
            )
        )
        session.add(
            MonthlyMenuEntry(
                id="MME_BASE_EXCEPTION",
                monthly_menu_id="2026-06",
                menu_date=date(2026, 6, 3),
                daypart="昼",
                slot_index=1,
                name="鶏の照焼き",
                category="主菜",
                diet_type="",
            )
        )

    payload = menu_service.upsert_entry_exceptions(
        "2026-06",
        "MME_BASE_EXCEPTION",
        {
            "facility_ids": ["FACEX001"],
            "name": "鮭の塩焼き",
            "unit_type": "cut",
            "qty_per_serving": 1,
            "category": "主菜",
        },
    )

    assert payload is not None
    assert payload["updated"] is True
    assert payload["facility_ids"] == ["FACEX001"]
    assert payload["entries"][0]["facility_override"] == "FACEX001"
    assert payload["entries"][0]["name"] == "鮭の塩焼き"
    assert payload["items"][0]["facility_override"] == "FACEX001"
    assert payload["items"][0]["name"] == "鮭の塩焼き"

    scoped_payload = menu_service.get_menu_for_facility("2026-06", "FACEX001")
    assert scoped_payload is not None
    scoped_entries = {(entry["menu_date"], entry["daypart"], entry["slot_index"]): entry for entry in scoped_payload["entries"]}
    assert scoped_entries[("2026-06-03", "昼食", 1)]["name"] == "鮭の塩焼き"

    base_payload = menu_service.get_menu_for_facility("2026-06", "FACEX002")
    assert base_payload is not None
    base_entries = {(entry["menu_date"], entry["daypart"], entry["slot_index"]): entry for entry in base_payload["entries"]}
    assert base_entries[("2026-06-03", "昼食", 1)]["name"] == "鶏の照焼き"


def test_monthly_menu_upload_allows_cut_units_and_reports_bagging_gap_after_upload(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()

    def _parse_cut_menu(*_args, **_kwargs):
        return None, None, [{"name": "鮭切身", "unit_type": "cut", "qty_per_serving": 1}], [
            {"menu_date": date(2026, 7, 1), "daypart": "昼", "name": "鮭切身", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_cut_menu)

    try:
        menu_service.create_menu(
            "2026-07",
            b"dummy",
            "menu.xlsx",
            require_menu_master_review=True,
        )
        assert False, "expected menu master review"
    except menu_service.MenuMasterResolutionRequired as exc:
        assert exc.issues[0]["reason"] == "missing"

    menu_service.create_menu(
        "2026-07",
        b"dummy",
        "menu.xlsx",
        require_menu_master_review=True,
        menu_master_resolutions=[
            {
                "source_name": "鮭切身",
                "action": "create",
                "unit_type": "cut",
                "qty_per_serving": 1,
            }
        ],
    )

    payload = menu_service.get_menu("2026-07")
    assert payload is not None
    item = payload["items"][0]
    assert item["unit_type"] == "cut"
    assert item["bag_max_qty"] is None
    assert item["bag_max_unit"] is None
    assert payload["master_checks"]["issues"][0]["reason"] == "bagging_settings_missing"


def test_monthly_menu_upload_allows_existing_master_bagging_gap_and_reports_after_upload(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(
            MenuMaster(
                id="MNU_EXISTING_CUT",
                name="鮭切身",
                normalized_name=menu_service._normalize_menu_name("鮭切身"),
                unit_type="cut",
                qty_per_serving=1,
            )
        )

    def _parse_existing_cut_menu(*_args, **_kwargs):
        return None, None, [{"name": "鮭切身", "unit_type": "cut", "qty_per_serving": 1}], [
            {"menu_date": date(2026, 7, 1), "daypart": "昼", "name": "鮭切身", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_existing_cut_menu)

    menu_service.create_menu(
        "2026-07",
        b"dummy",
        "menu.xlsx",
        require_menu_master_review=True,
    )

    payload = menu_service.get_menu("2026-07")
    assert payload is not None
    item = payload["items"][0]
    assert item["menu_master_id"] == "MNU_EXISTING_CUT"
    assert item["bag_max_qty"] is None
    assert item["bag_max_unit"] is None
    assert payload["master_checks"]["issues"][0]["reason"] == "bagging_settings_missing"
    with session_scope() as session:
        master = session.get(MenuMaster, "MNU_EXISTING_CUT")
        assert master is not None
        assert master.bag_max_qty is None
        assert master.bag_max_unit is None


def test_monthly_menu_lists_existing_condiment_bagging_gaps():
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(MonthlyMenu(id="2026-08", filename="menu.xlsx"))
        session.add(
            MenuMaster(
                id="MNU_SAUCE_MAIN",
                name="白身魚フライ",
                normalized_name=menu_service._normalize_menu_name("白身魚フライ"),
                unit_type="count",
                qty_per_serving=1,
                bag_max_qty=25,
                bag_max_unit="count",
                condiments=["ソース"],
            )
        )
        session.add(
            MonthlyMenuItem(
                id="MMI_SAUCE_MAIN",
                monthly_menu_id="2026-08",
                menu_master_id="MNU_SAUCE_MAIN",
                name="白身魚フライ",
                unit_type="count",
                qty_per_serving=1,
                bag_max_qty=25,
                bag_max_unit="count",
            )
        )
        session.add(
            MonthlyMenuEntry(
                id="MME_SAUCE_MAIN",
                monthly_menu_id="2026-08",
                menu_date=date(2026, 8, 1),
                daypart="昼",
                name="白身魚フライ",
                slot_index=0,
            )
        )

    payload = menu_service.get_menu("2026-08")
    assert payload is not None
    issues = payload["master_checks"]["issues"]
    assert len(issues) == 1
    assert issues[0]["reason"] == "bagging_settings_missing"
    assert issues[0]["condiment_issues"][0]["condiment_name"] == "ソース"

    with session_scope() as session:
        session.add(
            MenuMaster(
                id="MNU_SAUCE",
                name="ソース",
                normalized_name=menu_service._normalize_menu_name("ソース"),
                unit_type="count",
                qty_per_serving=1,
                bag_max_qty=50,
                bag_max_unit="count",
            )
        )

    payload = menu_service.get_menu("2026-08")
    assert payload is not None
    assert payload["master_checks"]["issues"] == []


def test_monthly_menu_upload_reports_condiment_bagging_gap_after_upload(monkeypatch):
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
        session.add(
            MenuMaster(
                id="MNU_WITH_SAUCE",
                name="白身魚フライ",
                normalized_name=menu_service._normalize_menu_name("白身魚フライ"),
                unit_type="count",
                qty_per_serving=1,
                bag_max_qty=25,
                bag_max_unit="count",
                condiments=["ソース"],
            )
        )

    def _parse_menu_with_sauce(*_args, **_kwargs):
        return None, None, [{"name": "白身魚フライ", "unit_type": "count", "qty_per_serving": 1}], [
            {"menu_date": date(2026, 9, 1), "daypart": "昼", "name": "白身魚フライ", "slot_index": 0},
        ]

    monkeypatch.setattr(menu_service, "_parse_monthly_menu", _parse_menu_with_sauce)

    menu_service.create_menu(
        "2026-09",
        b"dummy",
        "menu.xlsx",
        require_menu_master_review=True,
    )

    payload = menu_service.get_menu("2026-09")
    assert payload is not None
    assert payload["master_checks"]["issues"][0]["reason"] == "bagging_settings_missing"
    assert payload["master_checks"]["issues"][0]["condiment_issues"][0]["condiment_name"] == "ソース"
