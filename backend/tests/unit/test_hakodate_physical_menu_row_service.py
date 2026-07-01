from datetime import date

from src.services import hakodate_physical_menu_row_service


def _entries(day_counts: dict[int, tuple[int, int, int]]) -> list[dict]:
    rows: list[dict] = []
    for day, counts in day_counts.items():
        for daypart, count in zip(("breakfast", "lunch", "dinner"), counts, strict=True):
            for slot in range(count):
                rows.append(
                    {
                        "menu_date": date(2026, 7, day),
                        "daypart_key": daypart,
                        "slot_index": slot,
                        "menu_name": f"menu-{day}-{daypart}-{slot}",
                    }
                )
    return rows


def test_physical_row_count_for_normal_week_is_fifty_six() -> None:
    entries = _entries({day: (2, 3, 3) for day in range(12, 19)})

    assert hakodate_physical_menu_row_service.physical_row_count_from_entries(entries) == 56


def test_physical_row_count_increases_for_event_food_physical_rows() -> None:
    entries = _entries({day: (2, 3, 3) for day in range(5, 12)})
    entries.append(
        {
            "menu_date": date(2026, 7, 7),
            "daypart_key": "lunch",
            "slot_index": 3,
            "menu_name": "ちらし寿司の素",
        }
    )

    assert hakodate_physical_menu_row_service.physical_row_count_from_entries(entries) == 57


def test_sheet_physical_row_count_ignores_diet_override_rows() -> None:
    sheet = {
        "physical_menu_row_count": 56,
        "rows": [["regular"]] * 56 + [["soft override"]] * 7,
    }

    count, source = hakodate_physical_menu_row_service.physical_row_count_from_sheet(sheet)

    assert count == 56
    assert source == "physical_menu_row_count"


def test_sheet_without_physical_row_metadata_is_unresolved() -> None:
    count, source = hakodate_physical_menu_row_service.physical_row_count_from_sheet(
        {"rows": [["regular"]] * 56}
    )

    assert count == 0
    assert source == "physical_menu_rows_unresolved"
