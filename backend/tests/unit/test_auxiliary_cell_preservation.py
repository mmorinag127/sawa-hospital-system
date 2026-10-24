from copy import deepcopy

import pytest

from src.services import order_service
from tests.helpers.authored_parser_templates import authored_aux_component_template


@pytest.mark.parametrize("surface", ["dictionary", "lists"])
@pytest.mark.parametrize("raw_identity", [("05/01", "朝"), ("04/26", ""), ("", "")])
@pytest.mark.parametrize("total", ["0", "", "-", "※", "７０"])
def test_display_projection_preserves_bound_raw_total_without_reoverlay(surface, raw_identity, total):
    template = authored_aux_component_template()
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    header[4] = "合計"
    source = [["04/26", "朝", "主", "Menu A", total, "9", "", "", "", "", "", "", ""]]
    target = deepcopy(source)
    target[0][4] = "old-total"
    raw_row = [raw_identity[0], raw_identity[1], "", "Menu A", "999", "9", "", "", "", "", "", "", ""]
    raw = {"tables": [{"page_index": 1, "table_id": "unbound-total", "rows": [raw_row]}]}
    original_source, original_target, original_raw = deepcopy(source), deepcopy(target), deepcopy(raw)
    if surface == "dictionary":
        dictionaries = [{"row_id": "owned-row", "values": target[0]}]
        original_dictionaries = deepcopy(dictionaries)
        result = order_service._overlay_payload_aux_cells_onto_sheet_rows(
            rows=dictionaries, fields=fields, header=header, payload_rows=source,
            raw_payload=raw, template=template,
        )
        assert dictionaries == original_dictionaries
        assert result[0]["row_id"] == "owned-row"
        actual = [row["values"] for row in result]
    else:
        actual, stats = order_service._project_payload_display_cells_onto_sheet_row_lists(
            rows=target, fields=fields, header=header, payload_rows=source,
            raw_payload=raw, template=template,
        )
        assert stats["exact"] == 1
    assert actual == source
    assert source == original_source and target == original_target and raw == original_raw


def test_conflicting_totals_are_diagnosed_without_erasure_or_reanchoring():
    template = authored_aux_component_template()
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    header[4] = "合計"
    rows = [
        ["04/26", "朝", "主", "Menu A", "0", "9", "", "", "", "", "", "", ""],
        ["04/26", "朝", "副", "Menu B", "７０", "8", "", "", "", "", "", "", ""],
        ["04/26", "朝", "副", "Menu C", "", "7", "", "", "", "", "", "", ""],
    ]
    original = deepcopy(rows)
    result, stats = order_service._canonicalize_block_helper_display_rows(rows=rows, fields=fields, header=header)
    assert result == original and rows == original
    assert stats["helper_reanchor_count"] == 0
    assert stats["helper_conflict_count"] == 1
    assert stats["helper_conflicts"][0]["values"] == ["0", "７０"]


@pytest.mark.parametrize("surface", ["dictionary", "lists"])
@pytest.mark.parametrize("identity", [("05/01", "朝"), ("04/26", ""), ("", "朝")])
def test_unresolved_auxiliary_identity_cannot_modify_the_target(surface, identity):
    template = authored_aux_component_template()
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    header[4] = "合計"
    target = [["04/26", "朝", "主", "Menu A", "0", "9", "", "", "", "", "", "", ""]]
    source = deepcopy(target)
    source[0][0], source[0][1], source[0][4] = identity[0], identity[1], "999"
    original_target, original_source = deepcopy(target), deepcopy(source)
    with pytest.raises(order_service._CanonicalOcrProjectionError, match="sheet_canonical_mismatch"):
        if surface == "dictionary":
            order_service._overlay_payload_aux_cells_onto_sheet_rows(
                rows=[{"values": target[0]}], fields=fields, header=header,
                payload_rows=source, template=template,
            )
        else:
            order_service._project_payload_display_cells_onto_sheet_row_lists(
                rows=target, fields=fields, header=header, payload_rows=source, template=template,
            )
    assert target == original_target and source == original_source
