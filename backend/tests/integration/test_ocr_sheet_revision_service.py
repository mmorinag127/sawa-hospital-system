import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import ocr_sheet_revision_service  # noqa: E402


def _field_label(field: str) -> str:
    return {
        "date_mmdd": "日付",
        "menu": "メニュー",
        "qty.regular_x": "常食",
    }.get(field, field)


def _field_value_to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    return str(value)


def test_normalize_sheet_revision_snapshot_pads_fields_header_and_row_ids() -> None:
    snapshot = ocr_sheet_revision_service.normalize_sheet_revision_snapshot(
        fields=["date_mmdd", "menu"],
        header=["日付"],
        rows_payload=[["02/01", "Menu A", 4], {"date_mmdd": "02/02", "menu": "Menu B", "col3": 5}],
        row_ids=["row-a"],
        field_label=_field_label,
        field_value_to_str=_field_value_to_str,
    )

    assert snapshot["fields"] == ["date_mmdd", "menu", "col3"]
    assert snapshot["header"] == ["日付", "メニュー", "col3"]
    assert snapshot["rows"] == [["02/01", "Menu A", "4"], ["02/02", "Menu B", "5"]]
    assert snapshot["row_ids"] == ["row-a", "row-2"]


def test_select_edited_sheet_revision_prefers_latest_exact_sheet_revision() -> None:
    payload = {
        "_edited_ocr": {
            "revisions": [
                {"ui_mode": "sheet", "rows": [["a"]], "sheet_save_only": False, "revision_id": "r1"},
                {"ui_mode": "sheet", "rows": [["b"]], "sheet_save_only": True, "revision_id": "r2"},
            ],
            "latest": {"ui_mode": "sheet", "rows": [["c"]], "sheet_save_only": False, "revision_id": "r3"},
        }
    }

    latest_any = ocr_sheet_revision_service.select_edited_sheet_revision(payload, exact_only=False)
    latest_exact = ocr_sheet_revision_service.select_edited_sheet_revision(payload, exact_only=True)

    assert latest_any is not None and latest_any["revision_id"] == "r3"
    assert latest_exact is not None and latest_exact["revision_id"] == "r2"


def test_build_sheet_payload_from_revision_rebases_exact_rows_on_matching_row_ids() -> None:
    fallback_sheet = {
        "order_id": "ORDTEST",
        "fields": ["date_mmdd", "menu", "qty.regular_x", "remarks"],
        "header": ["日付", "メニュー", "常食", "備考"],
        "rows": [
            ["02/01", "Menu A", "", ""],
            ["02/01", "Menu B", "", ""],
        ],
        "row_ids": ["row-a", "row-b"],
        "source": "weekly_menu",
    }
    revision = {
        "fields": ["date_mmdd", "menu", "qty.regular_x", "remarks"],
        "header": ["日付", "メニュー", "常食", "備考"],
        "rows": [
            ["02/01", "Menu A", "4", ""],
            ["02/01", "Menu B", "8", "memo"],
        ],
        "row_ids": ["row-a", "row-b"],
        "sheet_save_only": True,
    }

    payload = ocr_sheet_revision_service.build_sheet_payload_from_revision(
        order_id="ORDTEST",
        revision=revision,
        fallback_sheet=fallback_sheet,
        field_label=_field_label,
        field_value_to_str=_field_value_to_str,
    )

    assert payload is not None
    assert payload["rows"] == revision["rows"]
    assert payload["row_ids"] == ["row-a", "row-b"]
    assert payload["source"] == "weekly_menu"
