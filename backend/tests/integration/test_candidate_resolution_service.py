import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import candidate_resolution_service  # noqa: E402


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


def test_build_facility_resolution_requires_choice_for_close_scores() -> None:
    resolution = candidate_resolution_service.build_facility_resolution(
        current_facility=None,
        payload={
            "facility_candidates": [
                {"facility_id": "FAC00001", "facility_name": "施設A", "score": 0.82},
                {"facility_id": "FAC00002", "facility_name": "施設B", "score": 0.75},
            ]
        },
    )

    assert resolution["decision_type"] == "facility"
    assert resolution["resolved_value"] is None
    assert resolution["requires_user_choice"] is True
    assert resolution["blocked"] is True
    assert "facility_choice_required" in resolution["blocked_reasons"]
    assert [item["value"] for item in resolution["candidates"]] == ["FAC00001", "FAC00002"]


def test_build_facility_resolution_keeps_current_order_value() -> None:
    resolution = candidate_resolution_service.build_facility_resolution(
        current_facility="FAC00009",
        payload={
            "facility_candidates": [
                {"facility_id": "FAC00001", "facility_name": "施設A", "score": 0.91},
            ]
        },
    )

    assert resolution["resolved_value"] == "FAC00009"
    assert resolution["requires_user_choice"] is False
    assert resolution["blocked"] is False
    assert resolution["candidates"][0]["value"] == "FAC00009"


def test_build_week_resolution_requires_choice_when_multiple_weeks_match(monkeypatch) -> None:
    def _fake_get_menu_for_facility(_month_id: str, _facility_id: str | None):
        return {
            "entries": [
                {"menu_date": "2026-03-01"},
                {"menu_date": "2026-03-02"},
                {"menu_date": "2026-03-03"},
                {"menu_date": "2026-03-04"},
                {"menu_date": "2026-03-05"},
                {"menu_date": "2026-03-06"},
                {"menu_date": "2026-03-07"},
                {"menu_date": "2026-03-15"},
                {"menu_date": "2026-03-16"},
                {"menu_date": "2026-03-17"},
                {"menu_date": "2026-03-18"},
                {"menu_date": "2026-03-19"},
                {"menu_date": "2026-03-20"},
                {"menu_date": "2026-03-21"},
            ]
        }

    monkeypatch.setattr(candidate_resolution_service.menu_service, "get_menu_for_facility", _fake_get_menu_for_facility)

    resolution = candidate_resolution_service.build_week_resolution(
        current_week=None,
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        payload={"table_raw": "03/24 注文書"},
        facility_id="FAC00001",
    )

    assert resolution["decision_type"] == "week"
    assert resolution["resolved_value"] is None
    assert resolution["requires_user_choice"] is True
    assert resolution["blocked"] is True
    assert "week_choice_required" in resolution["blocked_reasons"]
    assert len(resolution["candidates"]) >= 2


def test_build_template_resolution_requires_choice_when_multiple_candidates_exist() -> None:
    resolution = candidate_resolution_service.build_template_resolution_snapshot(
        {
            "template_resolution": {
                "resolved_template_id": "",
                "confidence": 0.4,
                "blocked": False,
                "blocked_reasons": [],
                "candidate_template_ids": [
                    "fax_layout_regular_soft_mixer_forbidden_v1",
                    "fax_layout_floor_2f3f_v1",
                ],
            }
        }
    )

    assert resolution["decision_type"] == "template"
    assert resolution["resolved_value"] is None
    assert resolution["requires_user_choice"] is True
    assert resolution["blocked"] is True
    assert "template_choice_required" in resolution["blocked_reasons"]
    assert [item["value"] for item in resolution["candidates"]] == [
        "fax_layout_regular_soft_mixer_forbidden_v1",
        "fax_layout_floor_2f3f_v1",
    ]


