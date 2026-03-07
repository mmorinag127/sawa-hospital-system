import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.fax_extractor import _rows_from_pipeline_payload  # noqa: E402


def test_rows_from_pipeline_payload_supports_flat_dotted_qty_keys():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.soft_2f",
            "remarks",
        ]
    }
    payload = {
        "rows": [
            {
                "date_mmdd": "2/15",
                "daypart": "朝",
                "menu": "じゃが芋のコンソメ煮",
                "qty.regular_2f": "20",
                "qty.soft_2f": "1",
                "remarks": "",
            }
        ]
    }

    rows = _rows_from_pipeline_payload(payload, template)

    assert rows == [["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""]]


def test_rows_from_pipeline_payload_supports_nested_qty_object():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.soft_2f",
            "remarks",
        ]
    }
    payload = {
        "rows": [
            {
                "date_mmdd": "2/15",
                "daypart": "朝",
                "menu": "キャベツサラダ",
                "qty": {"regular_2f": "18", "soft_2f": "2"},
                "remarks": "ok",
            }
        ]
    }

    rows = _rows_from_pipeline_payload(payload, template)

    assert rows == [["2/15", "朝", "キャベツサラダ", "18", "2", "ok"]]


def test_rows_from_pipeline_payload_expands_row_index_for_quantity_only_rows():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
        ]
    }
    payload = {
        "rows": [
            {"row_index": 0, "qty.regular_2f": "20"},
            {"row_index": 2, "qty.regular_2f": "11"},
        ]
    }

    rows = _rows_from_pipeline_payload(payload, template)

    assert rows == [
        ["", "", "", "20"],
        ["", "", "", ""],
        ["", "", "", "11"],
    ]
