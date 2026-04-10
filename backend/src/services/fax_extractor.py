from dataclasses import dataclass
from typing import Any, List, Optional
from io import BytesIO
import os
import re
import uuid

from loguru import logger

from src.services import config_service
from src.services.grid_detector import GridDetectionResult, detect_table_grid
from src.services.ocr_pipeline_service import run_ocr_pipeline


@dataclass
class FaxExtractedData:
    facility_name: Optional[str]
    date_strings: List[str]
    table_rows: List[List[str]]
    tokens: List[dict]
    grid: Optional[GridDetectionResult] = None
    ocr_provider: Optional[str] = None
    raw_text: Optional[str] = None
    provider_debug: Optional[dict] = None


def _get_main_provider(template: dict) -> str:
    forced_provider = template.get("_force_main_ocr_provider")
    if forced_provider:
        return str(forced_provider).lower()
    env_provider = os.getenv("OCR_MAIN_PROVIDER")
    if env_provider:
        return env_provider.lower()
    provider = template.get("main_ocr_provider")
    if provider:
        normalized = str(provider).lower()
        if normalized == "openai" and template.get("openai_ocr_enabled") is False:
            return "pipeline"
        if normalized == "gemini" and template.get("gemini_ocr_enabled") is False:
            return "pipeline"
        return normalized
    return "pipeline"


def _get_resolution(template: dict, key: str, fallback: int = 320) -> int:
    value = template.get(key)
    if value:
        return int(value)
    return int(template.get("token_ocr_resolution", fallback))


def _crop_to_bbox(image, bbox: list[float]):
    width, height = image.size
    x0 = int(max(bbox[0] * width, 0))
    y0 = int(max(bbox[1] * height, 0))
    x1 = int(min(bbox[2] * width, width))
    y1 = int(min(bbox[3] * height, height))
    if x1 <= x0 or y1 <= y0:
        return None
    return image.crop((x0, y0, x1, y1))


def _ocr_crop_text(crop) -> str:
    import pytesseract

    return pytesseract.image_to_string(crop, config="--psm 7").strip()


def _extract_tesseract_tokens(image) -> list[dict]:
    import pytesseract

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    width, height = image.size
    tokens: list[dict] = []
    for text, left, top, w, h in zip(
        data.get("text", []),
        data.get("left", []),
        data.get("top", []),
        data.get("width", []),
        data.get("height", []),
    ):
        if not text or not str(text).strip():
            continue
        x = (left + w / 2) / width
        y = (top + h / 2) / height
        tokens.append({"text": str(text).strip(), "x": x, "y": y})
    return tokens


def _map_tokens_to_box(tokens: list[dict], bbox: list[float]) -> list[dict]:
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return tokens
    mapped = []
    for token in tokens:
        x = token.get("x")
        y = token.get("y")
        if x is None or y is None:
            continue
        mapped.append(
            {
                **token,
                "x": x0 + x * width,
                "y": y0 + y * height,
            }
        )
    return mapped


def _coerce_row_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return str(value)


def _resolve_payload_template(
    payload: object,
    template: dict | None,
    *,
    allow_payload_template_override: bool = True,
) -> dict:
    resolved = template if isinstance(template, dict) else {}
    if not allow_payload_template_override:
        return resolved
    if not isinstance(payload, dict):
        return resolved
    template_id = payload.get("template_id")
    if not isinstance(template_id, str):
        classification = payload.get("classification")
        if isinstance(classification, dict):
            template_id = classification.get("matched_template_id")
    if not isinstance(template_id, str):
        return resolved
    template_id = template_id.strip()
    if not template_id:
        return resolved
    if resolved.get("template_id") == template_id:
        return resolved
    try:
        registry = config_service.load_fax_template_registry()
    except Exception:
        return resolved
    matched = registry.get(template_id)
    if isinstance(matched, dict) and matched:
        return matched
    return resolved


def _get_row_fields(template: dict) -> list[str]:
    fields = template.get("main_ocr_row_fields")
    if isinstance(fields, list):
        return [str(field) for field in fields if str(field).strip()]
    return []


def _resolve_row_field(row: dict, field: str) -> object:
    # LLM outputs may use flat dotted keys (e.g. "qty.regular_2f").
    # Prefer exact key lookup before nested traversal.
    if isinstance(row, dict) and field in row:
        return row.get(field)
    current: object = row
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _rows_from_payload(payload: object, template: dict) -> list[list[str]] | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None
    fields = _get_row_fields(template)
    if not fields:
        return None
    normalized: list[list[str]] = []
    indexed_rows: dict[int, list[str]] = {}
    for row in rows:
        if isinstance(row, list):
            normalized.append([_coerce_row_cell(cell) for cell in row])
            continue
        if isinstance(row, dict):
            row_values = [_coerce_row_cell(_resolve_row_field(row, field)) for field in fields]
            row_index_raw = row.get("row_index")
            try:
                row_index = int(row_index_raw) if row_index_raw is not None else None
            except Exception:
                row_index = None
            if row_index is not None and row_index >= 0:
                indexed_rows[row_index] = row_values
            else:
                normalized.append(row_values)
    if indexed_rows:
        width = len(fields)
        max_index = max(indexed_rows.keys())
        indexed_normalized = [["" for _ in range(width)] for _ in range(max_index + 1)]
        for idx, row_values in indexed_rows.items():
            indexed_normalized[idx] = row_values
        normalized = indexed_normalized + normalized
    if not normalized:
        return None
    return normalized


def _rows_from_pipeline_payload(
    payload: object,
    template: dict,
    *,
    allow_payload_template_override: bool = True,
) -> list[list[str]] | None:
    template = _resolve_payload_template(
        payload,
        template,
        allow_payload_template_override=allow_payload_template_override,
    )
    if not isinstance(payload, dict):
        return None
    base_rows: list[list[str]] | None = None
    table_rows = payload.get("table_rows")
    if isinstance(table_rows, list):
        normalized: list[list[str]] = []
        for row in table_rows:
            if not isinstance(row, list):
                continue
            normalized.append([_coerce_row_cell(cell) for cell in row])
        if normalized:
            base_rows = normalized
    rows = _rows_from_payload(payload, template)
    if rows:
        base_rows = rows
    if base_rows is None:
        rows = rows_from_structured_payload(
            payload,
            template,
            allow_payload_template_override=allow_payload_template_override,
        )
        if rows:
            base_rows = rows
    if base_rows is None:
        table_raw = payload.get("table_raw")
        if isinstance(table_raw, str) and table_raw.strip():
            rows = _rows_from_markdown_blocks(table_raw, template)
            if rows:
                base_rows = rows
    if base_rows is None:
        nested = payload.get("table")
        if isinstance(nested, dict):
            rows = _rows_from_payload(nested, template)
            if rows:
                base_rows = rows
            if base_rows is None:
                rows = rows_from_structured_payload(
                    nested,
                    template,
                    allow_payload_template_override=allow_payload_template_override,
                )
                if rows:
                    base_rows = rows
    if base_rows is None:
        qty = payload.get("qty")
        if isinstance(qty, dict):
            fields = _get_row_fields(template)
            if fields:
                menu_band = payload.get("menu_band") or ""
                menu_lines = [line.strip() for line in str(menu_band).splitlines() if line.strip()]
                row_order = payload.get("qty_row_order")
                if not isinstance(row_order, list):
                    row_order = list(qty.keys())
                normalized: list[list[str]] = []
                for idx, row_key in enumerate(row_order):
                    row_data: dict[str, object] = {}
                    if menu_lines:
                        menu_value = menu_lines[idx] if idx < len(menu_lines) else ""
                        row_data["menu"] = menu_value
                        row_data["menu_name"] = menu_value
                    row_qty = qty.get(row_key)
                    if isinstance(row_qty, dict):
                        row_data["qty"] = dict(row_qty)
                    normalized.append(
                        [_coerce_row_cell(_resolve_row_field(row_data, field)) for field in fields]
                    )
                if normalized:
                    base_rows = normalized
    overlay_rows = (
        _rows_from_overlay_payload(
            payload,
            template,
            allow_payload_template_override=allow_payload_template_override,
        )
        if _overlay_merge_enabled(payload)
        else None
    )
    return _merge_overlay_rows(base_rows, overlay_rows, template)


def _extract_provider_debug(output: object) -> tuple[str | None, dict | None]:
    if not isinstance(output, dict):
        return None, None
    raw_text = output.get("_ocr_raw_text")
    raw_text_value = raw_text.strip() if isinstance(raw_text, str) and raw_text.strip() else None
    provider_debug = output.get("_ocr_debug")
    debug_value = dict(provider_debug) if isinstance(provider_debug, dict) else None
    return raw_text_value, debug_value


def _normalize_header_token(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"\s+", "", normalized)
    translation = str.maketrans(
        {
            "０": "0",
            "１": "1",
            "２": "2",
            "３": "3",
            "４": "4",
            "５": "5",
            "６": "6",
            "７": "7",
            "８": "8",
            "９": "9",
            "ｆ": "f",
            "Ｆ": "f",
        }
    )
    return normalized.translate(translation)


def _select_field(candidates: list[str], fields: set[str]) -> str | None:
    for candidate in candidates:
        if candidate in fields:
            return candidate
    return None


def _count_mapped_header_cells(header: list[str], fields: set[str]) -> int:
    count = 0
    for cell in header:
        if _field_from_header(cell, fields):
            count += 1
    return count


def _contains_japanese_text(value: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ヶ一-龥]", value))


def _looks_like_date(value: str) -> bool:
    return bool(re.search(r"\d{1,2}/\d{1,2}", value))


def _looks_like_daypart(value: str) -> bool:
    return any(marker in value for marker in ("朝", "昼", "夕", "夜"))


