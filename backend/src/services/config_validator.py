from typing import Any


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


def validate_facility_config(config: Any) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    config_dict = _ensure_dict(config, "config", errors)
    fax_override = _ensure_dict(config_dict.get("fax_template_override"), "fax_template_override", errors)
    _validate_columns(fax_override.get("columns"), "fax_template_override.columns", errors, warnings)
    _validate_packaging_policy(config_dict.get("packaging_policy_override"), errors)
    _validate_label_profile(config_dict.get("label_profile_override"), errors)
    _validate_invoice_template(config_dict.get("invoice_template"), errors, warnings)
    _validate_bag_types(config_dict.get("bag_types"), errors, warnings)
    return {"errors": errors, "warnings": warnings}


def validate_facility_master(master: Any) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    master_dict = _ensure_dict(master, "facility_master", errors)
    facilities = _ensure_list(master_dict.get("facilities"), "facilities", errors)
    for idx, fac in enumerate(facilities):
        if not isinstance(fac, dict):
            errors.append(f"facilities[{idx}] must be an object")
            continue
        if not fac.get("facility_id"):
            errors.append(f"facilities[{idx}].facility_id is required")
        if not fac.get("facility_name"):
            errors.append(f"facilities[{idx}].facility_name is required")
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
