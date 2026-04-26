import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, position_column_mapping_service  # noqa: E402


def _fac00004_template() -> dict:
    resolved = config_service.get_facility_config("FAC00004") or {}
    template = resolved.get("fax_template")
    assert isinstance(template, dict)
    return copy.deepcopy(template)


def _fac00002_template() -> dict:
    resolved = config_service.get_facility_config("FAC00002") or {}
    template = resolved.get("fax_template")
    assert isinstance(template, dict)
    return copy.deepcopy(template)


def _fac00005_template() -> dict:
    resolved = config_service.get_facility_config("FAC00005") or {}
    template = resolved.get("fax_template")
    assert isinstance(template, dict)
    return copy.deepcopy(template)


def _fac00004_rows() -> list[list[str]]:
    return [
        ["", "", "", "", "", "", "", "", "", "", "", "山田菜", "備考欄"],
        ["", "", "", "献立", "合計", "#☆", "通所", "職員", "平森", "", "", "", ""],
        ["日 付", "区\n分", "", "", "", "", "", "", "肉蒸", "魚禁", "揚げ物", "", ""],
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26\n(日)", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""],
        ["", "夕", "主", "麻婆豆腐", "59", "58", "", "", "", "", "6", "", ""],
    ]


def _fac00002_rows() -> list[list[str]]:
    return [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22\n(日)", "IN", "HKD", "Menu A", "", "", "", "", "", "", ""],
        ["", '"', "VF", "Menu B", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu C", "27", "", "", "", "", "", ""],
    ]


def _fac00005_rows() -> list[list[str]]:
    return [
        ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
        ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
        ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
        ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
    ]


def _structured_cells(rows: list[list[str]]) -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    row_height = 1.0 / max(len(rows), 1)
    col_width = 1.0 / max(max((len(row) for row in rows), default=1), 1)
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            cells.append(
                {
                    "row_index": row_index,
                    "col_index": col_index,
                    "text": value,
                    "bbox": [
                        round(col_index * col_width, 4),
                        round(row_index * row_height, 4),
                        round((col_index + 1) * col_width, 4),
                        round((row_index + 1) * row_height, 4),
                    ],
                }
            )
    return cells


def _position_payload(
    *,
    resolved_value: str,
    rows: list[list[str]] | None = None,
    partial_quantity_mapping: bool = False,
    normalized: bool = False,
    source_col_indexes: list[int] | None = None,
) -> dict:
    payload = {
        "template_resolution": {
            "resolved_template_id": "fac00004-template",
            "candidate_template_ids": ["fac00004-template"],
            "confidence": 0.92,
            "blocked": False,
            "blocked_reasons": [],
            "decision_source": "position_fallback",
        },
        "column_mapping_resolution": {
            "resolved_value": resolved_value,
            "resolved_column_mapping_id": resolved_value,
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "decision_source": "position_fallback",
            "partial_quantity_mapping": partial_quantity_mapping,
            "confidence": 0.9,
        },
        "column_mapping_candidates": [
            {
                "candidate_id": "pcm-stale",
                "candidate_type": "position_fallback_candidate",
                "value": resolved_value,
                "label": "stale-position-fallback",
                "score": 0.9,
                "decision_source": "position_fallback",
                "auto_selectable": not partial_quantity_mapping,
                "partial_quantity_mapping": partial_quantity_mapping,
            }
        ],
    }
    if isinstance(source_col_indexes, list):
        payload["column_mapping_resolution"]["evidence_ref"] = {
            "source_col_indexes": source_col_indexes,
        }
    if isinstance(rows, list):
        payload["tables"] = [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "rows": rows,
                "cells": _structured_cells(rows),
                "row_count": len(rows),
                "col_count": len(rows[0]) if rows else 0,
            }
        ]
    if normalized:
        payload["page_correction"] = {
            "applied": True,
            "document_rotation_deg": 90,
        }
        payload["page_correction_artifacts"] = {
            "template_warp_page_indexes": [1],
            "position_normalized": True,
        }
    return payload


