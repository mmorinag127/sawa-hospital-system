from typing import Any
import re

from src.services.template_field_schema_service import derive_row_fields_from_columns


def _ensure_list(value: Any, path: str, errors: list[str]) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{path} must be a list")
        return []
    return value


def _ensure_dict(value: Any, path: str, errors: list[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {}
    return value


def _validate_columns(columns: Any, path: str, errors: list[str], warnings: list[str]) -> None:
    for idx, column in enumerate(_ensure_list(columns, path, errors)):
        if not isinstance(column, dict):
            errors.append(f"{path}[{idx}] must be an object")
            continue
        if "index" not in column:
            errors.append(f"{path}[{idx}].index is required")
        elif not isinstance(column.get("index"), int):
            errors.append(f"{path}[{idx}].index must be an integer")
        if "role" not in column:
            errors.append(f"{path}[{idx}].role is required")
        if column.get("role") in {"quantity", "quantity_change"}:
            if not column.get("diet_type"):
                warnings.append(f"{path}[{idx}].diet_type is missing for quantity role")


_MAIN_FIELD_PATTERN = re.compile(
    r"^(date_mmdd|date|daypart|menu|menu_name|remarks|note|aux\.[a-z0-9_]+|qty\.[a-z_]+_(?:x|\df|[a-z0-9_]+))$"
)


def _normalize_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _normalize_area_token(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw or raw in {"x", "common", "共通"}:
        return "x"
    if raw in {"2", "2f", "2階"}:
        return "2f"
    if raw in {"3", "3f", "3階"}:
        return "3f"
    return _normalize_token(raw) or "x"


def _derive_main_ocr_row_fields_from_columns(columns: Any) -> list[str]:
    return derive_row_fields_from_columns(
        [item for item in _ensure_list(columns, "columns", []) if isinstance(item, dict)]
    )


def _validate_column_field_consistency(
    columns: Any,
    fields: Any,
    path: str,
    errors: list[str],
) -> None:
    values = _ensure_list(fields, path, errors)
    normalized_fields = [str(item).strip() for item in values if isinstance(item, str) and str(item).strip()]
    if not normalized_fields:
        return
    derived = _derive_main_ocr_row_fields_from_columns(columns)
    if derived and derived != normalized_fields:
        errors.append(
            f"{path} does not match {path.rsplit('.', 1)[0]}.columns "
            f"(expected {derived}, got {normalized_fields})"
        )


def _validate_main_ocr_row_fields(
    fields: Any,
    path: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    values = _ensure_list(fields, path, errors)
    if not values:
        return
    seen: set[str] = set()
    quantity_count = 0
    for idx, raw in enumerate(values):
        item_path = f"{path}[{idx}]"
        if not isinstance(raw, str) or not raw.strip():
            errors.append(f"{item_path} must be a non-empty string")
            continue
        token = raw.strip()
        if token in seen:
            errors.append(f"{item_path} duplicated field: {token}")
            continue
        seen.add(token)
        if not _MAIN_FIELD_PATTERN.match(token):
            errors.append(
                f"{item_path} invalid field: {token} "
                "(expected date/daypart/menu/remarks or qty.<diet>_<x|2f|3f...>)"
            )
            continue
        if token.startswith("qty."):
            quantity_count += 1
    if quantity_count == 0:
        warnings.append(f"{path} has no qty.* columns")


def _validate_invoice_columns(columns: Any, path: str, errors: list[str]) -> None:
    for idx, column in enumerate(_ensure_list(columns, path, errors)):
        if not isinstance(column, dict):
            errors.append(f"{path}[{idx}] must be an object")
            continue
        name = column.get("name")
        source = column.get("source")
        if not isinstance(name, str) or not name:
            errors.append(f"{path}[{idx}].name is required")
        if not isinstance(source, str) or not source:
            errors.append(f"{path}[{idx}].source is required")
        diet_type = column.get("diet_type")
        if diet_type is not None and not isinstance(diet_type, str):
            errors.append(f"{path}[{idx}].diet_type must be a string")
        area_id = column.get("area_id")
        if area_id is not None and not isinstance(area_id, str):
            errors.append(f"{path}[{idx}].area_id must be a string")
        bag_type = column.get("bag_type")
        if bag_type is not None and not isinstance(bag_type, str):
            errors.append(f"{path}[{idx}].bag_type must be a string")


def _validate_invoice_template(template: Any, errors: list[str], warnings: list[str]) -> None:
    if template is None:
        return
    template_dict = _ensure_dict(template, "invoice_template", errors)
    _validate_invoice_columns(template_dict.get("columns"), "invoice_template.columns", errors)
    if template_dict.get("columns") and not template_dict.get("template_uri"):
        warnings.append("invoice_template.template_uri is missing")


def _validate_label_profile(profile: Any, errors: list[str]) -> None:
    if profile is None:
        return
    profile_dict = _ensure_dict(profile, "label_profile_override", errors)
    _ensure_list(profile_dict.get("label_fields"), "label_profile_override.label_fields", errors)


def _validate_packaging_policy(policy: Any, errors: list[str]) -> None:
    if policy is None:
        return
    policy_dict = _ensure_dict(policy, "packaging_policy_override", errors)
    _ensure_list(policy_dict.get("split_key"), "packaging_policy_override.split_key", errors)


def _validate_bag_types(bag_types: Any, errors: list[str], warnings: list[str]) -> None:
    for idx, bag_type in enumerate(_ensure_list(bag_types, "bag_types", errors)):
        if not isinstance(bag_type, dict):
            errors.append(f"bag_types[{idx}] must be an object")
            continue
        if not bag_type.get("bag_type_id"):
            errors.append(f"bag_types[{idx}].bag_type_id is required")
        if not bag_type.get("label"):
            warnings.append(f"bag_types[{idx}].label is missing")


def _ensure_number(value: Any, path: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)):
        errors.append(f"{path} must be a number")


def _validate_bbox(value: Any, path: str, errors: list[str]) -> None:
    box = _ensure_list(value, path, errors)
    if box and len(box) != 4:
        errors.append(f"{path} must have 4 elements")
    for idx, item in enumerate(box):
        if not isinstance(item, (int, float)):
            errors.append(f"{path}[{idx}] must be a number")


def _validate_template_match(match: Any, path: str, errors: list[str]) -> None:
    match_dict = _ensure_dict(match, path, errors)
    _ensure_number(match_dict.get("orb_nfeatures"), f"{path}.orb_nfeatures", errors)
    _ensure_number(match_dict.get("min_matches"), f"{path}.min_matches", errors)
    _ensure_number(match_dict.get("min_inlier_ratio"), f"{path}.min_inlier_ratio", errors)


def _validate_template_warp(warp: Any, path: str, errors: list[str]) -> None:
    warp_dict = _ensure_dict(warp, path, errors)
    output_size = _ensure_list(warp_dict.get("output_size"), f"{path}.output_size", errors)
    if output_size and len(output_size) != 2:
        errors.append(f"{path}.output_size must have 2 elements")
    for idx, item in enumerate(output_size):
        if not isinstance(item, (int, float)):
            errors.append(f"{path}.output_size[{idx}] must be a number")


def _validate_template_rois(rois: Any, path: str, errors: list[str]) -> None:
    rois_dict = _ensure_dict(rois, path, errors)
    if "facility_name_box" in rois_dict:
        _validate_bbox(rois_dict.get("facility_name_box"), f"{path}.facility_name_box", errors)
    if "menu_band" in rois_dict:
        _validate_bbox(rois_dict.get("menu_band"), f"{path}.menu_band", errors)
    if "notes_box" in rois_dict:
        _validate_bbox(rois_dict.get("notes_box"), f"{path}.notes_box", errors)

    qty = rois_dict.get("qty")
    if qty is None:
        return
    qty_dict = _ensure_dict(qty, f"{path}.qty", errors)
    schema = _ensure_dict(qty_dict.get("schema"), f"{path}.qty.schema", errors)
    rows = schema.get("rows")
    cols = schema.get("cols")
    if rows is not None and not isinstance(rows, int):
        errors.append(f"{path}.qty.schema.rows must be an integer")
    if cols is not None and not isinstance(cols, int):
        errors.append(f"{path}.qty.schema.cols must be an integer")
    _ensure_list(schema.get("row_names"), f"{path}.qty.schema.row_names", errors)
    _ensure_list(schema.get("col_names"), f"{path}.qty.schema.col_names", errors)

    boxes = _ensure_list(qty_dict.get("boxes_row_major"), f"{path}.qty.boxes_row_major", errors)
    for idx, box in enumerate(boxes):
        if not isinstance(box, list):
            errors.append(f"{path}.qty.boxes_row_major[{idx}] must be a list")
            continue
        if len(box) != 4:
            errors.append(f"{path}.qty.boxes_row_major[{idx}] must have 4 elements")
            continue
        for b_idx, item in enumerate(box):
            if not isinstance(item, (int, float)):
                errors.append(f"{path}.qty.boxes_row_major[{idx}][{b_idx}] must be a number")
    if isinstance(rows, int) and isinstance(cols, int) and rows > 0 and cols > 0:
        expected = rows * cols
        if boxes and len(boxes) < expected:
            errors.append(
                f"{path}.qty.boxes_row_major must include at least {expected} boxes"
            )


def _validate_template_postprocess(postprocess: Any, path: str, errors: list[str]) -> None:
    post_dict = _ensure_dict(postprocess, path, errors)
    qty_regex = post_dict.get("qty_regex")
    if qty_regex is not None and not isinstance(qty_regex, str):
        errors.append(f"{path}.qty_regex must be a string")
    if "normalize_fullwidth" in post_dict and not isinstance(
        post_dict.get("normalize_fullwidth"), bool
    ):
        errors.append(f"{path}.normalize_fullwidth must be a boolean")
    reject = post_dict.get("reject_repetition")
    if reject is not None:
        reject_dict = _ensure_dict(reject, f"{path}.reject_repetition", errors)
        _ensure_number(
            reject_dict.get("max_repeat_run"), f"{path}.reject_repetition.max_repeat_run", errors
        )
        _ensure_number(
            reject_dict.get("min_unique_line_ratio"),
            f"{path}.reject_repetition.min_unique_line_ratio",
            errors,
        )
    retry = post_dict.get("retry")
    if retry is not None:
        retry_dict = _ensure_dict(retry, f"{path}.retry", errors)
        _ensure_number(retry_dict.get("max_attempts"), f"{path}.retry.max_attempts", errors)
        crop_inset = _ensure_list(
            retry_dict.get("crop_inset_px"), f"{path}.retry.crop_inset_px", errors
        )
        if crop_inset and len(crop_inset) != 4:
            errors.append(f"{path}.retry.crop_inset_px must have 4 elements")
        if "alt_binarize" in retry_dict and not isinstance(
            retry_dict.get("alt_binarize"), bool
        ):
            errors.append(f"{path}.retry.alt_binarize must be a boolean")


_CELL_REF_RE = re.compile(r"^[A-Za-z]{1,3}[1-9][0-9]*$")


def _validate_order_form_patterns(
    patterns: Any,
    path: str,
    errors: list[str],
    warnings: list[str],
) -> set[str]:
    pattern_ids: set[str] = set()
    if patterns is None:
        return pattern_ids
    items = _ensure_list(patterns, path, errors)
    for idx, pattern in enumerate(items):
        item_path = f"{path}[{idx}]"
        if not isinstance(pattern, dict):
            errors.append(f"{item_path} must be an object")
            continue
        pattern_id = pattern.get("pattern_id")
        if not isinstance(pattern_id, str) or not pattern_id.strip():
            errors.append(f"{item_path}.pattern_id is required")
            continue
        pattern_id = pattern_id.strip()
        if pattern_id in pattern_ids:
            errors.append(f"{item_path}.pattern_id duplicated: {pattern_id}")
            continue
        pattern_ids.add(pattern_id)
        label = pattern.get("label")
        if label is not None and not isinstance(label, str):
            errors.append(f"{item_path}.label must be a string")
        description = pattern.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(f"{item_path}.description must be a string")
        marker_cells = pattern.get("marker_cells")
        if marker_cells is not None:
            cell_items = _ensure_list(marker_cells, f"{item_path}.marker_cells", errors)
            for cell_idx, cell in enumerate(cell_items):
                if not isinstance(cell, str) or not cell.strip():
                    errors.append(f"{item_path}.marker_cells[{cell_idx}] must be a non-empty string")
                    continue
                if not _CELL_REF_RE.match(cell.strip()):
                    warnings.append(
                        f"{item_path}.marker_cells[{cell_idx}] has unusual cell reference: {cell}"
                    )
        layout = pattern.get("layout")
        if layout is not None and not isinstance(layout, dict):
            errors.append(f"{item_path}.layout must be an object")
    return pattern_ids


def _validate_ocr_provider_config(config: dict, path: str, errors: list[str]) -> None:
    provider = config.get("main_ocr_provider")
    if provider is not None:
        if not isinstance(provider, str):
            errors.append(f"{path}.main_ocr_provider must be a string")
        elif provider.strip().lower() not in {"pipeline", "tesseract", "openai", "gemini"}:
            errors.append(
                f"{path}.main_ocr_provider must be one of pipeline|tesseract|openai|gemini"
            )
    enabled = config.get("openai_ocr_enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append(f"{path}.openai_ocr_enabled must be a boolean")
    model = config.get("openai_ocr_model")
    if model is not None and not isinstance(model, str):
        errors.append(f"{path}.openai_ocr_model must be a string")
    prompt = config.get("openai_ocr_prompt")
    if prompt is not None and not isinstance(prompt, str):
        errors.append(f"{path}.openai_ocr_prompt must be a string")
    max_tokens = config.get("openai_ocr_max_tokens")
    if max_tokens is not None and not isinstance(max_tokens, int):
        errors.append(f"{path}.openai_ocr_max_tokens must be an integer")
    openai_retry_on_truncation = config.get("openai_ocr_retry_on_truncation")
    if openai_retry_on_truncation is not None and not isinstance(openai_retry_on_truncation, bool):
        errors.append(f"{path}.openai_ocr_retry_on_truncation must be a boolean")
    openai_retry_max_tokens = config.get("openai_ocr_retry_max_tokens")
    if openai_retry_max_tokens is not None and not isinstance(openai_retry_max_tokens, int):
        errors.append(f"{path}.openai_ocr_retry_max_tokens must be an integer")
    timeout = config.get("openai_ocr_timeout_seconds")
    if timeout is not None and not isinstance(timeout, (int, float)):
        errors.append(f"{path}.openai_ocr_timeout_seconds must be a number")
    fallback = config.get("openai_ocr_fallback_provider")
    if fallback is not None:
        if not isinstance(fallback, str):
            errors.append(f"{path}.openai_ocr_fallback_provider must be a string")
        elif fallback.strip().lower() not in {"pipeline", "none"}:
            errors.append(
                f"{path}.openai_ocr_fallback_provider must be one of pipeline|none"
            )
    gemini_enabled = config.get("gemini_ocr_enabled")
    if gemini_enabled is not None and not isinstance(gemini_enabled, bool):
        errors.append(f"{path}.gemini_ocr_enabled must be a boolean")
    gemini_model = config.get("gemini_ocr_model")
    if gemini_model is not None and not isinstance(gemini_model, str):
        errors.append(f"{path}.gemini_ocr_model must be a string")
    gemini_prompt = config.get("gemini_ocr_prompt")
    if gemini_prompt is not None and not isinstance(gemini_prompt, str):
        errors.append(f"{path}.gemini_ocr_prompt must be a string")
    gemini_max_tokens = config.get("gemini_ocr_max_tokens")
    if gemini_max_tokens is not None and not isinstance(gemini_max_tokens, int):
        errors.append(f"{path}.gemini_ocr_max_tokens must be an integer")
    gemini_retry_on_truncation = config.get("gemini_ocr_retry_on_truncation")
    if gemini_retry_on_truncation is not None and not isinstance(gemini_retry_on_truncation, bool):
        errors.append(f"{path}.gemini_ocr_retry_on_truncation must be a boolean")
    gemini_retry_max_tokens = config.get("gemini_ocr_retry_max_tokens")
    if gemini_retry_max_tokens is not None and not isinstance(gemini_retry_max_tokens, int):
        errors.append(f"{path}.gemini_ocr_retry_max_tokens must be an integer")
    gemini_timeout = config.get("gemini_ocr_timeout_seconds")
    if gemini_timeout is not None and not isinstance(gemini_timeout, (int, float)):
        errors.append(f"{path}.gemini_ocr_timeout_seconds must be a number")
    gemini_fallback = config.get("gemini_ocr_fallback_provider")
    if gemini_fallback is not None:
        if not isinstance(gemini_fallback, str):
            errors.append(f"{path}.gemini_ocr_fallback_provider must be a string")
        elif gemini_fallback.strip().lower() not in {"pipeline", "none"}:
            errors.append(
                f"{path}.gemini_ocr_fallback_provider must be one of pipeline|none"
            )
    large_cell_mode = config.get("large_cell_mode")
    if large_cell_mode is not None and not isinstance(large_cell_mode, bool):
        errors.append(f"{path}.large_cell_mode must be a boolean")
    quantity_assignment_strategy = config.get("quantity_assignment_strategy")
    if quantity_assignment_strategy is not None:
        if not isinstance(quantity_assignment_strategy, str):
            errors.append(f"{path}.quantity_assignment_strategy must be a string")
        elif quantity_assignment_strategy.strip().lower() not in {"legacy", "hakodate", "both"}:
            errors.append(
                f"{path}.quantity_assignment_strategy must be one of legacy|hakodate|both"
            )
    hakodate_header_rows = config.get("hakodate_header_rows")
    if hakodate_header_rows is not None and not isinstance(hakodate_header_rows, int):
        errors.append(f"{path}.hakodate_header_rows must be an integer")
    hakodate_ocr_resolution = config.get("hakodate_ocr_resolution")
    if hakodate_ocr_resolution is not None and not isinstance(hakodate_ocr_resolution, int):
        errors.append(f"{path}.hakodate_ocr_resolution must be an integer")
    hakodate_min_edge_margin_ratio = config.get("hakodate_min_edge_margin_ratio")
    if hakodate_min_edge_margin_ratio is not None and not isinstance(
        hakodate_min_edge_margin_ratio,
        (int, float),
    ):
        errors.append(f"{path}.hakodate_min_edge_margin_ratio must be a number")
    hakodate_data_row_count = config.get("hakodate_data_row_count")
    if hakodate_data_row_count is not None and not isinstance(hakodate_data_row_count, int):
        errors.append(f"{path}.hakodate_data_row_count must be an integer")
    hakodate_template_signature = config.get("hakodate_template_signature")
    if hakodate_template_signature is not None and not isinstance(hakodate_template_signature, str):
        errors.append(f"{path}.hakodate_template_signature must be a string")
    hakodate_template_signature_components = config.get("hakodate_template_signature_components")
    if hakodate_template_signature_components is not None and not isinstance(
        hakodate_template_signature_components,
        dict,
    ):
        errors.append(f"{path}.hakodate_template_signature_components must be an object")


def validate_facility_config(config: Any) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    config_dict = _ensure_dict(config, "config", errors)
    _validate_ocr_provider_config(config_dict, "config", errors)
    pattern_id = config_dict.get("order_form_pattern_id")
    if pattern_id is not None and not isinstance(pattern_id, str):
        errors.append("config.order_form_pattern_id must be a string")
    fax_override = _ensure_dict(config_dict.get("fax_template_override"), "fax_template_override", errors)
    _validate_columns(fax_override.get("columns"), "fax_template_override.columns", errors, warnings)
    _validate_main_ocr_row_fields(
        fax_override.get("main_ocr_row_fields"),
        "fax_template_override.main_ocr_row_fields",
        errors,
        warnings,
    )
    _validate_column_field_consistency(
        fax_override.get("columns"),
        fax_override.get("main_ocr_row_fields"),
        "fax_template_override.main_ocr_row_fields",
        errors,
    )
    fax_template = _ensure_dict(config_dict.get("fax_template"), "fax_template", errors)
    _validate_main_ocr_row_fields(
        fax_template.get("main_ocr_row_fields"),
        "fax_template.main_ocr_row_fields",
        errors,
        warnings,
    )
    _validate_column_field_consistency(
        fax_template.get("columns"),
        fax_template.get("main_ocr_row_fields"),
        "fax_template.main_ocr_row_fields",
        errors,
    )
    _validate_packaging_policy(config_dict.get("packaging_policy_override"), errors)
    _validate_label_profile(config_dict.get("label_profile_override"), errors)
    _validate_invoice_template(config_dict.get("invoice_template"), errors, warnings)
    _validate_bag_types(config_dict.get("bag_types"), errors, warnings)
    return {"errors": errors, "warnings": warnings}


def validate_facility_master(master: Any) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    master_dict = _ensure_dict(master, "facility_master", errors)
    base_template = _ensure_dict(master_dict.get("fax_template_base"), "fax_template_base", errors)
    _validate_main_ocr_row_fields(
        base_template.get("main_ocr_row_fields"),
        "fax_template_base.main_ocr_row_fields",
        errors,
        warnings,
    )
    pattern_ids = _validate_order_form_patterns(
        master_dict.get("order_form_patterns"),
        "order_form_patterns",
        errors,
        warnings,
    )
    facilities = _ensure_list(master_dict.get("facilities"), "facilities", errors)
    for idx, fac in enumerate(facilities):
        if not isinstance(fac, dict):
            errors.append(f"facilities[{idx}] must be an object")
            continue
        if not fac.get("facility_id"):
            errors.append(f"facilities[{idx}].facility_id is required")
        if not fac.get("facility_name"):
            errors.append(f"facilities[{idx}].facility_name is required")
        _validate_ocr_provider_config(fac, f"facilities[{idx}]", errors)
        fax_template_ids = fac.get("fax_template_ids")
        if fax_template_ids is not None:
            template_ids = _ensure_list(
                fax_template_ids,
                f"facilities[{idx}].fax_template_ids",
                errors,
            )
            for item_idx, template_id in enumerate(template_ids):
                if not isinstance(template_id, str) or not template_id.strip():
                    errors.append(
                        f"facilities[{idx}].fax_template_ids[{item_idx}] must be a non-empty string"
                    )
        pattern_id = fac.get("order_form_pattern_id")
        if pattern_id is not None and not isinstance(pattern_id, str):
            errors.append(f"facilities[{idx}].order_form_pattern_id must be a string")
        if (
            isinstance(pattern_id, str)
            and pattern_id.strip()
            and pattern_ids
            and pattern_id.strip() not in pattern_ids
        ):
            errors.append(
                f"facilities[{idx}].order_form_pattern_id not found in order_form_patterns: {pattern_id.strip()}"
            )
        _ensure_list(fac.get("aliases"), f"facilities[{idx}].aliases", errors)
        _ensure_list(fac.get("areas"), f"facilities[{idx}].areas", errors)
        fax_override = _ensure_dict(
            fac.get("fax_template_override"), f"facilities[{idx}].fax_template_override", errors
        )
        _validate_columns(
            fax_override.get("columns"),
            f"facilities[{idx}].fax_template_override.columns",
            errors,
            warnings,
        )
        _validate_main_ocr_row_fields(
            fax_override.get("main_ocr_row_fields"),
            f"facilities[{idx}].fax_template_override.main_ocr_row_fields",
            errors,
            warnings,
        )
        _validate_column_field_consistency(
            fax_override.get("columns"),
            fax_override.get("main_ocr_row_fields"),
            f"facilities[{idx}].fax_template_override.main_ocr_row_fields",
            errors,
        )
        fax_template = _ensure_dict(
            fac.get("fax_template"),
            f"facilities[{idx}].fax_template",
            errors,
        )
        _validate_main_ocr_row_fields(
            fax_template.get("main_ocr_row_fields"),
            f"facilities[{idx}].fax_template.main_ocr_row_fields",
            errors,
            warnings,
        )
        _validate_column_field_consistency(
            fax_template.get("columns"),
            fax_template.get("main_ocr_row_fields"),
            f"facilities[{idx}].fax_template.main_ocr_row_fields",
            errors,
        )
        _validate_packaging_policy(fac.get("packaging_policy_override"), errors)
        _validate_label_profile(fac.get("label_profile_override"), errors)
        _validate_invoice_template(fac.get("invoice_template"), errors, warnings)
        _validate_bag_types(fac.get("bag_types"), errors, warnings)
    return {"errors": errors, "warnings": warnings}


def validate_fax_template_registry(registry: Any) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    registry_dict = _ensure_dict(registry, "fax_template_registry", errors)
    templates = registry_dict.get("templates")
    if templates is None:
        templates = registry_dict
    templates_dict = _ensure_dict(templates, "fax_template_registry.templates", errors)
    for key, template in templates_dict.items():
        if not isinstance(template, dict):
            errors.append(f"fax_template_registry.templates.{key} must be an object")
            continue
        if not template.get("template_id"):
            errors.append(f"fax_template_registry.templates.{key}.template_id is required")
        _validate_template_match(template.get("match"), f"fax_template_registry.templates.{key}.match", errors)
        _validate_template_warp(template.get("warp"), f"fax_template_registry.templates.{key}.warp", errors)
        _validate_template_rois(template.get("rois"), f"fax_template_registry.templates.{key}.rois", errors)
        _validate_template_postprocess(
            template.get("postprocess"),
            f"fax_template_registry.templates.{key}.postprocess",
            errors,
        )
    return {"errors": errors, "warnings": warnings}
