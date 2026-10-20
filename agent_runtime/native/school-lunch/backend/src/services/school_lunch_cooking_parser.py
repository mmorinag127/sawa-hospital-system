"""School-lunch combined menu/material tables, independent of price extraction."""
from typing import Any

from src.services import implementation_price_table_ocr_service as fields


def extract_tables(tables: list[dict[str, Any]], *, filename: str, provider: str) -> dict[str, Any]:
    result = {"document_type": "cooking_arrangement", "filename": filename,
              "status": "needs_review", "provider": provider, "review_required": True,
              "menus": [], "issues": []}
    for table in tables:
        rows = [[fields._normalize_text(cell) for cell in row] for row in table["rows"]]
        match = fields._find_header_row(rows)
        # Only direct workbook cells prove that a blank row is mere layout.
        # OCR gaps may have lost a heading: preserve the shared OCR stop/review path.
        if table.get("source") != "structured_excel" or not match or match[1]["menu"] != match[1]["ingredient"]:
            parsed = fields.extract_cooking_arrangement_candidates_from_tables([table], filename=filename, provider=provider)
            result["menus"].extend(parsed["menus"])
            result["issues"].extend(issue for issue in parsed["issues"] if issue["code"] != "no_cooking_rows")
            continue
        header, columns = match
        prefix = f"{provider}:{table.get('source') or 'table'}:page{table.get('page_index')}:table{table.get('table_id') or ''}"
        date_text = table.get("page_text") or "\n".join(cell for row in rows[:header] for cell in row)
        service_date, raw_date, date_issue = fields._explicit_date_from_page_text(date_text)
        servings = fields._explicit_servings_from_preamble(rows, header)
        issues = result["issues"]
        for code, message in ((date_issue, "提供日を年月日で確認してください。"),
                              ("servings_unresolved" if servings is None else None, "提供食数を確認してください。"),
                              ("quantity_header_missing" if columns["quantity"] is None else None, "使用量列を特定できませんでした。"),
                              ("process_header_missing" if columns["process"] is None else None, "調理作業内容の列を特定できませんでした。")):
            if code:
                issues.append({"code": code, "message": message, "source": prefix})
        current = None
        for row_number, row in enumerate(rows[header + 1:], start=header + 2):
            if not any(row):
                # Whitespace is layout, never a new menu heading.
                continue
            source = f"{prefix}:row{row_number}"
            name = fields._cell(row, columns["menu"]).strip()
            method = fields._cell(row, columns["process"]).strip()
            if name.startswith("※"):
                issues.append({"code": "source_note_review", "message": "原本の注記を確認してください。", "source": source, "raw_value": " ".join(cell for cell in row if cell)})
                continue
            heading = fields._combined_menu_name(name)
            if heading is not None or current is None:
                current = {"facility_name": table.get("facility_name", ""), "service_date": service_date,
                           "raw_service_date": raw_date, "name": heading or "", "servings": servings,
                           "status": "needs_review", "ingredients": [], "cooking_instructions": "",
                           "cooking_instruction_sources": []}
                result["menus"].append(current)
                if heading is None or not heading:
                    issues.append({"code": "menu_heading_missing", "message": "献立見出しがありません。確認するまで登録できません。", "source": source, "raw_value": name})
            if method:
                current["cooking_instructions"] = "\n".join(value for value in (current["cooking_instructions"], method) if value)
                cell_sources = table.get("cell_sources", {}).get(row_number, {})
                current["cooking_instruction_sources"].append({"source": f"{filename}:{cell_sources.get('cooking_method_source', source)}", "text": method})
            if heading is not None:
                # A quantity on a heading is not a material quantity.
                continue
            if not name and not any(cell for index, cell in enumerate(row) if index != columns["process"]):
                continue
            raw_quantity = fields._cell(row, columns["quantity"]).strip()
            quantity, unit = fields._explicit_quantity_and_unit(raw_quantity)
            if not name:
                issues.append({"code": "ingredient_name_missing", "message": "食材名を確認してください。", "source": source, "raw_cells": row})
            if quantity is None:
                issues.append({"code": "quantity_unresolved", "message": "使用量と単位を確認してください。", "source": source, "raw_value": raw_quantity})
            source_fields = {key: value for key, value in table.get("cell_sources", {}).get(row_number, {}).items() if key != "cooking_method_source"}
            current["ingredients"].append({"name": name, "required_quantity": fields._decimal_string(quantity),
                                            "raw_quantity": raw_quantity, "unit": unit,
                                            "process_text": source_fields.get("cutting", ""),
                                            "process_steps": [], "source": source, "status": "needs_review", **source_fields})
    for menu in result["menus"]:
        if not menu["ingredients"]:
            result["issues"].append({"code": "menu_ingredients_missing", "message": "献立に食材行がありません。", "source": filename, "raw_value": menu["name"]})
        names = [item["name"] for item in menu["ingredients"]]
        for name in sorted({name for name in names if name and names.count(name) > 1}):
            result["issues"].append({"code": "duplicate_ingredient_name", "message": "同名の食材があります。数量と工程を確認してください。", "source": filename, "raw_value": name})
    if not result["menus"]:
        result["issues"].append({"code": "no_cooking_rows", "message": "確認画面へ取り込める献立・食材行がありませんでした。", "source": filename})
    return result
