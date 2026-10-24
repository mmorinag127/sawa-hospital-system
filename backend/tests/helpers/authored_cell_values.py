"""Resolve only the declared note/remarks spelling in component input tables."""


def declared_field(target, fields):
    from src.services.hakodate_ocr_evidence_service import authored_cell_role
    role = authored_cell_role(target)
    field = target["field"]
    assert not {"note", "remarks"} <= set(fields), "ambiguous declared note aliases"
    if field not in fields and role == "note" and field == "note" and "remarks" in fields:
        field = "remarks"
    assert field in fields, ("physical target has no declared input field", target)
    return field


def assert_declared_cells_used(required, used):
    assert required <= used, "authored quantity/note cells must belong to the unchanged physical target plan"
