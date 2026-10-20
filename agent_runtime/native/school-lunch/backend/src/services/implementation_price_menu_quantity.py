"""Price draft portions come from the original handout, not rounded order totals."""
import re
from decimal import Decimal

from src.models.school_lunch import MenuExecution
from src.models.school_lunch_document import SchoolLunchDocument
from src.services.implementation_price_scope_service import require_facility, require_same_facility, PriceScopeError
from src.services.implementation_price_invoice_normalization import unit


def menu_price_quantities(session, *, menu_id, facility_id):
    facility = require_facility(session, facility_id)
    menu = session.get(MenuExecution, menu_id)
    if menu is None:
        raise PriceScopeError("献立実施がありません。")
    require_same_facility(menu.facility_id, facility.id)
    document = session.get(SchoolLunchDocument, menu.source_document_id) if menu.source_document_id else None
    if document is not None:
        require_same_facility(document.facility_id, facility.id)
    result = document.extracted_json if document is not None else None
    if document is not None and document.ocr_job_id:
        from src.services.implementation_price_ocr_job_service import validated_successful_result
        try:
            result = validated_successful_result(session, document.ocr_job_id, facility_id=facility.id,
                document_type="cooking_arrangement", document_id=document.id, for_adoption=False)
        except ValueError as exc:
            raise PriceScopeError(str(exc)) from exc
    candidates = [candidate for candidate in (result or {}).get("menus", [])
                  if candidate.get("name") == menu.name and candidate.get("service_date") == menu.service_date.isoformat()]
    original = candidates[0] if len(candidates) == 1 and candidates[0].get("facility_name") == facility.name else None
    rows = []
    for line in menu.menu_ingredients:
        row = {"ingredient_id": line.ingredient_id, "amount_per_person": None, "unit": None,
               "source": "", "issue": "手配表の1人量を原本で確認して入力してください。"}
        originals = [value for value in (original or {}).get("ingredients", []) if value.get("name") == line.ingredient.name]
        if len(originals) == 1 and original.get("servings") == menu.servings:
            value = originals[0]
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(g|kg|Kg|個|本|袋)\s*", str(value.get("amount_per_person") or ""))
            # Edited menu quantities must not silently revert to its older source.
            quantity_text = str(value.get("required_quantity") or "")
            quantity_unchanged = bool(re.fullmatch(r"\d+(?:\.\d+)?", quantity_text)) and Decimal(quantity_text) == line.required_quantity
            unit_unchanged = unit(value.get("unit")) == unit(line.unit)
            if match and quantity_unchanged and unit_unchanged:
                row.update(amount_per_person=str(Decimal(match[1])), unit=unit(match[2]),
                           source=f"手配表 {document.id} / {value.get('amount_per_person_source') or value.get('source')}", issue="")
        rows.append(row)
    return {"menu_execution_id": menu.id, "facility_id": facility.id, "service_date": menu.service_date.isoformat(), "quantity_items": rows}
