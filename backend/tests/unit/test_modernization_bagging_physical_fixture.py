"""Synthetic component ownership, not real recognizer or operator-flow proof."""
from copy import deepcopy

import pytest
from sqlalchemy import delete, select

from src.db import session_scope
from src.models.menu import MonthlyMenuEntry
from src.models.order import Order
from src.models.order_output_artifact import OrderBaggingResult
from src.models.order_sheet_draft import OrderSheetDraft
from src.services import order_service, hakodate_physical_menu_row_service
from src.services.template_field_schema_service import canonical_field_name_from_template_column
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


@pytest.mark.parametrize("diets,quantities", [
    (["regular"], [3]),
    (["soft", "mixer", "diabetes"], [4, 5, 6]),
])
def test_authored_physical_regular_is_unique_without_adding_order_quantities(
    isolated_formal_schema, monkeypatch, tmp_path, diets, quantities,
):
    lines = [{"date": "2026-06-21", "daypart": "昼", "menu_name": "合成献立",
              "diet_type": diet, "area_id": "2F", "quantity_original": quantity,
              "bag_type": "normal"}
             for diet, quantity in zip(diets, quantities, strict=True)]
    original = deepcopy(lines)
    registered = accepted_bagging_order(monkeypatch, tmp_path, lines)
    assert lines == original
    with session_scope() as session:
        order = session.get(Order, registered["id"])
        facility, week = order.facility_code, order.week_code
        entries = session.execute(select(MonthlyMenuEntry).where(
            MonthlyMenuEntry.facility_override == facility)).scalars().all()
        assert len(entries) == len(set(["regular", *diets]))
        assert sum(entry.diet_type == "regular" for entry in entries) == 1
        assert {entry.slot_index for entry in entries} == {0}
        artifact = session.execute(select(OrderBaggingResult).where(
            OrderBaggingResult.order_id == order.id)).scalar_one()
        candidate = artifact.payload_json["materialization_candidate"]
        assert {line["diet_type"]: line["quantity_original"] for line in candidate["lines"]} == dict(zip(diets, quantities, strict=True))
        assert all(line["quantity_corrected"] is None for line in candidate["lines"])
        draft = session.get(OrderSheetDraft, artifact.source_saved_sheet_id)
        assert draft.draft_sheet_json["fields"] == ["date", "daypart", "menu", *[
            canonical_field_name_from_template_column(column)
            for column in candidate["configured_input"]["facility"]["fax_template"]["columns"][3:]]]
        assert draft.draft_sheet_json["rows"] == [["2026-06-21", "昼", "合成献立", *quantities]]
    physical = order_service._build_position_menu_entries(week, facility)
    assert len(physical) == 1
    assert physical[0]["menu_name"] == "合成献立"
    if diets != ["regular"]:
        with session_scope() as session:
            session.execute(delete(MonthlyMenuEntry).where(
                MonthlyMenuEntry.facility_override == facility,
                MonthlyMenuEntry.diet_type == "regular"))
        unresolved = order_service._build_position_menu_entries(week, facility)
        assert unresolved == []
        with pytest.raises(ValueError, match="^physical_menu_rows_unresolved$"):
            hakodate_physical_menu_row_service.physical_row_keys_from_entries(unresolved)


@pytest.mark.parametrize("area,field", [("common", "qty.regular_x"),
                                      ("2F", "qty.regular_2f"),
                                      (None, "qty.regular_x")])
def test_authored_area_identity_uses_canonical_saved_field_without_quantity_changes(
    isolated_formal_schema, monkeypatch, tmp_path, area, field,
):
    import fitz
    from src.models.uploaded_pdf import UploadedPdf
    from src.services.order_output_artifact_service import payload_digest

    lines = [{"date": "2026-03-22", "daypart": "朝", "menu_name": "献立A",
              "diet_type": "regular", "area_id": area, "quantity_original": 2,
              "bag_type": "standard"}]
    original = deepcopy(lines)
    registered = accepted_bagging_order(monkeypatch, tmp_path, lines)
    assert lines == original
    with session_scope() as session:
        artifact = session.execute(select(OrderBaggingResult).where(
            OrderBaggingResult.order_id == registered["id"])).scalar_one()
        assert artifact.payload_digest == payload_digest(artifact.payload_json)
        candidate = artifact.payload_json["materialization_candidate"]
        assert [(line["diet_type"], line["area_id"], line["quantity_original"],
                 line["quantity_corrected"], line["source_field"])
                for line in candidate["lines"]] == [("regular", area, 2, None, field)]
        draft = session.get(OrderSheetDraft, artifact.source_saved_sheet_id)
        assert draft.draft_sheet_json["fields"] == ["date", "daypart", "menu", field]
        assert draft.draft_sheet_json["rows"] == [["2026-03-22", "朝", "献立A", 2]]
        column = candidate["configured_input"]["facility"]["fax_template"]["columns"][3]
        assert column["diet_type"] == "regular" and column["area_id"] == area
        upload = session.execute(select(UploadedPdf)).scalar_one()
        with fitz.open(upload.storage_uri) as pdf:
            text = "".join(page.get_text() for page in pdf)
        for value in (upload.facility_hint, "2026-03-22", "朝", "献立A",
                      f"regular / {area} / 2"):
            assert value in text