def _looks_like_quantity(value: str) -> bool:
    return bool(re.fullmatch(r"\s*[+-]?\d+(?:\.\d+)?\s*", value))


def _is_quantity_field_name(field: str) -> bool:
    normalized = field.strip().lower()
    if not normalized:
        return False
    if normalized.startswith("qty.") or normalized.startswith("qty_"):
        return True
    if normalized.startswith("quantity"):
        return True
    if normalized.endswith("_x") and any(
        token in normalized
        for token in (
            "regular",
            "soft",
            "mixer",
            "no_meat",
            "no_fish",
            "jelly",
        )
    ):
        return True
    return False


def _is_overlay_replaceable_field(field: str) -> bool:
    normalized = field.strip().lower()
    if not normalized:
        return False
    if _is_quantity_field_name(normalized):
        return True
    return normalized in {"remarks", "note", "notes"}


def _rows_from_overlay_payload(
    payload: object,
    template: dict,
    *,
    allow_payload_template_override: bool = True,
) -> list[list[str]] | None:
    template = _resolve_payload_template(
        payload,
        template,
        allow_payload_template_override=allow_payload_template_override,
    )
    if not isinstance(payload, dict):
        return None
    overlay_rows = payload.get("roi_overlay_rows")
    if isinstance(overlay_rows, list):
        rows = _rows_from_payload({"rows": overlay_rows}, template)
        if rows:
            return rows
    roi_extraction = payload.get("roi_extraction")
    if isinstance(roi_extraction, dict):
        overlay_rows = roi_extraction.get("overlay_rows")
        if isinstance(overlay_rows, list):
            rows = _rows_from_payload({"rows": overlay_rows}, template)
            if rows:
                return rows
    return None


