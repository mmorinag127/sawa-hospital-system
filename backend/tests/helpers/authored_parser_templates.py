"""Explicit synthetic parser layouts, not current or live facility definitions."""

def authored_aux_component_template(*, hiramori=False):
    diets = (["regular", "daycare", "staff", "hiramori", "no_meat", "no_fish", "no_fried"]
             if hiramori else ["regular", "daycare", "staff", "no_meat", "no_fish", "no_fried", "change_1"])
    quantities = [f"qty.{diet}_x" for diet in diets]
    return {
        "columns_authoritative": True,
        "main_ocr_row_fields": ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4",
                                *quantities, "remarks"],
        "columns": [
            {"role": "date", "index": 0, "source_index": 0},
            {"role": "daypart", "index": 1, "source_index": 1},
            {"role": "aux", "index": 2, "source_index": 2},
            {"role": "menu_name", "index": 3, "source_index": 3},
            {"role": "aux", "index": 4, "source_index": 4},
            *[{"role": "quantity", "name": f"qty.{diet}_x", "source_index": index,
               "index": index, "diet_type": diet, "area_id": "X"}
              for index, diet in enumerate(diets, 5)],
            {"role": "note", "index": 12, "source_index": 12},
        ],
    }
