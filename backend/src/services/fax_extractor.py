from dataclasses import dataclass
from typing import List, Optional
from io import BytesIO
import os
import re
import uuid

from loguru import logger

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


def _get_main_provider(template: dict) -> str:
    env_provider = os.getenv("OCR_MAIN_PROVIDER")
    if env_provider:
        return env_provider.lower()
    provider = template.get("main_ocr_provider")
    if provider:
        return str(provider).lower()
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


def _get_row_fields(template: dict) -> list[str]:
    fields = template.get("main_ocr_row_fields")
    if isinstance(fields, list):
        return [str(field) for field in fields if str(field).strip()]
    return []


def _resolve_row_field(row: dict, field: str) -> object:
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
    normalized: list[list[str]] = []
    for row in rows:
        if isinstance(row, list):
            normalized.append([_coerce_row_cell(cell) for cell in row])
            continue
        if isinstance(row, dict):
            fields = _get_row_fields(template)
            if not fields:
                continue
            normalized.append(
                [_coerce_row_cell(_resolve_row_field(row, field)) for field in fields]
            )
    if not normalized:
        return None
    return normalized


def _rows_from_pipeline_payload(payload: object, template: dict) -> list[list[str]] | None:
    if not isinstance(payload, dict):
        return None
    table_raw = payload.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        rows = _rows_from_markdown(table_raw, template)
        if rows:
            return rows
    rows = _rows_from_payload(payload, template)
    if rows:
        return rows
    nested = payload.get("table")
    if isinstance(nested, dict):
        rows = _rows_from_payload(nested, template)
        if rows:
            return rows
    qty = payload.get("qty")
    if not isinstance(qty, dict):
        return None
    fields = _get_row_fields(template)
    if not fields:
        return None
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
    return normalized or None


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


def _field_from_header(header: str, fields: set[str]) -> str | None:
    token = _normalize_header_token(header)
    if "備考" in token or "remarks" in token or "note" in token:
        return _select_field(["remarks", "note"], fields)
    if "献立" in token or "メニュー" in token or "menu" in token:
        return _select_field(["menu", "menu_name"], fields)
    if "日付" in token or token.startswith("日"):
        return _select_field(["date_mmdd", "date"], fields)
    if "常食" in token or "regular" in token:
        if "2f" in token:
            return _select_field(["qty.regular_2f", "regular_2f"], fields)
        if "3f" in token:
            return _select_field(["qty.regular_3f", "regular_3f"], fields)
    if "軟菜" in token or "soft" in token:
        if "2f" in token:
            return _select_field(["qty.soft_2f", "soft_2f"], fields)
        if "3f" in token:
            return _select_field(["qty.soft_3f", "soft_3f"], fields)
    if "ミキサ" in token or "mixer" in token:
        if "2f" in token:
            return _select_field(["qty.mixer_2f", "mixer_2f"], fields)
        if "3f" in token:
            return _select_field(["qty.mixer_3f", "mixer_3f"], fields)
    return None


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
        rows.append([cell.strip() for cell in content.split("|")])
    if len(rows) >= 2:
        separator = rows[1]
        if separator and all(re.fullmatch(r"[-: ]+", cell) for cell in separator):
            return rows[0], rows[2:]
    return [], rows


def _is_subheader_row(row: list[str]) -> bool:
    if not row:
        return False
    non_empty = 0
    markers = 0
    for cell in row:
        token = _normalize_header_token(cell)
        if not token:
            continue
        non_empty += 1
        if token in {"2f", "3f", "2階", "3階"}:
            markers += 1
        else:
            return False
    return non_empty >= 2 and markers == non_empty


def _merge_header_rows(primary: list[str], secondary: list[str]) -> list[str]:
    combined: list[str] = []
    current_group = ""
    max_len = max(len(primary), len(secondary))
    for idx in range(max_len):
        h1 = primary[idx].strip() if idx < len(primary) else ""
        h2 = secondary[idx].strip() if idx < len(secondary) else ""
        if h1:
            current_group = h1
        if h2:
            group = current_group if current_group else h1
            combined.append(f"{group} {h2}".strip() if group else h2)
        else:
            combined.append(h1)
    return combined


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


def _rows_from_markdown(markdown: str, template: dict) -> list[list[str]] | None:
    fields = _get_row_fields(template)
    if not fields:
        return None
    tables = _extract_markdown_tables(markdown)
    if not tables:
        return None
    header, data = max(tables, key=lambda item: len(item[1]))
    if header and data and _is_subheader_row(data[0]):
        header = _merge_header_rows(header, data[0])
        data = data[1:]
    mapped_indexes: dict[int, int] = {}
    fields_set = set(fields)
    if header:
        for idx, cell in enumerate(header):
            field = _field_from_header(cell, fields_set)
            if field:
                mapped_indexes[idx] = fields.index(field)
    if not mapped_indexes:
        if header and len(header) == len(fields):
            mapped_indexes = {idx: idx for idx in range(len(header))}
        elif data and len(data[0]) == len(fields):
            mapped_indexes = {idx: idx for idx in range(len(fields))}
    if not mapped_indexes:
        return None
    normalized: list[list[str]] = []
    for row in data:
        output_row = [""] * len(fields)
        for src_idx, dest_idx in mapped_indexes.items():
            if src_idx < len(row):
                output_row[dest_idx] = row[src_idx]
        if any(cell.strip() for cell in output_row):
            normalized.append([_coerce_row_cell(cell) for cell in output_row])
    return normalized or None


def rows_from_markdown(markdown: str, template: dict) -> list[list[str]] | None:
    return _rows_from_markdown(markdown, template)


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
        if isinstance(output, dict):
            raw_name = output.get("facility_name")
            if isinstance(raw_name, str):
                facility_name = raw_name.strip() or None
            raw_dates = output.get("date_strings")
            if isinstance(raw_dates, list):
                date_strings = [str(item) for item in raw_dates if str(item).strip()]
        return FaxExtractedData(
            facility_name=facility_name,
            date_strings=date_strings,
            table_rows=rows,
            tokens=[],
            grid=grid,
            ocr_provider=provider,
        )

    raise RuntimeError(f"OCR provider '{provider}' is not supported")