def _overlay_merge_enabled(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    policy = str(payload.get("roi_overlay_policy") or "").strip().lower()
    if policy in {"merge", "enabled", "replace"}:
        return True
    if policy in {"audit_only", "disabled", "off", "none"}:
        return False
    return str(os.getenv("OCR_ENABLE_ROI_OVERLAY_MERGE", "false")).strip().lower() == "true"


def _merge_overlay_rows(
    base_rows: list[list[str]] | None,
    overlay_rows: list[list[str]] | None,
    template: dict,
) -> list[list[str]] | None:
    if not overlay_rows:
        return base_rows
    fields = _get_row_fields(template)
    width = len(fields)
    if width <= 0:
        width = max((len(row) for row in overlay_rows), default=0)
        width = max(width, max((len(row) for row in (base_rows or [])), default=0))
    if width <= 0:
        return base_rows or overlay_rows

    merged = [list(row[:width]) + [""] * max(0, width - len(row)) for row in (base_rows or [])]
    if not merged:
        merged = [["" for _ in range(width)] for _ in range(len(overlay_rows))]
    for row_index, overlay_row in enumerate(overlay_rows):
        while row_index >= len(merged):
            merged.append(["" for _ in range(width)])
        normalized_overlay = list(overlay_row[:width]) + [""] * max(0, width - len(overlay_row))
        target = merged[row_index]
        for col_index in range(width):
            overlay_value = _coerce_row_cell(normalized_overlay[col_index])
            if overlay_value == "":
                continue
            field = fields[col_index] if col_index < len(fields) else ""
            current_value = _coerce_row_cell(target[col_index]) if col_index < len(target) else ""
            if current_value and not _is_overlay_replaceable_field(field):
                continue
            target[col_index] = overlay_value
    return merged


def _best_column_index(
    data: list[list[str]],
    *,
    max_columns: int,
    exclude_src: set[int],
    scorer,
    minimum_score: int = 1,
) -> int | None:
    best_idx: int | None = None
    best_score = 0
    for idx in range(max_columns):
        if idx in exclude_src:
            continue
        score = 0
        for row in data:
            if idx >= len(row):
                continue
            value = str(row[idx] or "").strip()
            if not value:
                continue
            if scorer(value):
                score += 1
        if score > best_score:
            best_score = score
            best_idx = idx
    if best_idx is None or best_score < minimum_score:
        return None
    return best_idx


def _infer_mapped_indexes(
    *,
    data: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
) -> dict[int, int]:
    if not data:
        return mapped_indexes
    max_columns = max((len(row) for row in data), default=0)
    if max_columns <= 0:
        return mapped_indexes

    used_src = set(mapped_indexes.keys())
    used_dest = set(mapped_indexes.values())
    field_to_dest = {field: idx for idx, field in enumerate(fields)}

    def _ensure_field(
        field: str,
        scorer,
        minimum_score: int = 1,
    ) -> None:
        dest_idx = field_to_dest.get(field)
        if dest_idx is None or dest_idx in used_dest:
            return
        src_idx = _best_column_index(
            data,
            max_columns=max_columns,
            exclude_src=used_src,
            scorer=scorer,
            minimum_score=minimum_score,
        )
        if src_idx is None:
            return
        mapped_indexes[src_idx] = dest_idx
        used_src.add(src_idx)
        used_dest.add(dest_idx)

    _ensure_field("date_mmdd", _looks_like_date)
    _ensure_field("date", _looks_like_date)
    _ensure_field("daypart", _looks_like_daypart)
    _ensure_field(
        "menu",
        lambda value: _contains_japanese_text(value) and not _looks_like_quantity(value),
        minimum_score=2,
    )
    _ensure_field(
        "menu_name",
        lambda value: _contains_japanese_text(value) and not _looks_like_quantity(value),
        minimum_score=2,
    )

    quantity_fields = [
        field
        for field in fields
        if _is_quantity_field_name(field)
    ]
    for field in quantity_fields:
        _ensure_field(field, _looks_like_quantity)
    return mapped_indexes


def _field_from_header(header: str, fields: set[str]) -> str | None:
    token = _normalize_header_token(header)
    quantity_floor = None
    if "2f" in token or "2階" in token or "花" in token:
        quantity_floor = "2f"
    elif "3f" in token or "3階" in token or "月" in token:
        quantity_floor = "3f"
    if "備考" in token or "remarks" in token or "note" in token:
        return _select_field(["remarks", "note"], fields)
    if "献立" in token or "メニュー" in token or "menu" in token:
        return _select_field(["menu", "menu_name"], fields)
    if "日付" in token or token.startswith("日"):
        return _select_field(["date_mmdd", "date"], fields)
    if "区分" in token or "時間帯" in token:
        return _select_field(["daypart"], fields)
    if ("袋" in token or "bag" in token) and (
        "常食" in token or "regular" in token or "通常" in token or "常" in token
    ):
        return _select_field(["qty.regular_bag_x", "regular_bag_x"], fields)
    if "常食" in token or "regular" in token or "常" in token:
        if quantity_floor == "2f":
            return _select_field(["qty.regular_2f", "regular_2f", "qty.regular_x", "regular_x"], fields)
        if quantity_floor == "3f":
            return _select_field(["qty.regular_3f", "regular_3f", "qty.regular_x", "regular_x"], fields)
        return _select_field(
            [
                "qty.regular_x",
                "regular_x",
                "qty.regular_2f",
                "regular_2f",
                "qty.regular_3f",
                "regular_3f",
            ],
            fields,
        )
    if "軟菜" in token or "soft" in token or "軟" in token:
        if quantity_floor == "2f":
            return _select_field(["qty.soft_2f", "soft_2f", "qty.soft_x", "soft_x"], fields)
        if quantity_floor == "3f":
            return _select_field(["qty.soft_3f", "soft_3f", "qty.soft_x", "soft_x"], fields)
        return _select_field(
            ["qty.soft_x", "soft_x", "qty.soft_2f", "soft_2f", "qty.soft_3f", "soft_3f"],
            fields,
        )
    if "ミキサ" in token or "mixer" in token or "ミキ" in token:
        if quantity_floor == "2f":
            return _select_field(["qty.mixer_2f", "mixer_2f", "qty.mixer_x", "mixer_x"], fields)
        if quantity_floor == "3f":
            return _select_field(["qty.mixer_3f", "mixer_3f", "qty.mixer_x", "mixer_x"], fields)
        return _select_field(
            [
                "qty.mixer_x",
                "mixer_x",
                "qty.mixer_2f",
                "mixer_2f",
                "qty.mixer_3f",
                "mixer_3f",
            ],
            fields,
        )
    if "職員" in token or "staff" in token:
        return _select_field(["qty.staff_x", "staff_x"], fields)
    if "お茶" in token or "tea" in token:
        return _select_field(["qty.tea_x", "tea_x"], fields)
    if "事業" in token or "business" in token:
        return _select_field(["qty.business_x", "business_x"], fields)
    if "通所" in token or "daycare" in token:
        return _select_field(["qty.daycare_x", "daycare_x"], fields)
    if "揚げ物禁" in header or "揚物禁" in header or "nofried" in token:
        return _select_field(["qty.no_fried_x", "no_fried_x"], fields)
    if "糖尿" in token or "diabetes" in token:
        return _select_field(["qty.糖尿_x", "糖尿_x", "qty.diabetes_x", "diabetes_x"], fields)
    if "妊娠" in token or "pregnancy" in token:
        return _select_field(["qty.pregnancy_x", "pregnancy_x"], fields)
    if ("ごま" in token or "ゴマ" in header or "sesame" in token) and (
        "アレル" in header or "allergy" in token
    ):
        return _select_field(["qty.sesame_allergy_x", "sesame_allergy_x"], fields)
    if (
        ("肉" in token or "meat" in token)
        and ("卵" in token or "玉子" in token or "egg" in token)
        and ("魚" in token or "鯖" in token or "さば" in token or "fish" in token)
    ) or "肉卵魚禁" in header:
        return _select_field(["qty.forbidden_other_x", "forbidden_other_x"], fields)
    if ("禁" in token and "肉" in token) or "nomeat" in token:
        return _select_field(
            ["qty.no_meat_x", "no_meat_x", "qty.no_meat_2f", "no_meat_2f", "qty.no_meat_3f", "no_meat_3f"],
            fields,
        )
    if ("禁" in token and "魚" in token) or "nofish" in token:
        return _select_field(
            ["qty.no_fish_x", "no_fish_x", "qty.no_fish_2f", "no_fish_2f", "qty.no_fish_3f", "no_fish_3f"],
            fields,
        )
    if "禁食" in token:
        return _select_field(["qty.禁食_x", "禁食_x", "qty.forbidden_x", "forbidden_x"], fields)
    if "変更1" in header or "change1" in token:
        return _select_field(["qty.change_1_x", "change_1_x"], fields)
    if "変更2" in header or "change2" in token:
        return _select_field(["qty.change_2_x", "change_2_x"], fields)
    if token in {"-", "placeholder"}:
        return _select_field(["qty.placeholder_x", "placeholder_x"], fields)
    return None


def _template_explicit_quantity_column_count(template: dict | None) -> int:
    if not isinstance(template, dict):
        return 0
    columns = template.get("columns")
    if not isinstance(columns, list):
        return 0
    return sum(
        1
        for col in columns
        if isinstance(col, dict) and str(col.get("role") or "").strip().lower() == "quantity"
    )


def _explicit_field_from_template_column(column: dict[str, Any], fields: list[str]) -> str | None:
    role = str(column.get("role") or "").strip().lower()
    if role == "date":
        return "date_mmdd" if "date_mmdd" in fields else ("date" if "date" in fields else None)
    if role == "daypart":
        return "daypart" if "daypart" in fields else None
    if role == "menu_name":
        return "menu" if "menu" in fields else ("menu_name" if "menu_name" in fields else None)
    if role == "note":
        return "remarks" if "remarks" in fields else ("note" if "note" in fields else None)
    if role == "quantity":
        name = str(column.get("name") or "").strip()
        if name and name in fields:
            return name
        diet_type = str(column.get("diet_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
        area_id = str(column.get("area_id") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if diet_type and area_id:
            derived_name = f"qty.{diet_type}_{area_id}"
            if derived_name in fields:
                return derived_name
    return None


def _mapped_indexes_from_template_columns(
    *,
    template: dict | None,
    fields: list[str],
    observed_width: int,
) -> dict[int, int]:
    if not isinstance(template, dict) or observed_width <= 0:
        return {}
    columns = template.get("columns")
    if not isinstance(columns, list):
        return {}
    explicit: dict[int, int] = {}
    used_dest_indexes: set[int] = set()
    for raw_col in columns:
        if not isinstance(raw_col, dict):
            continue
        try:
            source_col_index = int(raw_col.get("index"))
        except Exception:
            continue
        if source_col_index < 0 or source_col_index >= observed_width:
            continue
        field = _explicit_field_from_template_column(raw_col, fields)
        if not field:
            continue
        dest_idx = fields.index(field)
        if dest_idx in used_dest_indexes:
            continue
        explicit[source_col_index] = dest_idx
        used_dest_indexes.add(dest_idx)
    return explicit


def _overlay_explicit_mapped_indexes(
    mapped_indexes: dict[int, int],
    explicit_mapped_indexes: dict[int, int],
) -> dict[int, int]:
    if not explicit_mapped_indexes:
        return mapped_indexes
    explicit_dest_indexes = set(explicit_mapped_indexes.values())
    normalized = {
        src_idx: dest_idx
        for src_idx, dest_idx in mapped_indexes.items()
        if src_idx not in explicit_mapped_indexes and dest_idx not in explicit_dest_indexes
    }
    normalized.update(explicit_mapped_indexes)
    return normalized


def _fill_remaining_quantity_mapping_by_order(
    *,
    data: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
) -> dict[int, int]:
    if not data or not fields:
        return mapped_indexes
    max_columns = max((len(row) for row in data), default=0)
    if max_columns <= 0:
        return mapped_indexes

    used_src = set(mapped_indexes.keys())
    used_dest = set(mapped_indexes.values())
    remaining_dest_quantity = [
        idx
        for idx, field in enumerate(fields)
        if _is_quantity_field_name(field) and idx not in used_dest
    ]
    if not remaining_dest_quantity:
        return mapped_indexes

    remaining_src_quantity = [
        idx
        for idx in range(max_columns)
        if idx not in used_src
        and any(idx < len(row) and _looks_like_quantity(str(row[idx] or "")) for row in data)
    ]
    if len(remaining_src_quantity) != len(remaining_dest_quantity):
        return mapped_indexes

    for src_idx, dest_idx in zip(remaining_src_quantity, remaining_dest_quantity):
        mapped_indexes[src_idx] = dest_idx
    return mapped_indexes


def _realign_quantity_mapping_by_numeric_block(
    *,
    data: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
    preserve_sparse_full_header_mapping: bool = False,
) -> dict[int, int]:
    if not data or not fields:
        return mapped_indexes
    max_columns = max((len(row) for row in data), default=0)
    if max_columns <= 0:
        return mapped_indexes

    quantity_dest_indexes = [
        idx for idx, field in enumerate(fields) if _is_quantity_field_name(field)
    ]
    if not quantity_dest_indexes:
        return mapped_indexes

    numeric_hits: dict[int, int] = {idx: 0 for idx in range(max_columns)}
    non_empty_hits: dict[int, int] = {idx: 0 for idx in range(max_columns)}
    for row in data:
        for idx in range(max_columns):
            if idx >= len(row):
                continue
            value = str(row[idx] or "").strip()
            if not value:
                continue
            non_empty_hits[idx] = int(non_empty_hits.get(idx, 0)) + 1
            if _looks_like_quantity(value):
                numeric_hits[idx] = int(numeric_hits.get(idx, 0)) + 1

    non_quantity_mapping = {
        src_idx: dest_idx
        for src_idx, dest_idx in mapped_indexes.items()
        if 0 <= dest_idx < len(fields) and not _is_quantity_field_name(fields[dest_idx])
    }
    menu_source_indexes = [
        src_idx
        for src_idx, dest_idx in non_quantity_mapping.items()
        if fields[dest_idx] in {"menu", "menu_name"}
    ]
    non_note_source_indexes = [
        src_idx
        for src_idx, dest_idx in non_quantity_mapping.items()
        if fields[dest_idx] not in {"remarks", "note"}
    ]
    note_source_indexes = [
        src_idx
        for src_idx, dest_idx in non_quantity_mapping.items()
        if fields[dest_idx] in {"remarks", "note"}
    ]
    lower_bound = 0
    if menu_source_indexes:
        lower_bound = max(menu_source_indexes) + 1
    elif non_note_source_indexes:
        lower_bound = max(non_note_source_indexes) + 1
    upper_bound = max_columns
    if note_source_indexes:
        note_src_idx = min(note_source_indexes)
        note_numeric_hits = int(numeric_hits.get(note_src_idx, 0))
        note_non_empty_hits = int(non_empty_hits.get(note_src_idx, 0))
        # Some stale yomitoku markdown shifts the quantity block one cell right
        # and temporarily places the last quantity inside the nominal remarks slot.
        if note_numeric_hits > 0 and note_numeric_hits >= note_non_empty_hits:
            upper_bound = note_src_idx + 1
        else:
            upper_bound = note_src_idx
    if upper_bound <= lower_bound:
        return mapped_indexes

    candidate_source_indexes = [
        idx
        for idx in range(lower_bound, upper_bound)
        if int(numeric_hits.get(idx, 0)) > 0
    ]
    if not candidate_source_indexes:
        return mapped_indexes

    if len(candidate_source_indexes) > len(quantity_dest_indexes):
        best_window: list[int] | None = None
        best_score = -1
        window_size = len(quantity_dest_indexes)
        for start_idx in range(len(candidate_source_indexes) - window_size + 1):
            window = candidate_source_indexes[start_idx : start_idx + window_size]
            score = sum(
                (int(numeric_hits.get(src_idx, 0)) * 10) + int(non_empty_hits.get(src_idx, 0))
                for src_idx in window
            )
            if score > best_score:
                best_score = score
                best_window = window
        if best_window:
            candidate_source_indexes = best_window

    current_quantity_mapping = {
        src_idx: dest_idx
        for src_idx, dest_idx in mapped_indexes.items()
        if dest_idx in quantity_dest_indexes
    }
    if (
        preserve_sparse_full_header_mapping
        and len(current_quantity_mapping) >= len(quantity_dest_indexes)
    ):
        return mapped_indexes
    if (
        preserve_sparse_full_header_mapping
        and len(current_quantity_mapping) >= len(quantity_dest_indexes)
        and len(candidate_source_indexes) < len(quantity_dest_indexes)
    ):
        return mapped_indexes
    current_numeric_score = sum(int(numeric_hits.get(src_idx, 0)) for src_idx in current_quantity_mapping)
    proposed_numeric_score = sum(int(numeric_hits.get(src_idx, 0)) for src_idx in candidate_source_indexes)
    current_has_empty_quantity_column = any(
        int(numeric_hits.get(src_idx, 0)) <= 0 for src_idx in current_quantity_mapping
    )
    if (
        current_quantity_mapping
        and proposed_numeric_score < current_numeric_score
        and not current_has_empty_quantity_column
    ):
        return mapped_indexes

    normalized = {
        src_idx: dest_idx
        for src_idx, dest_idx in mapped_indexes.items()
        if dest_idx not in quantity_dest_indexes
    }
    for src_idx, dest_idx in zip(candidate_source_indexes, quantity_dest_indexes):
        normalized[src_idx] = dest_idx
    return normalized


def _prefer_positional_quantity_mapping_when_width_matches(
    *,
    header: list[str],
    data: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
) -> dict[int, int]:
    widths = [len(row) for row in data if isinstance(row, list)]
    if isinstance(header, list):
        widths.append(len(header))
    widths = [width for width in widths if width > 0]
    if not widths:
        return mapped_indexes
    max_width = max(widths)
    min_width = min(widths)
    if max_width != len(fields) or min_width != len(fields):
        return mapped_indexes
    normalized = dict(mapped_indexes)
    for idx, field in enumerate(fields):
        if _is_quantity_field_name(field):
            normalized[idx] = idx
    return normalized


def _split_markdown_cells(content: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in content:
        if escaped:
            if char == "|":
                current.append("|")
            else:
                current.append("\\")
                current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def _parse_markdown_table_lines(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    if not lines:
        return [], []
    rows = []
    for line in lines:
        content = line.strip()
        if content.startswith("|"):
            content = content[1:]
        if content.endswith("|"):
            content = content[:-1]
        rows.append(_split_markdown_cells(content))
    if len(rows) >= 2:
        separator = rows[1]
        if separator and all(re.fullmatch(r"[-: ]+", cell) for cell in separator):
            return rows[0], rows[2:]
    return [], rows


def _normalize_table_matrix_rows(rows: list[list[object]]) -> list[list[str]]:
    normalized: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        normalized.append(
            [
                _coerce_row_cell(cell).replace("\r", " ").replace("\n", " ").strip()
                for cell in row
            ]
        )
    return normalized


def _is_subheader_row(row: list[str]) -> bool:
    if not row:
        return False
    tokens: list[str] = []
    known_markers = {
        "2f",
        "3f",
        "2階",
        "3階",
        "花",
        "月",
        "禁食",
        "通常",
        "袋分け",
        "肉禁",
        "魚禁",
        "肉",
        "魚",
        "変更1",
        "変更2",
    }
    for cell in row:
        token = _normalize_header_token(cell)
        if not token:
            continue
        tokens.append(token)
    if len(tokens) < 2:
        return False
    if all(token in known_markers for token in tokens):
        return True
    # Support quantity headers like 花/月 repeating in two-tier tables.
    if len(set(tokens)) <= 3 and all(len(token) <= 3 for token in tokens):
        return True
    return False


def _resolve_forbidden_subheader(group_token: str, secondary_token: str, secondary_value: str) -> str | None:
    if ("禁" in secondary_token and "肉" in secondary_token) or "nomeat" in secondary_token:
        return secondary_value
    if ("禁" in secondary_token and "魚" in secondary_token) or "nofish" in secondary_token:
        return secondary_value
    if "禁食" not in group_token and "forbidden" not in group_token:
        return None
    if "肉" in secondary_token and "魚" not in secondary_token:
        return "肉禁"
    if "魚" in secondary_token and "肉" not in secondary_token:
        return "魚禁"
    return None


def _merge_header_rows(primary: list[str], secondary: list[str]) -> list[str]:
    combined: list[str] = []
    current_group = ""
    standalone_secondary_tokens = {
        "肉禁",
        "魚禁",
        "change1",
        "change2",
        "変更1",
        "変更2",
    }
    max_len = max(len(primary), len(secondary))
    for idx in range(max_len):
        h1 = primary[idx].strip() if idx < len(primary) else ""
        h2 = secondary[idx].strip() if idx < len(secondary) else ""
        if h1:
            current_group = h1
        if h2:
            secondary_token = _normalize_header_token(h2)
            current_group_token = _normalize_header_token(current_group)
            forbidden_header = _resolve_forbidden_subheader(current_group_token, secondary_token, h2)
            if forbidden_header is not None:
                combined.append(forbidden_header)
                continue
            if secondary_token in standalone_secondary_tokens:
                combined.append(h2)
                continue
            if current_group_token and secondary_token == current_group_token:
                combined.append(h2)
                continue
            group = current_group if current_group else h1
            combined.append(f"{group} {h2}".strip() if group else h2)
        else:
            combined.append(h1)
    return combined


def _merge_header_group(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    merged = list(rows[0])
    for row in rows[1:]:
        merged = _merge_header_rows(merged, row)
    return merged


def _date_field_index(fields: list[str]) -> int | None:
    return next(
        (
            idx
            for idx, field in enumerate(fields)
            if _normalize_header_token(field) in {"date", "datemmdd", "date_mmdd"}
        ),
        None,
    )


def _daypart_field_index(fields: list[str]) -> int | None:
    return next(
        (
            idx
            for idx, field in enumerate(fields)
            if _normalize_header_token(field) in {"daypart", "meal", "time"}
        ),
        None,
    )


def _menu_field_index(fields: list[str]) -> int | None:
    return next(
        (
            idx
            for idx, field in enumerate(fields)
            if _normalize_header_token(field) in {"menu", "menuname", "menu_name"}
        ),
        None,
    )


def _looks_like_menu_projection_value(value: str, fields: list[str]) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized_daypart = re.sub(r"[\s\u3000()（）【】「」『』/／_-]+", "", text)
    if _looks_like_date(text) or _looks_like_quantity(text):
        return False
    if normalized_daypart in {"朝", "昼", "夕", "夜"}:
        return False
    normalized = _normalize_header_token(text)
    normalized_field_tokens = {
        _normalize_header_token(field)
        for field in fields
        if str(field or "").strip()
    }
    if normalized in normalized_field_tokens:
        return False
    if normalized in {
        "menu",
        "メニュー",
        "献立",
        "date",
        "日付",
        "daypart",
        "区分",
        "時間帯",
        "remarks",
        "備考",
        "note",
    }:
        return False
    if _contains_japanese_text(text):
        compact = re.sub(r"[\s\u3000・,，.。()（）<>＜＞【】「」『』\\/_-]+", "", text)
        return len(compact) >= 2
    alpha_chars = re.sub(r"[^A-Za-z]", "", text)
    if alpha_chars:
        return len(alpha_chars) >= 1
    return False


def _best_menu_source_index(
    *,
    data: list[list[str]],
    fields: list[str],
    max_source_index: int | None,
) -> int | None:
    observed_width = max((len(row) for row in data if isinstance(row, list)), default=0)
    if observed_width <= 0:
        return None
    upper_bound = observed_width if max_source_index is None else max(0, min(observed_width, max_source_index))
    best_index: int | None = None
    best_key: tuple[float, ...] | None = None
    for source_idx in range(upper_bound):
        values = [
            str(row[source_idx] or "").strip()
            for row in data
            if isinstance(row, list) and source_idx < len(row) and str(row[source_idx] or "").strip()
        ]
        if len(values) < 2:
            continue
        menu_like_count = sum(1 for value in values if _looks_like_menu_projection_value(value, fields))
        if menu_like_count <= 0:
            continue
        quantity_like_count = sum(1 for value in values if _looks_like_quantity(value))
        date_like_count = sum(1 for value in values if _looks_like_date(value))
        daypart_like_count = sum(1 for value in values if _looks_like_daypart(value))
        current_key = (
            float(menu_like_count) / float(len(values)),
            float(menu_like_count),
            -float(quantity_like_count),
            -float(date_like_count),
            -float(daypart_like_count),
            float(source_idx),
        )
        if best_key is not None and current_key <= best_key:
            continue
        best_key = current_key
        best_index = source_idx
    return best_index


def _best_date_source_index(
    *,
    data: list[list[str]],
) -> int | None:
    observed_width = max((len(row) for row in data if isinstance(row, list)), default=0)
    if observed_width <= 0:
        return None
    best_index: int | None = None
    best_key: tuple[float, ...] | None = None
    for source_idx in range(observed_width):
        values = [
            str(row[source_idx] or "").strip()
            for row in data
            if isinstance(row, list) and source_idx < len(row) and str(row[source_idx] or "").strip()
        ]
        if not values:
            continue
        date_like_count = sum(1 for value in values if _looks_like_date(value))
        if date_like_count <= 0:
            continue
        quantity_like_count = sum(1 for value in values if _looks_like_quantity(value))
        current_key = (
            float(date_like_count) / float(len(values)),
            float(date_like_count),
            -float(quantity_like_count),
            -float(source_idx),
        )
        if best_key is not None and current_key <= best_key:
            continue
        best_key = current_key
        best_index = source_idx
    return best_index


def _realign_structural_mapping_by_observed_content(
    *,
    data: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
) -> dict[int, int]:
    normalized = dict(mapped_indexes)

    menu_dest_idx = _menu_field_index(fields)
    if menu_dest_idx is not None:
        best_menu_source = _best_menu_source_index(
            data=data,
            fields=fields,
            max_source_index=None,
        )
        if best_menu_source is not None:
            normalized = {
                src_idx: dest_idx
                for src_idx, dest_idx in normalized.items()
                if dest_idx != menu_dest_idx
            }
            normalized[int(best_menu_source)] = int(menu_dest_idx)

    date_dest_idx = _date_field_index(fields)
    if date_dest_idx is not None:
        best_date_source = _best_date_source_index(data=data)
        if best_date_source is not None:
            normalized = {
                src_idx: dest_idx
                for src_idx, dest_idx in normalized.items()
                if dest_idx != date_dest_idx
            }
            normalized[int(best_date_source)] = int(date_dest_idx)
    return normalized


def _evaluate_structured_projection_rows(
    *,
    rows: list[list[str]] | None,
    fields: list[str],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "row_count": 0,
        "menu_non_empty_count": 0,
        "menu_like_count": 0,
        "menu_noise_count": 0,
        "date_non_empty_count": 0,
        "date_valid_count": 0,
        "daypart_non_empty_count": 0,
        "daypart_valid_count": 0,
        "quantity_non_empty_count": 0,
        "quantity_numeric_count": 0,
        "quantity_non_numeric_count": 0,
        "leading_quantity_non_empty_count": 0,
        "leading_quantity_numeric_count": 0,
        "menu_like_ratio": 0.0,
        "date_valid_ratio": 0.0,
        "daypart_valid_ratio": 0.0,
        "quantity_numeric_ratio": 0.0,
        "projection_corrupted": False,
    }
    if not isinstance(rows, list) or not rows or not fields:
        return metrics

    date_idx = _date_field_index(fields)
    daypart_idx = _daypart_field_index(fields)
    menu_idx = _menu_field_index(fields)
    quantity_indexes = [
        idx for idx, field in enumerate(fields) if _is_quantity_field_name(field)
    ]
    leading_quantity_idx = min(quantity_indexes) if quantity_indexes else None

    metrics["row_count"] = len(rows)
    for row in rows:
        if not isinstance(row, list):
            continue
        if date_idx is not None and date_idx < len(row):
            value = str(row[date_idx] or "").strip()
            if value:
                metrics["date_non_empty_count"] += 1
                if _looks_like_date(value):
                    metrics["date_valid_count"] += 1
        if daypart_idx is not None and daypart_idx < len(row):
            value = str(row[daypart_idx] or "").strip()
            if value:
                metrics["daypart_non_empty_count"] += 1
                if _looks_like_daypart(value):
                    metrics["daypart_valid_count"] += 1
        if menu_idx is not None and menu_idx < len(row):
            value = str(row[menu_idx] or "").strip()
            if value:
                metrics["menu_non_empty_count"] += 1
                if _looks_like_menu_projection_value(value, fields):
                    metrics["menu_like_count"] += 1
                else:
                    metrics["menu_noise_count"] += 1
        for idx in quantity_indexes:
            if idx >= len(row):
                continue
            value = str(row[idx] or "").strip()
            if not value:
                continue
            metrics["quantity_non_empty_count"] += 1
            if leading_quantity_idx is not None and idx == leading_quantity_idx:
                metrics["leading_quantity_non_empty_count"] += 1
            if _looks_like_quantity(value):
                metrics["quantity_numeric_count"] += 1
                if leading_quantity_idx is not None and idx == leading_quantity_idx:
                    metrics["leading_quantity_numeric_count"] += 1
            else:
                metrics["quantity_non_numeric_count"] += 1

    menu_non_empty = int(metrics["menu_non_empty_count"] or 0)
    date_non_empty = int(metrics["date_non_empty_count"] or 0)
    daypart_non_empty = int(metrics["daypart_non_empty_count"] or 0)
    quantity_non_empty = int(metrics["quantity_non_empty_count"] or 0)
    metrics["menu_like_ratio"] = (
        float(metrics["menu_like_count"]) / float(menu_non_empty)
        if menu_non_empty > 0
        else 0.0
    )
    metrics["date_valid_ratio"] = (
        float(metrics["date_valid_count"]) / float(date_non_empty)
        if date_non_empty > 0
        else 0.0
    )
    metrics["daypart_valid_ratio"] = (
        float(metrics["daypart_valid_count"]) / float(daypart_non_empty)
        if daypart_non_empty > 0
        else 0.0
    )
    metrics["quantity_numeric_ratio"] = (
        float(metrics["quantity_numeric_count"]) / float(quantity_non_empty)
        if quantity_non_empty > 0
        else 0.0
    )
    corrupted = False
    if menu_non_empty >= 4 and float(metrics["menu_like_ratio"]) < 0.6:
        corrupted = True
    if date_non_empty >= 2 and float(metrics["date_valid_ratio"]) < 0.5:
        corrupted = True
    if quantity_non_empty >= 4 and (
        float(metrics["quantity_numeric_ratio"]) < 0.65
        or int(metrics["quantity_non_numeric_count"]) >= 4
    ):
        corrupted = True
    metrics["projection_corrupted"] = corrupted
    return metrics


def _normalize_projection_identity_token(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def classify_structured_projection_rows(
    *,
    rows: list[list[str]] | None,
    fields: list[str],
) -> dict[str, Any]:
    metrics = _evaluate_structured_projection_rows(rows=rows, fields=fields)
    classification: dict[str, Any] = {
        "accepted": False,
        "row_kinds": [],
        "menu_row_count": 0,
        "quantity_only_row_count": 0,
        "structural_row_count": 0,
        "blank_row_count": 0,
        "duplicate_identity_row_count": 0,
        "classification_corrupted": False,
        "projection_metrics": metrics,
    }
    if not isinstance(rows, list) or not rows or not fields:
        return classification

    date_idx = _date_field_index(fields)
    daypart_idx = _daypart_field_index(fields)
    menu_idx = _menu_field_index(fields)
    quantity_indexes = [
        idx for idx, field in enumerate(fields) if _is_quantity_field_name(field)
    ]

    prev_menu_identity: tuple[str, str, str] | None = None
    row_kinds: list[str] = []
    for row in rows:
        normalized_row = [str(cell or "").strip() for cell in (row or [])]
        date_value = normalized_row[date_idx] if date_idx is not None and date_idx < len(normalized_row) else ""
        daypart_value = normalized_row[daypart_idx] if daypart_idx is not None and daypart_idx < len(normalized_row) else ""
        menu_value = normalized_row[menu_idx] if menu_idx is not None and menu_idx < len(normalized_row) else ""
        quantity_values = [
            normalized_row[idx]
            for idx in quantity_indexes
            if idx < len(normalized_row) and str(normalized_row[idx] or "").strip()
        ]
        non_empty_values = [value for value in normalized_row if value]
        if not non_empty_values:
            kind = "blank_row"
            prev_menu_identity = None
        elif menu_value and _looks_like_menu_projection_value(menu_value, fields):
            kind = "menu_row"
            menu_identity = (
                _normalize_projection_identity_token(date_value),
                _normalize_projection_identity_token(daypart_value),
                _normalize_projection_identity_token(menu_value),
            )
            if prev_menu_identity is not None and menu_identity == prev_menu_identity:
                classification["duplicate_identity_row_count"] = int(
                    classification.get("duplicate_identity_row_count") or 0
                ) + 1
            prev_menu_identity = menu_identity
        elif not menu_value and quantity_values:
            kind = "quantity_only_row"
        else:
            kind = "structural_row"
            prev_menu_identity = None
        row_kinds.append(kind)
        counter_key = {
            "menu_row": "menu_row_count",
            "quantity_only_row": "quantity_only_row_count",
            "structural_row": "structural_row_count",
            "blank_row": "blank_row_count",
        }[kind]
        classification[counter_key] = int(classification.get(counter_key) or 0) + 1

    classification["row_kinds"] = row_kinds
    menu_row_count = int(classification.get("menu_row_count") or 0)
    row_count = len(row_kinds)
    duplicate_identity_row_count = int(classification.get("duplicate_identity_row_count") or 0)
    structural_row_count = int(classification.get("structural_row_count") or 0)
    blank_row_count = int(classification.get("blank_row_count") or 0)
    quantity_only_row_count = int(classification.get("quantity_only_row_count") or 0)

    classification_corrupted = False
    if menu_row_count <= 0:
        classification_corrupted = True
    if duplicate_identity_row_count > max(0, menu_row_count // 6):
        classification_corrupted = True
    if structural_row_count >= max(3, row_count // 4):
        classification_corrupted = True
    if blank_row_count >= max(6, row_count // 3):
        classification_corrupted = True
    if quantity_only_row_count > max(menu_row_count * 2, 12):
        classification_corrupted = True
    if bool(metrics.get("projection_corrupted")):
        classification_corrupted = True

    classification["classification_corrupted"] = classification_corrupted
    classification["accepted"] = not classification_corrupted
    return classification


def _projection_sort_key(
    *,
    metrics: dict[str, Any],
    prefer_explicit: bool,
) -> tuple[float, ...]:
    return (
        1.0 if not metrics.get("projection_corrupted") else 0.0,
        float(metrics.get("menu_like_ratio") or 0.0),
        float(metrics.get("date_valid_ratio") or 0.0),
        float(metrics.get("leading_quantity_numeric_count") or 0.0),
        float(metrics.get("leading_quantity_non_empty_count") or 0.0),
        float(metrics.get("quantity_numeric_ratio") or 0.0),
        float(metrics.get("daypart_valid_ratio") or 0.0),
        -float(metrics.get("menu_noise_count") or 0.0),
        -float(metrics.get("quantity_non_numeric_count") or 0.0),
        float(metrics.get("menu_like_count") or 0.0),
        float(metrics.get("quantity_numeric_count") or 0.0),
        1.0 if prefer_explicit else 0.0,
    )


def _materialize_projected_rows(
    *,
    data: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
    header_height: int = 0,
) -> tuple[dict[int, int], list[list[str]]]:
    output_rows: list[list[str]] = []
    row_map: dict[int, int] = {}
    for raw_row_index, row in enumerate(data, start=header_height):
        output_row = [""] * len(fields)
        for src_idx, dest_idx in mapped_indexes.items():
            if src_idx < len(row):
                output_row[dest_idx] = row[src_idx]
        if any(cell.strip() for cell in output_row):
            row_map[raw_row_index] = len(output_rows)
            output_rows.append([_coerce_row_cell(cell) for cell in output_row])
    return row_map, output_rows


def _finalize_projected_mapped_indexes(
    *,
    data: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
    template: dict,
) -> dict[int, int]:
    explicit_quantity_column_count = _template_explicit_quantity_column_count(template)
    current_mapped_quantity_count = sum(
        1
        for dest_idx in mapped_indexes.values()
        if 0 <= dest_idx < len(fields) and _is_quantity_field_name(fields[dest_idx])
    )
    finalized = _realign_quantity_mapping_by_numeric_block(
        data=data,
        fields=fields,
        mapped_indexes=dict(mapped_indexes),
        preserve_sparse_full_header_mapping=(
            explicit_quantity_column_count > 0
            and current_mapped_quantity_count >= explicit_quantity_column_count
        ),
    )
    finalized = _fill_remaining_quantity_mapping_by_order(
        data=data,
        fields=fields,
        mapped_indexes=finalized,
    )
    return finalized


def _project_rows_from_header_and_data_internal(
    *,
    header: list[str],
    data: list[list[str]],
    template: dict,
    allow_explicit_template_columns: bool,
    header_height: int = 0,
) -> tuple[dict[str, Any], list[list[str]]] | None:
    fields = _get_row_fields(template)
    if not fields:
        return None
    header = [str(cell or "").strip() for cell in header]
    data = [
        [str(cell or "").strip() for cell in row]
        for row in data
        if isinstance(row, list)
    ]
    fields_set = set(fields)
    header_score = _count_mapped_header_cells(header, fields_set) if header else 0
    if data and header_score < 2:
        for idx, candidate in enumerate(data[:3]):
            if _count_mapped_header_cells(candidate, fields_set) >= 2:
                header = candidate
                data = data[idx + 1 :]
                header_height += idx + 1
                break
    while header and data and _is_subheader_row(data[0]):
        header = _merge_header_rows(header, data[0])
        data = data[1:]
        header_height += 1
    mapped_indexes: dict[int, int] = {}
    used_dest_indexes: set[int] = set()
    if header:
        for idx, cell in enumerate(header):
            field = _field_from_header(cell, fields_set)
            if field:
                dest_idx = fields.index(field)
                if dest_idx in used_dest_indexes:
                    continue
                mapped_indexes[idx] = dest_idx
                used_dest_indexes.add(dest_idx)
    if not mapped_indexes:
        if header and len(header) == len(fields):
            mapped_indexes = {idx: idx for idx in range(len(header))}
        elif data and len(data[0]) == len(fields):
            mapped_indexes = {idx: idx for idx in range(len(fields))}
    mapped_indexes = _infer_mapped_indexes(
        data=data,
        fields=fields,
        mapped_indexes=mapped_indexes,
    )
    mapped_indexes = _prefer_positional_quantity_mapping_when_width_matches(
        header=header,
        data=data,
        fields=fields,
        mapped_indexes=mapped_indexes,
    )
    mapped_indexes = _realign_structural_mapping_by_observed_content(
        data=data,
        fields=fields,
        mapped_indexes=mapped_indexes,
    )
    observed_width = max(
        [len(header)] + [len(row) for row in data if isinstance(row, list)],
        default=0,
    )
    candidate_specs: list[tuple[str, dict[int, int], bool]] = [
        ("observed", dict(mapped_indexes), False),
    ]
    explicit_mapped_indexes = _mapped_indexes_from_template_columns(
        template=template,
        fields=fields,
        observed_width=observed_width,
    )
    if allow_explicit_template_columns and explicit_mapped_indexes:
        candidate_specs.append(
            (
                "template_explicit",
                _overlay_explicit_mapped_indexes(dict(mapped_indexes), explicit_mapped_indexes),
                True,
            )
        )

    best_meta: dict[str, Any] | None = None
    best_rows: list[list[str]] | None = None
    best_key: tuple[float, ...] | None = None
    for projection_variant, seed_indexes, used_explicit in candidate_specs:
        finalized_indexes = _finalize_projected_mapped_indexes(
            data=data,
            fields=fields,
            mapped_indexes=seed_indexes,
            template=template,
        )
        if not finalized_indexes:
            continue
        row_map, output_rows = _materialize_projected_rows(
            data=data,
            fields=fields,
            mapped_indexes=finalized_indexes,
            header_height=header_height,
        )
        if not output_rows:
            continue
        metrics = _evaluate_structured_projection_rows(rows=output_rows, fields=fields)
        row_classification = classify_structured_projection_rows(
            rows=output_rows,
            fields=fields,
        )
        if not bool(row_classification.get("accepted")):
            continue
        current_key = _projection_sort_key(
            metrics=metrics,
            prefer_explicit=used_explicit,
        )
        if best_key is not None and current_key <= best_key:
            continue
        best_key = current_key
        best_rows = output_rows
        best_meta = {
            "fields": fields,
            "mapped_indexes": finalized_indexes,
            "row_map": row_map,
            "projection_variant": projection_variant,
            "projection_used_explicit_template_columns": used_explicit,
            "projection_metrics": metrics,
            "projection_row_classification": row_classification,
        }

    if best_meta is None or best_rows is None:
        return None
    return best_meta, best_rows


def _looks_like_data_row(row: list[str], fields: set[str]) -> bool:
    values = [str(cell or "").strip() for cell in row if str(cell or "").strip()]
    if not values:
        return False
    header_hits = _count_mapped_header_cells(values, fields)
    date_like = any(_looks_like_date(value) for value in values)
    daypart_like = any(_looks_like_daypart(value) for value in values)
    quantity_like = sum(1 for value in values if _looks_like_quantity(value))
    menu_like = any(
        (
            _contains_japanese_text(value)
            or bool(re.search(r"[A-Za-z]", value))
        )
        and not _looks_like_quantity(value)
        and not _looks_like_date(value)
        and _field_from_header(value, fields) is None
        for value in values
    )
    if date_like and (menu_like or quantity_like > 0 or daypart_like):
        return True
    if daypart_like and (menu_like or quantity_like > 0) and header_hits < 2:
        return True
    if quantity_like >= 2 and menu_like and header_hits < 2:
        return True
    return False


def _extract_markdown_tables(markdown: str) -> list[tuple[list[str], list[list[str]]]]:
    tables: list[list[str]] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if line.strip().startswith("|"):
            current.append(line)
        else:
            if current:
                tables.append(current)
                current = []
    if current:
        tables.append(current)
    parsed: list[tuple[list[str], list[list[str]]]] = []
    for table_lines in tables:
        header, data = _parse_markdown_table_lines(table_lines)
        if data:
            parsed.append((header, data))
    return parsed


def _rows_from_header_and_data(
    header: list[str],
    data: list[list[str]],
    template: dict,
    *,
    allow_explicit_template_columns: bool = False,
) -> list[list[str]] | None:
    projected = _project_rows_from_header_and_data_internal(
        header=header,
        data=data,
        template=template,
        allow_explicit_template_columns=allow_explicit_template_columns,
    )
    if not isinstance(projected, tuple) or len(projected) != 2:
        return None
    _meta, rows = projected
    return rows or None


def _rows_from_table_matrix(table_rows: list[list[object]], template: dict) -> list[list[str]] | None:
    fields = _get_row_fields(template)
    if not fields:
        return None
    normalized_rows = _normalize_table_matrix_rows(table_rows)
    if not normalized_rows:
        return None
    fields_set = set(fields)
    header_height: int | None = None
    for idx, row in enumerate(normalized_rows[:6]):
        if _looks_like_data_row(row, fields_set):
            header_height = idx
            break
    if header_height is None:
        header_height = 1 if len(normalized_rows) > 1 else 0
    header_rows = normalized_rows[:header_height]
    data = normalized_rows[header_height:]
    if not data and normalized_rows:
        header_rows = normalized_rows[:1]
        data = normalized_rows[1:]
    header = _merge_header_group(header_rows)
    return _rows_from_header_and_data(
        header,
        data,
        template,
        allow_explicit_template_columns=True,
    )


def _rows_from_markdown_blocks(markdown: str, template: dict) -> list[list[str]] | None:
    fields = _get_row_fields(template)
    if not fields:
        return None
    tables = _extract_markdown_tables(markdown)
    if not tables:
        return None
    normalized: list[list[str]] = []
    for header, data in tables:
        table_rows = _rows_from_header_and_data(header, data, template)
        if table_rows:
            normalized.extend(table_rows)
    return normalized or None


def _matrix_from_structured_cells(
    cells: list[dict],
    *,
    row_count_hint: int | None,
    col_count_hint: int | None,
) -> list[list[str]] | None:
    row_values: list[int] = []
    col_values: list[int] = []
    normalized_cells: list[tuple[int, int, str]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        row_raw = cell.get("row_index")
        col_raw = cell.get("col_index")
        if row_raw is None:
            row_raw = cell.get("row")
        if col_raw is None:
            col_raw = cell.get("col")
        try:
            row_idx = int(row_raw)
            col_idx = int(col_raw)
        except Exception:
            continue
        row_values.append(row_idx)
        col_values.append(col_idx)
        text = cell.get("text")
        if text is None:
            text = cell.get("contents")
        normalized_cells.append((row_idx, col_idx, _coerce_row_cell(text).replace("\r", " ").replace("\n", " ").strip()))
    if not normalized_cells:
        return None
    row_base = min(row_values) if row_values else 0
    col_base = min(col_values) if col_values else 0
    row_count = int(row_count_hint) if isinstance(row_count_hint, int) else (max(row_values) - row_base + 1)
    col_count = int(col_count_hint) if isinstance(col_count_hint, int) else (max(col_values) - col_base + 1)
    if row_count <= 0 or col_count <= 0:
        return None
    matrix = [["" for _ in range(col_count)] for _ in range(row_count)]
    for row_raw, col_raw, text in normalized_cells:
        row_idx = row_raw - row_base
        col_idx = col_raw - col_base
        if row_idx < 0 or col_idx < 0:
            continue
        if row_idx >= row_count or col_idx >= col_count:
            continue
        if text and not matrix[row_idx][col_idx]:
            matrix[row_idx][col_idx] = text
    return matrix


def _collect_structured_tables(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    seen_table_ids: set[str] = set()
    structured_tables: list[dict[str, Any]] = []

    def _collect(table_payload: object) -> None:
        if not isinstance(table_payload, dict):
            return
        table_id = str(table_payload.get("table_id") or "").strip()
        if table_id and table_id in seen_table_ids:
            return
        if table_id:
            seen_table_ids.add(table_id)
        structured_tables.append(table_payload)

    top_tables = payload.get("tables")
    if isinstance(top_tables, list):
        for table_payload in top_tables:
            _collect(table_payload)
    pages = payload.get("pages")
    if isinstance(pages, list):
        for page_payload in pages:
            if not isinstance(page_payload, dict):
                continue
            page_tables = page_payload.get("tables")
            if not isinstance(page_tables, list):
                continue
            for table_payload in page_tables:
                _collect(table_payload)
    return structured_tables


def _resolve_structured_table_mapping(
    table_rows: list[list[object]],
    template: dict,
) -> tuple[dict[str, Any], list[list[str]]] | None:
    fields = _get_row_fields(template)
    if not fields:
        return None
    normalized_rows = _normalize_table_matrix_rows(table_rows)
    if not normalized_rows:
        return None
    fields_set = set(fields)
    header_height: int | None = None
    for idx, row in enumerate(normalized_rows[:6]):
        if _looks_like_data_row(row, fields_set):
            header_height = idx
            break
    if header_height is None:
        header_height = 1 if len(normalized_rows) > 1 else 0
    header_rows = normalized_rows[:header_height]
    data = normalized_rows[header_height:]
    if not data and normalized_rows:
        header_rows = normalized_rows[:1]
        data = normalized_rows[1:]
        header_height = min(header_height, 1)
    header = _merge_header_group(header_rows)
    projected = _project_rows_from_header_and_data_internal(
        header=header,
        data=data,
        template=template,
        allow_explicit_template_columns=True,
        header_height=header_height,
    )
    if not isinstance(projected, tuple) or len(projected) != 2:
        return None
    return projected


def rows_from_structured_payload(
    payload: object,
    template: dict,
    *,
    allow_payload_template_override: bool = True,
) -> list[list[str]] | None:
    template = _resolve_payload_template(
        payload,
        template,
        allow_payload_template_override=allow_payload_template_override,
    )
    structured_tables = _collect_structured_tables(payload)
    if not structured_tables:
        return None

    normalized: list[list[str]] = []
    for table_payload in structured_tables:
        matrix: list[list[str]] | None = None
        raw_rows = table_payload.get("rows")
        if isinstance(raw_rows, list) and raw_rows:
            matrix = _normalize_table_matrix_rows(raw_rows)
        if matrix is None:
            raw_cells = table_payload.get("cells")
            if isinstance(raw_cells, list) and raw_cells:
                matrix = _matrix_from_structured_cells(
                    raw_cells,
                    row_count_hint=table_payload.get("row_count")
                    if isinstance(table_payload.get("row_count"), int)
                    else table_payload.get("n_row"),
                    col_count_hint=table_payload.get("col_count")
                    if isinstance(table_payload.get("col_count"), int)
                    else table_payload.get("n_col"),
                )
        if not matrix:
            continue
        rows = _rows_from_table_matrix(matrix, template)
        if rows:
            normalized.extend(rows)
    return normalized or None


def _structured_issue_value_contains_digits(value: str) -> bool:
    return bool(re.search(r"[0-9０-９]", str(value or "")))


def _structured_issue_is_multiline_numeric(value: str) -> bool:
    text = str(value or "")
    if "\n" not in text:
        return False
    lines = [re.sub(r"\s+", "", line) for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    digit_lines = [line for line in lines if re.fullmatch(r"[0-9０-９]+", line)]
    return len(digit_lines) >= 2


def structured_cell_issues_from_payload(
    payload: object,
    template: dict,
    *,
    allow_payload_template_override: bool = True,
) -> list[dict[str, Any]]:
    template = _resolve_payload_template(
        payload,
        template,
        allow_payload_template_override=allow_payload_template_override,
    )
    structured_tables = _collect_structured_tables(payload)
    if not structured_tables:
        return []

    issues: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    row_offset = 0

    def _append_issue(
        *,
        source_row_index: int,
        field: str,
        issue_code: str,
        table_payload: dict[str, Any],
        cell_payload: dict[str, Any] | None,
        value: str,
    ) -> None:
        dedupe_key = (source_row_index, field, issue_code)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        issue: dict[str, Any] = {
            "row_index": source_row_index,
            "field": field,
            "issue_code": issue_code,
            "severity": "warning",
            "source": "yomitoku_structured",
            "table_id": str(table_payload.get("table_id") or "").strip() or None,
            "page_index": table_payload.get("page_index"),
            "value": value,
        }
        if isinstance(cell_payload, dict):
            if cell_payload.get("bbox") is not None:
                issue["bbox"] = cell_payload.get("bbox")
            for key in ("row_span", "col_span"):
                try:
                    issue[key] = max(1, int(cell_payload.get(key) or 1))
                except Exception:
                    continue
        issues.append(issue)

    for table_payload in structured_tables:
        matrix: list[list[object]] | None = None
        raw_rows = table_payload.get("rows")
        use_cell_offsets = False
        if isinstance(raw_rows, list) and raw_rows:
            matrix = raw_rows
        else:
            raw_cells = table_payload.get("cells")
            if isinstance(raw_cells, list) and raw_cells:
                matrix = _matrix_from_structured_cells(
                    raw_cells,
                    row_count_hint=table_payload.get("row_count")
                    if isinstance(table_payload.get("row_count"), int)
                    else table_payload.get("n_row"),
                    col_count_hint=table_payload.get("col_count")
                    if isinstance(table_payload.get("col_count"), int)
                    else table_payload.get("n_col"),
                )
                use_cell_offsets = True
        if not matrix:
            continue

        resolved = _resolve_structured_table_mapping(matrix, template)
        if resolved is None:
            continue
        mapping, output_rows = resolved
        fields = mapping["fields"]
        mapped_indexes = mapping["mapped_indexes"]
        row_map = mapping["row_map"]

        raw_cells = table_payload.get("cells")
        if isinstance(raw_cells, list) and raw_cells:
            row_base = 0
            col_base = 0
            if use_cell_offsets:
                row_values: list[int] = []
                col_values: list[int] = []
                for cell in raw_cells:
                    if not isinstance(cell, dict):
                        continue
                    try:
                        row_values.append(int(cell.get("row_index") if cell.get("row_index") is not None else cell.get("row")))
                        col_values.append(int(cell.get("col_index") if cell.get("col_index") is not None else cell.get("col")))
                    except Exception:
                        continue
                row_base = min(row_values) if row_values else 0
                col_base = min(col_values) if col_values else 0
            for cell in raw_cells:
                if not isinstance(cell, dict):
                    continue
                try:
                    raw_row_index = int(
                        cell.get("row_index") if cell.get("row_index") is not None else cell.get("row")
                    ) - row_base
                    raw_col_index = int(
                        cell.get("col_index") if cell.get("col_index") is not None else cell.get("col")
                    ) - col_base
                except Exception:
                    continue
                relative_row_index = row_map.get(raw_row_index)
                if relative_row_index is None:
                    continue
                dest_idx = mapped_indexes.get(raw_col_index)
                if dest_idx is None or dest_idx >= len(fields):
                    continue
                field = fields[dest_idx]
                if not _is_quantity_field_name(field):
                    continue
                text = _coerce_row_cell(cell.get("text") if cell.get("text") is not None else cell.get("contents"))
                if _structured_issue_is_multiline_numeric(text):
                    _append_issue(
                        source_row_index=row_offset + relative_row_index,
                        field=field,
                        issue_code="multiline_numeric_cell",
                        table_payload=table_payload,
                        cell_payload=cell,
                        value=text,
                    )
                if _structured_issue_value_contains_digits(text):
                    try:
                        row_span = max(1, int(cell.get("row_span") or 1))
                    except Exception:
                        row_span = 1
                    try:
                        col_span = max(1, int(cell.get("col_span") or 1))
                    except Exception:
                        col_span = 1
                    if row_span > 1 or col_span > 1:
                        _append_issue(
                            source_row_index=row_offset + relative_row_index,
                            field=field,
                            issue_code="merged_numeric_cell",
                            table_payload=table_payload,
                            cell_payload=cell,
                            value=text,
                        )
        else:
            normalized_rows = _normalize_table_matrix_rows(matrix)
            for raw_row_index, relative_row_index in row_map.items():
                if raw_row_index >= len(normalized_rows):
                    continue
                row = normalized_rows[raw_row_index]
                for src_idx, dest_idx in mapped_indexes.items():
                    if src_idx >= len(row) or dest_idx >= len(fields):
                        continue
                    field = fields[dest_idx]
                    if not _is_quantity_field_name(field):
                        continue
                    text = _coerce_row_cell(row[src_idx])
                    if _structured_issue_is_multiline_numeric(text):
                        _append_issue(
                            source_row_index=row_offset + relative_row_index,
                            field=field,
                            issue_code="multiline_numeric_cell",
                            table_payload=table_payload,
                            cell_payload=None,
                            value=text,
                        )
        row_offset += len(output_rows)

    return issues


def _rows_from_markdown(markdown: str, template: dict) -> list[list[str]] | None:
    tables = _extract_markdown_tables(markdown)
    if not tables:
        return None
    header, data = max(tables, key=lambda item: len(item[1]))
    return _rows_from_header_and_data(header, data, template)


def rows_from_markdown(markdown: str, template: dict) -> list[list[str]] | None:
    return _rows_from_markdown(markdown, template)


def rows_from_pipeline_payload(
    payload: object,
    template: dict,
    *,
    allow_payload_template_override: bool = True,
) -> list[list[str]] | None:
    return _rows_from_pipeline_payload(
        payload,
        template,
        allow_payload_template_override=allow_payload_template_override,
    )


def _extract_facility_and_dates_tesseract(image, template: dict) -> tuple[str | None, list[str]]:
    facility_name = None
    date_strings: List[str] = []
    facility_box = template.get("facility_name_box")
    if facility_box:
        crop = _crop_to_bbox(image, facility_box)
        if crop:
            text = _ocr_crop_text(crop)
            if text:
                facility_name = text
    for date_box in template.get("date_boxes", []) or []:
        bbox = date_box.get("bbox")
        if not bbox:
            continue
        crop = _crop_to_bbox(image, bbox)
        if not crop:
            continue
        text = _ocr_crop_text(crop)
        if text:
            date_strings.append(text)
    return facility_name, date_strings


def filter_tokens_by_box(tokens: List[dict], table_box: Optional[list[float]]) -> List[dict]:
    if not table_box:
        return tokens
    x0, y0, x1, y1 = table_box
    return [
        token
        for token in tokens
        if token.get("x") is not None
        and token.get("y") is not None
        and x0 <= token["x"] <= x1
        and y0 <= token["y"] <= y1
    ]


def extract_fax_data(
    pdf_bytes: bytes,
    template: dict,
    *,
    facility_id: str | None = None,
    preferred_template_id: str | None = None,
) -> FaxExtractedData:
    provider = _get_main_provider(template)
    grid = detect_table_grid(pdf_bytes, template)
    page_index = max(int(template.get("page", 1)) - 1, 0)
    logger.info("Main OCR provider selected", provider=provider)

    if provider == "tesseract":
        import pdfplumber

        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[page_index] if pdf.pages else None
            if not page:
                return FaxExtractedData(None, [], [], [], grid=grid, ocr_provider=provider)
            resolution = _get_resolution(template, "main_ocr_resolution", 320)
            image = page.to_image(resolution=resolution).original
        logger.info(
            "Main OCR provider: tesseract",
            resolution=resolution,
            use_table_box=bool(template.get("main_ocr_use_table_box", True)),
        )
        facility_name, date_strings = _extract_facility_and_dates_tesseract(image, template)
        use_table_box = bool(template.get("main_ocr_use_table_box", True))
        table_box = template.get("table_box") if use_table_box else None
        token_image = image
        if table_box:
            crop = _crop_to_bbox(image, table_box)
            if crop:
                token_image = crop
        tokens = _extract_tesseract_tokens(token_image)
        if table_box:
            tokens = _map_tokens_to_box(tokens, table_box)
        return FaxExtractedData(
            facility_name=facility_name,
            date_strings=date_strings,
            table_rows=[],
            tokens=tokens,
            grid=grid,
            ocr_provider=provider,
        )

    if provider == "pipeline":
        output = run_ocr_pipeline(
            pdf_bytes=pdf_bytes,
            job_id=f"MAIN-{uuid.uuid4().hex[:8]}",
            facility_id=facility_id,
            input_reference=None,
            preferred_template_id=preferred_template_id,
        )
        rows = _rows_from_pipeline_payload(output, template) or []
        facility_name = None
        date_strings = []
        raw_text = None
        provider_debug: dict | None = {"provider": "pipeline"}
        if isinstance(output, dict):
            raw_name = output.get("facility_name")
            if isinstance(raw_name, str):
                facility_name = raw_name.strip() or None
            raw_dates = output.get("date_strings")
            if isinstance(raw_dates, list):
                date_strings = [str(item) for item in raw_dates if str(item).strip()]
            table_raw = output.get("table_raw")
            if isinstance(table_raw, str) and table_raw.strip():
                raw_text = table_raw.strip()
            template_id = output.get("template_id")
            if isinstance(template_id, str) and template_id.strip():
                provider_debug["template_id"] = template_id.strip()
        return FaxExtractedData(
            facility_name=facility_name,
            date_strings=date_strings,
            table_rows=rows,
            tokens=[],
            grid=grid,
            ocr_provider=provider,
            raw_text=raw_text,
            provider_debug=provider_debug,
        )

    if provider == "openai":
        from src.services.openai_ocr_service import run_openai_ocr

        fallback_provider = str(template.get("openai_ocr_fallback_provider") or "pipeline").lower()
        try:
            output = run_openai_ocr(
                pdf_bytes=pdf_bytes,
                template=template,
                facility_id=facility_id,
            )
            rows = _rows_from_pipeline_payload(output, template) or []
            facility_name = None
            date_strings = []
            raw_text, provider_debug = _extract_provider_debug(output)
            if isinstance(output, dict):
                raw_name = output.get("facility_name")
                if isinstance(raw_name, str):
                    facility_name = raw_name.strip() or None
                raw_dates = output.get("date_strings")
                if isinstance(raw_dates, list):
                    date_strings = [str(item) for item in raw_dates if str(item).strip()]
            if not isinstance(provider_debug, dict):
                provider_debug = {}
            provider_debug.setdefault("provider", "openai")
            return FaxExtractedData(
                facility_name=facility_name,
                date_strings=date_strings,
                table_rows=rows,
                tokens=[],
                grid=grid,
                ocr_provider=provider,
                raw_text=raw_text,
                provider_debug=provider_debug,
            )
        except Exception as exc:  # noqa: BLE001
            if fallback_provider != "pipeline":
                raise
            logger.warning("OpenAI OCR failed; fallback to pipeline: {}", str(exc))
            output = run_ocr_pipeline(
                pdf_bytes=pdf_bytes,
                job_id=f"MAIN-{uuid.uuid4().hex[:8]}",
                facility_id=facility_id,
                input_reference=None,
                preferred_template_id=preferred_template_id,
            )
            rows = _rows_from_pipeline_payload(output, template) or []
            facility_name = None
            date_strings = []
            raw_text = None
            provider_debug: dict = {
                "provider": "openai_fallback_pipeline",
                "failed_provider": "openai",
                "fallback_reason": str(exc),
            }
            if isinstance(output, dict):
                raw_name = output.get("facility_name")
                if isinstance(raw_name, str):
                    facility_name = raw_name.strip() or None
                raw_dates = output.get("date_strings")
                if isinstance(raw_dates, list):
                    date_strings = [str(item) for item in raw_dates if str(item).strip()]
                table_raw = output.get("table_raw")
                if isinstance(table_raw, str) and table_raw.strip():
                    raw_text = table_raw.strip()
                template_id = output.get("template_id")
                if isinstance(template_id, str) and template_id.strip():
                    provider_debug["fallback_template_id"] = template_id.strip()
            return FaxExtractedData(
                facility_name=facility_name,
                date_strings=date_strings,
                table_rows=rows,
                tokens=[],
                grid=grid,
                ocr_provider="openai_fallback_pipeline",
                raw_text=raw_text,
                provider_debug=provider_debug,
            )

    if provider == "gemini":
        from src.services.gemini_ocr_service import run_gemini_ocr

        fallback_provider = str(template.get("gemini_ocr_fallback_provider") or "pipeline").lower()
        try:
            output = run_gemini_ocr(
                pdf_bytes=pdf_bytes,
                template=template,
                facility_id=facility_id,
            )
            rows = _rows_from_pipeline_payload(output, template) or []
            facility_name = None
            date_strings = []
            raw_text, provider_debug = _extract_provider_debug(output)
            if isinstance(output, dict):
                raw_name = output.get("facility_name")
                if isinstance(raw_name, str):
                    facility_name = raw_name.strip() or None
                raw_dates = output.get("date_strings")
                if isinstance(raw_dates, list):
                    date_strings = [str(item) for item in raw_dates if str(item).strip()]
            if not isinstance(provider_debug, dict):
                provider_debug = {}
            provider_debug.setdefault("provider", "gemini")
            return FaxExtractedData(
                facility_name=facility_name,
                date_strings=date_strings,
                table_rows=rows,
                tokens=[],
                grid=grid,
                ocr_provider=provider,
                raw_text=raw_text,
                provider_debug=provider_debug,
            )
        except Exception as exc:  # noqa: BLE001
            if fallback_provider != "pipeline":
                raise
            logger.warning("Gemini OCR failed; fallback to pipeline: {}", str(exc))
            output = run_ocr_pipeline(
                pdf_bytes=pdf_bytes,
                job_id=f"MAIN-{uuid.uuid4().hex[:8]}",
                facility_id=facility_id,
                input_reference=None,
                preferred_template_id=preferred_template_id,
            )
            rows = _rows_from_pipeline_payload(output, template) or []
            facility_name = None
            date_strings = []
            raw_text = None
            provider_debug: dict = {
                "provider": "gemini_fallback_pipeline",
                "failed_provider": "gemini",
                "fallback_reason": str(exc),
            }
            if isinstance(output, dict):
                raw_name = output.get("facility_name")
                if isinstance(raw_name, str):
                    facility_name = raw_name.strip() or None
                raw_dates = output.get("date_strings")
                if isinstance(raw_dates, list):
                    date_strings = [str(item) for item in raw_dates if str(item).strip()]
                table_raw = output.get("table_raw")
                if isinstance(table_raw, str) and table_raw.strip():
                    raw_text = table_raw.strip()
                template_id = output.get("template_id")
                if isinstance(template_id, str) and template_id.strip():
                    provider_debug["fallback_template_id"] = template_id.strip()
            return FaxExtractedData(
                facility_name=facility_name,
                date_strings=date_strings,
                table_rows=rows,
                tokens=[],
                grid=grid,
                ocr_provider="gemini_fallback_pipeline",
                raw_text=raw_text,
                provider_debug=provider_debug,
            )

    raise RuntimeError(f"OCR provider '{provider}' is not supported")