def test_resolve_order_candidates_collects_critical_choices() -> None:
    resolved = candidate_resolution_service.resolve_order_candidates(
        order_id="ORD-CAND-1",
        facility_code=None,
        week_code=None,
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        evidence_payload={
            "facility_candidates": [
                {"facility_id": "FAC00001", "facility_name": "施設A", "score": 0.82},
                {"facility_id": "FAC00002", "facility_name": "施設B", "score": 0.76},
            ],
            "template_resolution": {
                "resolved_template_id": "",
                "confidence": 0.4,
                "blocked": False,
                "blocked_reasons": [],
                "candidate_template_ids": [
                    "fax_layout_regular_soft_mixer_forbidden_v1",
                    "fax_layout_floor_2f3f_v1",
                ],
            },
            "table_raw": "03/24 注文書",
        },
    )

    assert resolved["order_id"] == "ORD-CAND-1"
    assert resolved["requires_user_choice"] is True
    assert {item["decision_type"] for item in resolved["critical_choices"]} >= {"facility", "template"}
    assert resolved["resolutions"]["facility"]["requires_user_choice"] is True
    assert resolved["resolutions"]["template"]["requires_user_choice"] is True


def test_resolve_order_candidates_uses_position_fallback_when_cells_cover_quantity_columns(monkeypatch) -> None:
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]

    monkeypatch.setattr(
        candidate_resolution_service.config_service,
        "get_facility_config",
        lambda _facility_id: {
            "fax_template_id": "fax_layout_floor_2f3f_v1",
            "fax_template": {
                "template_id": "fax_layout_floor_2f3f_v1",
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                    "qty.regular_3f",
                    "qty.soft_2f",
                    "qty.soft_3f",
                    "qty.mixer_2f",
                    "qty.mixer_3f",
                    "remarks",
                ],
            },
        },
    )

    resolved = candidate_resolution_service.resolve_order_candidates(
        order_id="ORD-POSITION-FALLBACK-1",
        facility_code="FAC00001",
        week_code="2026-03@2026-03-22~2026-03-28",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        evidence_payload={
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1.png"}],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )

    template = resolved["resolutions"]["template"]
    column_mapping = resolved["resolutions"]["column_mapping"]
    assert template["resolved_value"] == "fax_layout_floor_2f3f_v1"
    assert template["blocked"] is False
    assert column_mapping["resolved_value"] is not None
    assert column_mapping["decision_source"] == "position_fallback"
    assert column_mapping["requires_user_choice"] is False


def test_resolve_order_candidates_uses_position_fallback_for_noisy_regular_forbidden_headers(
    monkeypatch,
) -> None:
    rows = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22", "\"", "VF", "Menu A", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu B", "27", "", "", "", "", "", ""],
    ]

    monkeypatch.setattr(
        candidate_resolution_service.config_service,
        "get_facility_config",
        lambda _facility_id: {
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "fax_template": {
                "template_id": "fax_layout_regular_forbidden_v1",
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_x",
                    "qty.no_meat_x",
                    "qty.no_fish_x",
                    "qty.change_1_x",
                    "qty.change_2_x",
                    "remarks",
                ],
                "columns": [
                    {"index": 0, "role": "date", "header": "日付"},
                    {"index": 1, "role": "daypart", "header": "区分"},
                    {"index": 2, "role": "menu_name", "header": "メニュー"},
                    {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
                    {"index": 4, "role": "quantity", "header": "肉禁", "diet_type": "no_meat", "area_id": "X"},
                    {"index": 5, "role": "quantity", "header": "魚禁", "diet_type": "no_fish", "area_id": "X"},
                    {"index": 6, "role": "quantity", "header": "変更1", "diet_type": "change_1", "area_id": "X"},
                    {"index": 7, "role": "quantity", "header": "変更2", "diet_type": "change_2", "area_id": "X"},
                    {"index": 8, "role": "note", "header": "備考欄"},
                ],
            },
        },
    )

    resolved = candidate_resolution_service.resolve_order_candidates(
        order_id="ORD-POSITION-FALLBACK-REGULAR-FORBIDDEN-NOISY",
        facility_code="FAC00002",
        week_code="2026-03@2026-03-22~2026-03-28",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        evidence_payload={
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1.png"}],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )

    template = resolved["resolutions"]["template"]
    column_mapping = resolved["resolutions"]["column_mapping"]
    assert template["resolved_value"] == "fax_layout_regular_forbidden_v1"
    assert template["blocked"] is False
    assert column_mapping["decision_source"] == "position_fallback"
    assert column_mapping["requires_user_choice"] is False
    assert (
        column_mapping["resolved_value"]
        == "4:qty.regular_x|6:qty.no_meat_x|7:qty.no_fish_x|8:qty.change_1_x|9:qty.change_2_x"
    )
    assert resolved["critical_choices"] == []


def test_resolve_order_candidates_does_not_override_existing_template_resolution_with_position_fallback(
    monkeypatch,
) -> None:
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]

    monkeypatch.setattr(
        candidate_resolution_service.config_service,
        "get_facility_config",
        lambda _facility_id: {
            "fax_template_id": "fax_layout_floor_2f3f_v1",
            "fax_template": {
                "template_id": "fax_layout_floor_2f3f_v1",
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                    "qty.regular_3f",
                    "qty.soft_2f",
                    "qty.soft_3f",
                    "qty.mixer_2f",
                    "qty.mixer_3f",
                    "remarks",
                ],
            },
        },
    )

    resolved = candidate_resolution_service.resolve_order_candidates(
        order_id="ORD-POSITION-FALLBACK-MISMATCH",
        facility_code="FAC00001",
        week_code="2026-03@2026-03-22~2026-03-28",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        evidence_payload={
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "blocked": False,
                "blocked_reasons": [],
            },
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1.png"}],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )

    template = resolved["resolutions"]["template"]
    column_mapping = resolved["resolutions"]["column_mapping"]
    assert template["resolved_value"] == "fax_layout_regular_soft_mixer_forbidden_v1"
    assert column_mapping.get("decision_source") != "position_fallback"
    assert not str(column_mapping.get("resolved_value") or "").strip()


def test_resolve_order_candidates_requires_choice_for_ambiguous_position_fallback(monkeypatch) -> None:
    rows_a = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    rows_b = [
        ["日付", "区分", "メニュー", "補助", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "", "5", "4", "3", "2", "1", "1", ""],
    ]

    monkeypatch.setattr(
        candidate_resolution_service.config_service,
        "get_facility_config",
        lambda _facility_id: {
            "fax_template_id": "fax_layout_floor_2f3f_v1",
            "fax_template": {
                "template_id": "fax_layout_floor_2f3f_v1",
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                    "qty.regular_3f",
                    "qty.soft_2f",
                    "qty.soft_3f",
                    "qty.mixer_2f",
                    "qty.mixer_3f",
                    "remarks",
                ],
            },
        },
    )

    resolved = candidate_resolution_service.resolve_order_candidates(
        order_id="ORD-POSITION-FALLBACK-AMBIGUOUS",
        facility_code="FAC00001",
        week_code="2026-03@2026-03-22~2026-03-28",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        evidence_payload={
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1.png"}],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows_a),
                    "col_count": len(rows_a[0]),
                    "rows": rows_a,
                    "cells": _structured_cells(rows_a),
                },
                {
                    "page_index": 1,
                    "table_id": "page1_table2",
                    "row_count": len(rows_b),
                    "col_count": len(rows_b[0]),
                    "rows": rows_b,
                    "cells": _structured_cells(rows_b),
                },
            ],
        },
    )

    column_mapping = resolved["resolutions"]["column_mapping"]
    assert column_mapping["decision_source"] == "position_fallback"
    assert column_mapping["requires_user_choice"] is True
    assert column_mapping["resolved_value"] is None
    assert "column_mapping_choice_required" in column_mapping["blocked_reasons"]


