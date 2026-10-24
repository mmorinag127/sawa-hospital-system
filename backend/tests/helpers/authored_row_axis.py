"""Synthetic image geometry; original capture and calendar validators stay real."""
from src.services import order_service as orders
from src.services import master_order_form_template_service as master
from src.services import ocr_row_recovery_service as recovery


def review_geometry(source_receipt, start_input):
    days = recovery.calendar_contract(
        [{"date": row["menu_date"]} for row in start_input["physical_menu_entries"]],
        week_value=source_receipt["order_input_identity"]["week_code"],
    )
    return {"calendar_days": days, "header_band_count": len(master.TEMPLATE_HEADER_EDGE_ROWS),
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 100}}


def operator_input(review):
    days = review["calendar_days"]
    assert days
    boundaries = [0]
    for day in days:
        expected = day["expected_band_count"]
        assert expected is None or (type(expected) is int and expected > 0)
        # This component explicitly supplies one operator band on empty dates.
        boundaries.append(boundaries[-1] + (1 if expected is None else expected))
    count = review["header_band_count"] + boundaries[-1] + 1
    return {"corrected_ys": [index * 100.0 / (count - 1) for index in range(count)],
            "coordinate_space": review["coordinate_space"],
            "body_day_boundary_indexes": boundaries,
            "day_labels": [day["date"] for day in days]}


def install_image_provider(monkeypatch):
    seen = []

    def image(order_id, *, source_receipt, start_input):
        orders._validate_geometry_source(source_receipt, source_receipt["selected_pdf_receipt"],
            start_input["configured_input"], source_receipt["order_input_identity"])
        saved = ((start_input["workflow"]["secondary_actions_json"] or {}).get("workflow_v2") or {}).get("row_axis_override")
        consumed = orders._normalize_operator_row_axis_override(saved,
            selected_pdf_receipt=source_receipt["selected_pdf_receipt"],
            configured_input=start_input["configured_input"], order_input_identity=source_receipt["order_input_identity"])
        seen.append(consumed)
        return {"status": "ready", "saved_override": consumed,
                **review_geometry(source_receipt, start_input)}

    monkeypatch.setattr(orders, "build_hakodate_row_axis_review", image)
    return seen
