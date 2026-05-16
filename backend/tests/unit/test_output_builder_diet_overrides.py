from src.services import output_builder


def test_output_diet_type_override_maps_diabetes_to_regular(monkeypatch):
    facility_config = {
        "fax_template_override": {
            "columns": [
                {
                    "role": "quantity",
                    "diet_type": "diabetes",
                    "area_id": "X",
                    "output_diet_type": "regular",
                }
            ]
        }
    }
    monkeypatch.setattr(output_builder.config_service, "get_facility_config", lambda _facility_id: facility_config)
    monkeypatch.setattr(output_builder.order_service, "_expanded_cell_same_daypart_copy_enabled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(output_builder.order_service, "_week_sheet_name_from_week_value", lambda _value: "")
    monkeypatch.setattr(output_builder.order_service, "_apply_change_override_priority_to_lines", lambda lines: lines)
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_entries_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_items_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder, "get_order_menu_snapshot", lambda _order_id: {})
    monkeypatch.setattr(output_builder.daily_output_override_service, "apply_overrides_to_lines", lambda lines, _facility_id: lines)

    lines = output_builder.build_order_lines_for_outputs(
        {
            "id": "ORD-test",
            "facility": "FAC00016",
            "week_value": "2026-05",
            "lines": [
                {
                    "date": "2026-05-10",
                    "daypart": "朝",
                    "menu_name": "ごぼうと竹輪の煮物",
                    "diet_type": "diabetes",
                    "area_id": "X",
                    "quantity": 4,
                }
            ],
        }
    )

    assert lines[0]["diet_type"] == "regular"
    assert lines[0]["source_diet_type"] == "diabetes"


def test_output_diet_type_is_unchanged_without_facility_override(monkeypatch):
    monkeypatch.setattr(output_builder.config_service, "get_facility_config", lambda _facility_id: {"fax_template_override": {"columns": []}})
    monkeypatch.setattr(output_builder.order_service, "_expanded_cell_same_daypart_copy_enabled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(output_builder.order_service, "_week_sheet_name_from_week_value", lambda _value: "")
    monkeypatch.setattr(output_builder.order_service, "_apply_change_override_priority_to_lines", lambda lines: lines)
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_entries_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_items_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder, "get_order_menu_snapshot", lambda _order_id: {})
    monkeypatch.setattr(output_builder.daily_output_override_service, "apply_overrides_to_lines", lambda lines, _facility_id: lines)

    lines = output_builder.build_order_lines_for_outputs(
        {
            "id": "ORD-test",
            "facility": "FAC00016",
            "week_value": "2026-05",
            "lines": [
                {
                    "date": "2026-05-10",
                    "daypart": "朝",
                    "menu_name": "ごぼうと竹輪の煮物",
                    "diet_type": "diabetes",
                    "area_id": "X",
                    "quantity": 4,
                }
            ],
        }
    )

    assert lines[0]["diet_type"] == "diabetes"
    assert "source_diet_type" not in lines[0]