def test_resolve_order_candidates_skips_position_fallback_when_facility_conflicts_with_evidence(monkeypatch) -> None:
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]

    monkeypatch.setattr(
        candidate_resolution_service.config_service,
        "get_facility_config",
        lambda _facility_id: {
            "fax_template_id": "fax_layout_floor_2f3f_v1",
            "fax_template": {
                "template_id": "fax_layout_floor_2f3f_v1",
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                    "qty.regular_3f",
                    "qty.soft_2f",
                    "qty.soft_3f",
                    "qty.mixer_2f",
                    "qty.mixer_3f",
                    "remarks",
                ],
            },
        },
    )

    resolved = candidate_resolution_service.resolve_order_candidates(
        order_id="ORD-POSITION-FALLBACK-FACILITY-CONFLICT",
        facility_code="FAC00001",
        week_code="2026-03@2026-03-22~2026-03-28",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        evidence_payload={
            "facility_candidates": [
                {"facility_id": "FAC99999", "facility_name": "別施設", "score": 0.96},
                {"facility_id": "FAC88888", "facility_name": "次点施設", "score": 0.52},
            ],
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1.png"}],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )

    column_mapping = resolved["resolutions"]["column_mapping"]
    assert column_mapping.get("decision_source") != "position_fallback"
    assert column_mapping["resolved_value"] is None


def test_build_column_mapping_resolution_requires_choice_for_stale_position_fallback_resolution() -> None:
    resolution = candidate_resolution_service.build_column_mapping_resolution(
        {
            "column_mapping_resolution": {
                "resolved_value": "3:qty.regular_2f|4:qty.regular_3f",
                "resolved_column_mapping_id": "3:qty.regular_2f|4:qty.regular_3f",
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
                "decision_source": "position_fallback",
            },
            "column_mapping_candidates": [
                {
                    "value": "3:qty.regular_2f|4:qty.regular_3f",
                    "label": "常食2F / 常食3F",
                    "score": 0.99,
                    "decision_source": "position_fallback",
                },
                {
                    "value": "4:qty.regular_2f|5:qty.regular_3f",
                    "label": "常食2F / 常食3F",
                    "score": 0.99,
                    "decision_source": "position_fallback",
                },
            ],
        }
    )

    assert resolution["decision_source"] == "position_fallback"
    assert resolution["requires_user_choice"] is True
    assert resolution["resolved_value"] is None
    assert "column_mapping_choice_required" in resolution["blocked_reasons"]


def test_build_column_mapping_resolution_marks_attention_for_column_swap() -> None:
    resolution = candidate_resolution_service.build_column_mapping_resolution(
        {
            "cell_issues": [
                {"issue_code": "column_swap"},
                {"issue_code": "mirrored_sibling_columns"},
            ]
        }
    )

    assert resolution["decision_type"] == "column_mapping"
    assert resolution["requires_user_choice"] is False
    assert resolution["attention_required"] is True
    assert resolution["attention_reasons"] == ["column_swap", "mirrored_sibling_columns"]


def test_build_column_mapping_resolution_requires_choice_when_candidates_exist() -> None:
    resolution = candidate_resolution_service.build_column_mapping_resolution(
        {
            "column_mapping_candidates": [
                {
                    "candidate_id": "cm-a",
                    "candidate_type": "column_mapping_candidate",
                    "value": "layout-cols-a",
                    "label": "常食 / 軟菜 / ミキサー",
                    "score": 0.62,
                    "reason": "ocr_alignment_close",
                    "evidence_ref": {"page": 1, "row_index": 3},
                },
                {
                    "candidate_id": "cm-b",
                    "candidate_type": "column_mapping_candidate",
                    "value": "layout-cols-b",
                    "label": "常食 / 常食(袋分け) / 軟菜",
                    "score": 0.58,
                    "reason": "ocr_alignment_close",
                    "evidence_ref": {"page": 1, "row_index": 3},
                },
            ]
        }
    )

    assert resolution["decision_type"] == "column_mapping"
    assert resolution["resolved_value"] is None
    assert resolution["requires_user_choice"] is True
    assert resolution["blocked"] is True
    assert "column_mapping_choice_required" in resolution["blocked_reasons"]
    assert [item["value"] for item in resolution["candidates"]] == ["layout-cols-a", "layout-cols-b"]
    assert resolution["ambiguity_scope"] == "column_mapping"
    assert resolution["decision_source"] == "ocr_evidence"
    assert resolution["candidates"][0]["candidate_id"] == "cm-a"
    assert resolution["candidates"][0]["candidate_type"] == "column_mapping_candidate"
    assert resolution["candidates"][0]["evidence_ref"] == {"page": 1, "row_index": 3}


def test_build_quantity_resolution_marks_attention_for_failed_cells_and_spans() -> None:
    resolution = candidate_resolution_service.build_quantity_resolution(
        {
            "cell_issues": [
                {"issue_code": "merged_numeric_cell"},
                {"issue_code": "unexpected_dense_fill"},
            ],
            "failed_cells": [
                {"row_index": 1, "column_index": 4},
            ],
        }
    )

    assert resolution["decision_type"] == "quantity"
    assert resolution["requires_user_choice"] is False
    assert resolution["attention_required"] is True
    assert "merged_numeric_cell" in resolution["attention_reasons"]
    assert "unexpected_dense_fill" in resolution["attention_reasons"]
    assert "failed_cells_present" in resolution["attention_reasons"]
    assert resolution["failed_cell_count"] == 1


def test_build_quantity_resolution_requires_choice_when_critical_candidates_exist() -> None:
    resolution = candidate_resolution_service.build_quantity_resolution(
        {
            "critical_quantity_candidates": [
                {
                    "candidate_id": "qty-a",
                    "candidate_type": "critical_quantity_candidate",
                    "value": "qty-candidate-a",
                    "label": "3 / 8 / 8",
                    "score": 0.61,
                    "reason": "high_impact_total_cell",
                    "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4},
                    "critical": True,
                },
                {
                    "candidate_id": "qty-b",
                    "candidate_type": "critical_quantity_candidate",
                    "value": "qty-candidate-b",
                    "label": "8 / 8 / 8",
                    "score": 0.57,
                    "reason": "high_impact_total_cell",
                    "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4},
                    "critical": True,
                },
            ]
        }
    )

    assert resolution["decision_type"] == "quantity"
    assert resolution["resolved_value"] is None
    assert resolution["requires_user_choice"] is True
    assert resolution["blocked"] is True
    assert "quantity_choice_required" in resolution["blocked_reasons"]
    assert [item["value"] for item in resolution["candidates"]] == ["qty-candidate-a", "qty-candidate-b"]
    assert resolution["ambiguity_scope"] == "high_impact_quantity"
    assert resolution["decision_source"] == "critical_quantity_candidates"
    assert resolution["candidates"][0]["candidate_type"] == "critical_quantity_candidate"
    assert resolution["candidates"][0]["evidence_ref"] == {"page": 1, "row_index": 7, "column_index": 4}


def test_resolve_order_candidates_promotes_critical_quantity_choice_payload() -> None:
    resolved = candidate_resolution_service.resolve_order_candidates(
        order_id="ORD-CAND-CRITQ",
        facility_code="FAC00001",
        week_code="2026-03@2026-03-22~2026-03-28",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        evidence_payload={
            "critical_quantity_candidates": [
                {
                    "candidate_id": "qty-a",
                    "candidate_type": "critical_quantity_candidate",
                    "value": "qty-candidate-a",
                    "label": "3 / 8 / 8",
                    "score": 0.61,
                    "reason": "high_impact_total_cell",
                    "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4},
                    "critical": True,
                },
                {
                    "candidate_id": "qty-b",
                    "candidate_type": "critical_quantity_candidate",
                    "value": "qty-candidate-b",
                    "label": "8 / 8 / 8",
                    "score": 0.57,
                    "reason": "high_impact_total_cell",
                    "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4},
                    "critical": True,
                },
            ]
        },
    )

    quantity = resolved["resolutions"]["quantity"]
    assert quantity["requires_user_choice"] is True
    assert quantity["ambiguity_scope"] == "high_impact_quantity"
    critical_choice = next(item for item in resolved["critical_choices"] if item["decision_type"] == "quantity")
    assert critical_choice["decision_source"] == "critical_quantity_candidates"
    assert critical_choice["ambiguity_scope"] == "high_impact_quantity"
    assert critical_choice["evidence_ref"] == {"page": 1, "row_index": 7, "column_index": 4}