def test_augment_payload_with_position_fallback_recomputes_stale_aux_mapping() -> None:
    template = _fac00004_template()
    payload = _position_payload(
        resolved_value="4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
        rows=_fac00004_rows(),
    )

    assert not position_column_mapping_service.payload_uses_ready_position_fallback(
        payload,
        template=template,
    )

    augmented = position_column_mapping_service.augment_payload_with_position_fallback(
        payload,
        template,
        template_id="fac00004-template",
    )

    resolution = augmented.get("column_mapping_resolution") if isinstance(augmented, dict) else None
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") == (
        "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
        "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
    )
    assert resolution.get("blocked") is False
    evidence_ref = resolution.get("evidence_ref") if isinstance(resolution.get("evidence_ref"), dict) else {}
    assert evidence_ref.get("source_col_indexes") == [5, 6, 7, 8, 9, 10, 11]


def test_payload_uses_ready_position_fallback_preserves_valid_aux_aware_mapping() -> None:
    template = _fac00004_template()
    payload = _position_payload(
        resolved_value="5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x",
        rows=_fac00004_rows(),
    )

    assert position_column_mapping_service.payload_uses_ready_position_fallback(
        payload,
        template=template,
    )

    augmented = position_column_mapping_service.augment_payload_with_position_fallback(
        payload,
        template,
        template_id="fac00004-template",
    )

    resolution = augmented.get("column_mapping_resolution") if isinstance(augmented, dict) else None
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") == (
        "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
        "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
    )
    assert resolution.get("blocked") is False


def test_augment_payload_with_position_fallback_blocks_stale_aux_mapping_when_recompute_is_impossible() -> None:
    template = _fac00004_template()
    payload = _position_payload(
        resolved_value="4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
        rows=None,
    )

    augmented = position_column_mapping_service.augment_payload_with_position_fallback(
        payload,
        template,
        template_id="fac00004-template",
    )

    resolution = augmented.get("column_mapping_resolution") if isinstance(augmented, dict) else None
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") is None
    assert resolution.get("resolved_column_mapping_id") is None
    assert resolution.get("blocked") is True
    assert resolution.get("blocked_reasons") == ["column_mapping_contract_mismatch"]
    assert resolution.get("requires_user_choice") is False


def test_augment_payload_with_position_fallback_accepts_shifted_full_coverage_for_position_normalized_evidence() -> None:
    template = _fac00002_template()
    payload = _position_payload(
        resolved_value="4:qty.regular_x|5:qty.no_meat_x|6:qty.no_fish_x|7:qty.change_1_x|8:qty.change_2_x",
        rows=_fac00002_rows(),
        normalized=True,
    )

    augmented = position_column_mapping_service.augment_payload_with_position_fallback(
        payload,
        template,
        template_id="fac00002-template",
    )

    resolution = augmented.get("column_mapping_resolution") if isinstance(augmented, dict) else None
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") == (
        "4:qty.regular_x|5:qty.no_meat_x|6:qty.no_fish_x|7:qty.change_1_x|8:qty.change_2_x"
    )
    assert resolution.get("blocked") is False
    assert position_column_mapping_service.payload_uses_ready_position_fallback(
        augmented,
        template=template,
    )
    assert not position_column_mapping_service.payload_uses_partial_position_fallback(augmented)


def test_augment_payload_with_position_fallback_keeps_non_normalized_shifted_coverage_partial() -> None:
    template = _fac00005_template()
    payload = _position_payload(
        resolved_value="5:qty.soft_x|6:qty.regular_bag_x",
        rows=_fac00005_rows(),
        partial_quantity_mapping=True,
        source_col_indexes=[5, 6],
    )

    augmented = position_column_mapping_service.augment_payload_with_position_fallback(
        payload,
        template,
        template_id="fac00005-template",
    )

    resolution = augmented.get("column_mapping_resolution") if isinstance(augmented, dict) else None
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") == "5:qty.soft_x|6:qty.regular_bag_x"
    assert resolution.get("blocked") is False
    assert not position_column_mapping_service.payload_uses_ready_position_fallback(
        augmented,
        template=template,
    )
    assert position_column_mapping_service.payload_uses_partial_position_fallback(augmented)
