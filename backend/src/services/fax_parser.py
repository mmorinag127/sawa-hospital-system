import os
import re
from datetime import date, datetime
from typing import Any, Optional

from src.services.ingest_policy import parse_date_string


def _parse_number(
    value: str,
    *,
    strict_numeric_cell: bool = False,
    max_abs: float | None = None,
) -> Optional[float]:
    if value is None:
        return None
    text = str(value).translate(_FULLWIDTH_TRANSLATION)
    text = (
        text.replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("l", "1")
        .replace("|", "1")
    )
    text = (
        text.replace("<br>", " ")
        .replace("<br/>", " ")
        .replace("<br />", " ")
        .replace("，", ",")
        .replace("．", ".")
        .replace("。", ".")
        .replace("－", "-")
        .replace("ー", "-")
    )
    if strict_numeric_cell:
        cleaned = re.sub(r"[\s　]+", "", text)
        cleaned = cleaned.replace(",", "").strip("()[]（）")
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
            return None
    else:
        cleaned = re.sub(r"[^\d.-]", "", text)
        if cleaned == "":
            return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    if strict_numeric_cell and parsed < 0:
        return None
    if max_abs is not None and max_abs > 0 and abs(parsed) > max_abs:
        return None
    return parsed


def _normalize_cell(value: Any, normalize_whitespace: bool) -> str:
    if value is None:
        return ""
    text = str(value)
    if normalize_whitespace:
        text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_numeric_note_noise(text: str) -> bool:
    if not text:
        return False
    tokens = re.findall(r"\d+", text)
    if len(tokens) < 6:
        return False
    non_numeric = re.sub(r"[\d\s,./-]", "", text)
    return non_numeric == ""


_FULLWIDTH_TRANSLATION = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


def _normalize_token_text(text: str) -> str:
    return text.translate(_FULLWIDTH_TRANSLATION).strip()


def _canonical_daypart(value: Any) -> str:
    text = _normalize_cell(value, True).translate(_FULLWIDTH_TRANSLATION)
    text = re.sub(r"[\s　]+", "", text)
    if not text:
        return ""
    if "朝" in text:
        return "朝"
    if "昼" in text:
        return "昼"
    if "夕" in text or "夜" in text:
        return "夕"
    return ""


def _group_tokens_by_row(tokens: list[dict], tolerance: float) -> list[list[dict]]:
    rows: list[list[dict]] = []
    row_centers: list[float] = []
    for token in sorted(tokens, key=lambda t: t.get("y", 0)):
        y = token.get("y")
        if y is None:
            continue
        if not rows or abs(y - row_centers[-1]) > tolerance:
            rows.append([token])
            row_centers.append(y)
        else:
            rows[-1].append(token)
            row_centers[-1] = (row_centers[-1] + y) / 2
    return rows


def _assign_tokens_to_columns(
    row_tokens: list[dict],
    columns: list[dict],
    snap_tolerance: float,
) -> list[str]:
    ranges: list[tuple[float, float] | None] = []
    centers: list[float | None] = []
    for col in columns:
        x_range = col.get("x_range")
        if not x_range or len(x_range) != 2:
            ranges.append(None)
            centers.append(None)
            continue
        x0, x1 = x_range
        ranges.append((x0, x1))
        centers.append((x0 + x1) / 2)
    buckets: list[list[dict]] = [[] for _ in columns]
    for token in row_tokens:
        x = token.get("x")
        if x is None:
            continue
        chosen = None
        for idx, span in enumerate(ranges):
            if not span:
                continue
            if span[0] <= x <= span[1]:
                chosen = idx
                break
        if chosen is None and snap_tolerance > 0:
            best_idx = None
            best_dist = None
            for idx, center in enumerate(centers):
                if center is None:
                    continue
                dist = abs(x - center)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_idx = idx
            if best_dist is not None and best_dist <= snap_tolerance:
                chosen = best_idx
        if chosen is None:
            continue
        buckets[chosen].append(token)
    cells: list[str] = []
    for cell_tokens in buckets:
        if not cell_tokens:
            cells.append("")
            continue
        cell_tokens.sort(key=lambda t: t.get("x", 0))
        cell_text = " ".join(token.get("text", "") for token in cell_tokens).strip()
        cells.append(cell_text)
    return cells


def _rows_from_grouped_tokens(
    grouped_rows: list[list[dict]],
    columns: list[dict],
    snap_tolerance: float,
) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_tokens in grouped_rows:
        rows.append(_assign_tokens_to_columns(row_tokens, columns, snap_tolerance))
    return rows


def _row_bounds_from_grouped_tokens(
    grouped_rows: list[list[dict]], padding: float
) -> list[tuple[float, float]]:
    bounds: list[tuple[float, float]] = []
    for row_tokens in grouped_rows:
        ys = [token.get("y") for token in row_tokens if token.get("y") is not None]
        if not ys:
            bounds.append((0.0, 0.0))
            continue
        y_min = max(min(ys) - padding, 0.0)
        y_max = min(max(ys) + padding, 1.0)
        bounds.append((y_min, y_max))
    return bounds


def _find_header_center(header_tokens: list[dict], match_groups: list[list[str]]) -> float | None:
    positions: list[float] = []
    normalized_groups = [
        [_normalize_token_text(item).upper() for item in group if item]
        for group in match_groups
    ]
    for group in normalized_groups:
        matches = [
            token.get("x")
            for token in header_tokens
            if token.get("x") is not None
            and any(_normalize_token_text(token.get("text", "")).upper().find(key) >= 0 for key in group)
        ]
        matches = [x for x in matches if x is not None]
        if not matches:
            return None
        positions.append(sum(matches) / len(matches))
    if not positions:
        return None
    return sum(positions) / len(positions)


def _auto_columns_from_headers(tokens: list[dict], template: dict) -> list[dict]:
    headers = template.get("auto_headers") or []
    if not headers:
        return []
    band = template.get("auto_header_band") or [0.18, 0.26]
    y_min, y_max = band
    header_tokens = [t for t in tokens if t.get("y") is not None and y_min <= t["y"] <= y_max]
    if not header_tokens:
        return []
    table_box = template.get("table_box") or [0.0, 0.0, 1.0, 1.0]
    left_bound = table_box[0] if len(table_box) > 0 else 0.0
    right_bound = table_box[2] if len(table_box) > 2 else 1.0
    computed = []
    for header in headers:
        match_groups = header.get("match_groups") or []
        center = _find_header_center(header_tokens, match_groups)
        if center is None:
            continue
        computed.append({**header, "center": center})
    if not computed:
        return []
    computed.sort(key=lambda h: h["center"])
    centers = [h["center"] for h in computed]
    columns: list[dict] = []
    for idx, header in enumerate(computed):
        left = left_bound if idx == 0 else (centers[idx - 1] + centers[idx]) / 2
        right = right_bound if idx == len(computed) - 1 else (centers[idx] + centers[idx + 1]) / 2
        col = {k: v for k, v in header.items() if k not in {"center", "match_groups"}}
        col["x_range"] = [round(left, 4), round(right, 4)]
        columns.append(col)
    return columns


def _columns_from_grid(grid: dict | None, template: dict) -> list[dict]:
    if not grid:
        return []
    grid_columns = template.get("grid_columns") or []
    edges = grid.get("column_edges") or []
    if not grid_columns or len(edges) < 2:
        return []
    if len(edges) - 1 != len(grid_columns):
        return []
    columns: list[dict] = []
    for idx, column in enumerate(grid_columns):
        col = dict(column)
        col["x_range"] = [edges[idx], edges[idx + 1]]
        columns.append(col)
    return columns


def _filter_row_edges(row_edges: list[float], min_height: float) -> list[float]:
    if min_height <= 0 or len(row_edges) < 2:
        return row_edges
    filtered = [row_edges[0]]
    for edge in row_edges[1:]:
        if edge - filtered[-1] < min_height:
            continue
        filtered.append(edge)
    if len(filtered) < 2:
        return row_edges
    return filtered


def _rows_from_grid(
    tokens: list[dict],
    grid: dict,
    columns: list[dict],
    row_padding: float = 0.0,
    min_row_height: float = 0.0,
    max_rows: int = 0,
    snap_tolerance: float = 0.0,
) -> tuple[list[list[str]], list[tuple[float, float]]]:
    row_edges = grid.get("row_edges") or []
    if len(row_edges) < 2:
        return [], []
    row_edges = sorted(row_edges)
    row_edges = _filter_row_edges(row_edges, min_row_height)
    if max_rows and len(row_edges) - 1 > max_rows:
        return [], []
    rows: list[list[str]] = []
    row_bounds: list[tuple[float, float]] = []
    for row_idx in range(len(row_edges) - 1):
        y0 = max(row_edges[row_idx] - row_padding, 0.0)
        y1 = min(row_edges[row_idx + 1] + row_padding, 1.0)
        if y1 <= y0:
            continue
        row_tokens = [
            token
            for token in tokens
            if token.get("y") is not None
            and y0 <= token["y"]
            and (token["y"] < y1 or row_idx == len(row_edges) - 2)
        ]
        rows.append(_assign_tokens_to_columns(row_tokens, columns, snap_tolerance))
        row_bounds.append((y0, y1))
    return rows, row_bounds


def _grid_rows_are_reliable(
    rows: list[list[str]],
    columns: list[dict],
    header_rows: int,
    min_numeric_ratio: float,
    min_rows_ratio: float,
) -> bool:
    qty_indexes = [
        idx for idx, col in enumerate(columns) if col.get("role") in {"quantity", "quantity_change"}
    ]
    if not qty_indexes:
        return True
    data_rows = rows[header_rows:] if header_rows < len(rows) else []
    if not data_rows:
        return False
    total_cells = len(data_rows) * len(qty_indexes)
    if total_cells <= 0:
        return True
    numeric_cells = 0
    rows_with_numeric = 0
    for row in data_rows:
        row_has_numeric = False
        for idx in qty_indexes:
            if idx >= len(row):
                continue
            cell = _normalize_cell(row[idx], True)
            if _parse_number(cell) is not None:
                numeric_cells += 1
                row_has_numeric = True
        if row_has_numeric:
            rows_with_numeric += 1
    numeric_ratio = numeric_cells / total_cells if total_cells else 0.0
    rows_ratio = rows_with_numeric / len(data_rows) if data_rows else 0.0
    if numeric_ratio < min_numeric_ratio or rows_ratio < min_rows_ratio:
        return False
    return True


def _is_numeric_token(text: str) -> bool:
    if not text:
        return False
    normalized = _normalize_token_text(text)
    return bool(re.search(r"\d", normalized))


def _cluster_positions(values: list[float], tolerance: float) -> list[tuple[float, int]]:
    if not values:
        return []
    values = sorted(values)
    clusters: list[list[float]] = []
    for value in values:
        if not clusters or value - clusters[-1][-1] > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    result = []
    for cluster in clusters:
        center = sum(cluster) / len(cluster)
        result.append((center, len(cluster)))
    return result


def _auto_columns_from_numeric(tokens: list[dict], template: dict) -> list[dict]:
    config = template.get("auto_numeric_columns") or {}
    columns = config.get("columns") or []
    tail_column = config.get("tail_column")
    expected = int(config.get("expected_count") or len(columns))
    if not columns or expected <= 0:
        return []
    table_box = template.get("table_box") or [0.0, 0.0, 1.0, 1.0]
    x_band = config.get("x_band") or [table_box[0], table_box[2]]
    y_band = config.get("y_band") or [table_box[1], table_box[3]]
    x_min, x_max = x_band
    y_min, y_max = y_band
    numeric_positions = [
        token.get("x")
        for token in tokens
        if token.get("x") is not None
        and token.get("y") is not None
        and x_min <= token["x"] <= x_max
        and y_min <= token["y"] <= y_max
        and _is_numeric_token(token.get("text", ""))
    ]
    tolerance = float(config.get("cluster_tolerance", 0.02))
    clusters = _cluster_positions(numeric_positions, tolerance)
    min_tokens = int(config.get("min_tokens", 4))
    clusters = [cluster for cluster in clusters if cluster[1] >= min_tokens]
    if len(clusters) < expected:
        return []
    clusters.sort(key=lambda item: item[1], reverse=True)
    selected = sorted(clusters[:expected], key=lambda item: item[0])
    centers = [center for center, _ in selected]
    left_bound = x_band[0]
    right_bound = x_band[1]
    computed: list[dict] = []
    for idx, col in enumerate(columns[:expected]):
        left = left_bound if idx == 0 else (centers[idx - 1] + centers[idx]) / 2
        right = right_bound if idx == len(centers) - 1 else (centers[idx] + centers[idx + 1]) / 2
        computed.append({**col, "x_range": [round(left, 4), round(right, 4)]})
    if tail_column:
        tail_left = computed[-1]["x_range"][1] if computed else left_bound
        computed.append({**tail_column, "x_range": [round(tail_left, 4), round(right_bound, 4)]})
    return computed


def _infer_quantity_meta(field: str) -> tuple[str | None, str | None] | None:
    normalized = field.strip()
    if normalized.startswith("qty"):
        normalized = normalized[3:]
    normalized = normalized.lstrip("._")
    lowered = normalized.lower()

    diet_type = None
    area_id = None

    if any(token in normalized for token in ("常食", "通常")):
        diet_type = "regular"
    elif any(token in normalized for token in ("軟菜", "やわ", "ﾔﾜ", "ヤワ")):
        diet_type = "soft"
    elif any(token in normalized for token in ("ミキサ", "ﾐｷｻ")):
        diet_type = "mixer"

    area_match = re.search(r"(\\d)\\s*(?:f|ｆ|階)", lowered)
    if area_match:
        area_id = f"{area_match.group(1)}F"

    parts = [part for part in re.split(r"[._]", lowered) if part]
    for part in parts:
        if part in {"regular", "soft", "mixer"}:
            diet_type = diet_type or part
        elif part in {"2f", "3f"}:
            area_id = area_id or part.upper()
        elif part in {"2", "3"} and area_id is None:
            area_id = f"{part}F"
    if not diet_type and not area_id:
        return None
    return diet_type, area_id


def _columns_from_row_fields(template: dict) -> list[dict]:
    fields = template.get("main_ocr_row_fields")
    if not isinstance(fields, list):
        return []
    columns: list[dict] = []
    for idx, field in enumerate(fields):
        field_name = str(field).strip()
        compact = re.sub(r"[\\s　]+", "", field_name)
        col: dict = {"index": idx}
        if not field_name:
            columns.append(col)
            continue
        normalized = field_name.lower()
        if normalized.startswith("date") or "日付" in compact:
            col["role"] = "date"
        elif normalized in {"menu", "menu_name"} or any(token in compact for token in ("献立", "メニュー")):
            col["role"] = "menu_name"
        elif normalized in {"daypart", "day_part", "meal"} or any(token in compact for token in ("区分", "時間")):
            col["role"] = "daypart"
        elif "remark" in normalized or normalized in {"note", "notes"}:
            col["role"] = "note"
        else:
            qty_meta = _infer_quantity_meta(normalized)
            if qty_meta:
                diet_type, area_id = qty_meta
                col["role"] = "quantity"
                if diet_type:
                    col["diet_type"] = diet_type
                if area_id:
                    col["area_id"] = area_id
                col["bag_type"] = "standard"
            elif normalized.startswith("qty"):
                col["role"] = "quantity"
                col["bag_type"] = "standard"
        columns.append(col)
    return columns


def parse_order_lines(
    rows: list[list[str]],
    template: dict,
    received_at: datetime,
    quantity_rules: Optional[dict] = None,
    default_date: Optional[date] = None,
    tokens: Optional[list[dict]] = None,
    grid: Optional[dict] = None,
    pdf_bytes: bytes | None = None,
) -> list[dict[str, Any]]:
    quantity_rules = quantity_rules or {}
    zero_as_empty = quantity_rules.get("zero_as_empty", True)
    use_change_column = quantity_rules.get("use_change_column_if_present", True)
    strict_numeric_quantity_cell = bool(quantity_rules.get("strict_numeric_quantity_cell", False))
    allow_blank_structure_rows = bool(quantity_rules.get("allow_blank_structure_rows", False))
    rows_are_body_only = bool(quantity_rules.get("rows_are_body_only", False))
    max_quantity_abs_raw = quantity_rules.get("max_quantity_abs")
    if max_quantity_abs_raw is None and strict_numeric_quantity_cell:
        max_quantity_abs_raw = os.getenv("OCR_SHEET_MAX_QTY", "999")
    try:
        max_quantity_abs = (
            float(max_quantity_abs_raw)
            if max_quantity_abs_raw is not None
            else None
        )
    except Exception:
        max_quantity_abs = None

    header_rows = int(template.get("header_rows", 0))
    columns = template.get("columns", []) or []
    token_columns = template.get("token_columns") or []
    token_row_tolerance = float(template.get("token_row_tolerance", 0.008))
    raw_snap = template.get("grid_column_snap_tolerance")
    snap_tolerance = float(raw_snap) if raw_snap is not None else 0.003
    if tokens and (token_columns or template.get("grid_columns")):
        grid_columns = _columns_from_grid(grid, template)
        auto_columns = _auto_columns_from_headers(tokens, template) if template.get("auto_headers") else []
        if not auto_columns and template.get("auto_numeric_columns"):
            auto_columns = _auto_columns_from_numeric(tokens, template)
        base_columns = [col for col in token_columns if col.get("role") in {"date", "menu_name"}]
        if grid_columns:
            combined = grid_columns
        elif auto_columns:
            auto_roles = {col.get("role") for col in auto_columns if col.get("role")}
            if auto_roles:
                base_columns = [col for col in base_columns if col.get("role") not in auto_roles]
            combined = base_columns + auto_columns
        else:
            combined = base_columns + [
                col for col in token_columns if col.get("role") not in {"date", "menu_name"}
            ]
        combined = [dict(col) for col in combined]
        combined.sort(key=lambda col: (col.get("x_range") or [0])[0])
        for idx, col in enumerate(combined):
            col["index"] = idx
        columns = combined
        row_bounds: list[tuple[float, float]] = []
        use_grid_rows = bool(template.get("grid_use_rows", True))
        if grid_columns and grid and use_grid_rows:
            grid_rows, row_bounds = _rows_from_grid(
                tokens,
                grid,
                columns,
                row_padding=float(template.get("grid_row_padding", 0.0)),
                min_row_height=float(template.get("grid_min_row_height", 0.0)),
                max_rows=int(template.get("grid_max_rows", 0) or 0),
                snap_tolerance=snap_tolerance,
            )
            min_rows = int(template.get("grid_min_rows", 0))
            grid_min_numeric_ratio = float(template.get("grid_min_numeric_ratio", 0.12) or 0.12)
            grid_min_rows_ratio = float(template.get("grid_min_rows_ratio", 0.08) or 0.08)
            grid_ok = _grid_rows_are_reliable(
                grid_rows,
                columns,
                int(template.get("grid_header_rows", header_rows)),
                grid_min_numeric_ratio,
                grid_min_rows_ratio,
            )
            if grid_rows and (not min_rows or len(grid_rows) >= min_rows) and grid_ok:
                rows = grid_rows
                header_rows = int(template.get("grid_header_rows", header_rows))
            else:
                row_bounds = []
        if not row_bounds:
            grouped_rows = _group_tokens_by_row(tokens, token_row_tolerance)
            rows = _rows_from_grouped_tokens(grouped_rows, columns, snap_tolerance)
            row_bounds = _row_bounds_from_grouped_tokens(
                grouped_rows, float(template.get("token_row_padding", 0.006))
            )
    if not columns:
        columns = _columns_from_row_fields(template)
    effective_header_rows = 0 if rows_are_body_only else header_rows
    large_cell_mode = bool(template.get("large_cell_mode", False))
    fill_forward_roles = set(template.get("fill_forward_roles") or [])
    if large_cell_mode:
        fill_forward_roles.update({"date", "daypart"})
    fill_forward_roles.discard("menu_name")
    fill_missing_date_with_hint = bool(template.get("fill_missing_date_with_hint", False))
    if "fill_missing_date_with_first_seen" in template:
        fill_missing_date_with_first_seen = bool(template.get("fill_missing_date_with_first_seen"))
    else:
        fill_missing_date_with_first_seen = large_cell_mode
    normalize_whitespace = bool(template.get("normalize_whitespace", True))
    carry_forward: dict[str, Any] = {role: None for role in fill_forward_roles}
    first_date_in_table: Optional[date] = None
    if fill_missing_date_with_first_seen:
        date_indexes = [
            col.get("index") for col in columns if col.get("role") == "date"
        ]
        for row in rows[effective_header_rows:]:
            for idx in date_indexes:
                if idx is None or idx >= len(row):
                    continue
                cell = _normalize_cell(row[idx], normalize_whitespace)
                parsed = parse_date_string(cell, received_at)
                if parsed:
                    first_date_in_table = parsed
                    break
            if first_date_in_table:
                break
    lines: list[dict[str, Any]] = []

    quantity_columns = {
        col["index"]: col
        for col in columns
        if col.get("role") == "quantity"
    }
    change_columns = [
        col for col in columns if col.get("role") == "quantity_change"
    ]

    if "grid_quantity_ffill" in template:
        qty_ffill = bool(template.get("grid_quantity_ffill"))
    else:
        qty_ffill = False
    qty_ffill_scope = template.get("grid_quantity_ffill_scope", "date")
    qty_carry: dict[int, float] = {}
    qty_carry_key = None
    carry_forward_daypart_date = None

    for source_row_index, row in enumerate(rows[effective_header_rows:]):
        base = {
            "date": None,
            "daypart": None,
            "menu_name": None,
            "change_note": None,
        }
        quantities: dict[int, dict[str, Any]] = {}

        for col in columns:
            idx = col.get("index")
            if idx is None or idx >= len(row):
                continue
            cell = _normalize_cell(row[idx], normalize_whitespace)
            role = col.get("role")
            if role == "date":
                base["date"] = parse_date_string(cell, received_at)
            elif role == "daypart":
                base["daypart"] = _canonical_daypart(cell) or None
            elif role == "menu_name":
                base["menu_name"] = cell or None
            elif role == "note":
                base["change_note"] = None if _is_numeric_note_noise(cell) else cell or None
            elif role == "quantity":
                qty = _parse_number(
                    cell,
                    strict_numeric_cell=strict_numeric_quantity_cell,
                    max_abs=max_quantity_abs,
                )
                if qty == 0 and zero_as_empty:
                    qty = None
                quantities[idx] = {
                    "diet_type": col.get("diet_type"),
                    "area_id": col.get("area_id"),
                    "bag_type": col.get("bag_type"),
                    "quantity_original": qty,
                    "quantity_corrected": None,
                }

        current_row_date = base.get("date")
        if (
            "daypart" in fill_forward_roles
            and isinstance(current_row_date, date)
            and isinstance(carry_forward_daypart_date, date)
            and current_row_date != carry_forward_daypart_date
        ):
            carry_forward["daypart"] = None

        for role in fill_forward_roles:
            if role not in base:
                continue
            value = base.get(role)
            if value:
                carry_forward[role] = value
                if role == "daypart":
                    carry_forward_daypart_date = current_row_date
            else:
                base[role] = carry_forward.get(role)
        if fill_missing_date_with_hint and base.get("date") is None and default_date is not None:
            base["date"] = default_date
        if base.get("date") is None and first_date_in_table is not None:
            base["date"] = first_date_in_table

        if qty_ffill:
            current_key = base.get("date") if qty_ffill_scope == "date" else None
            if current_key is not None and current_key != qty_carry_key:
                qty_carry = {}
                qty_carry_key = current_key

        if use_change_column:
            for col in change_columns:
                idx = col.get("index")
                if idx is None or idx >= len(row):
                    continue
                cell = _normalize_cell(row[idx], normalize_whitespace)
                qty = _parse_number(
                    cell,
                    strict_numeric_cell=strict_numeric_quantity_cell,
                    max_abs=max_quantity_abs,
                )
                if qty == 0 and zero_as_empty:
                    qty = None
                source_index = col.get("source_index")
                if source_index in quantities and qty is not None:
                    quantities[source_index]["quantity_corrected"] = qty

        if qty_ffill:
            for idx, qty_info in quantities.items():
                current_value = qty_info.get("quantity_corrected")
                if current_value is None:
                    current_value = qty_info.get("quantity_original")
                if current_value is None and idx in qty_carry:
                    qty_info["quantity_original"] = qty_carry[idx]
                elif current_value is not None:
                    qty_carry[idx] = current_value

        for qty_info in quantities.values():
            if (
                not allow_blank_structure_rows
                and not base["date"]
                and not base["menu_name"]
                and not base["daypart"]
            ):
                continue
            if qty_info.get("quantity_original") is None and qty_info.get("quantity_corrected") is None:
                continue
            lines.append(
                {
                    "date": base["date"],
                    "daypart": base["daypart"],
                    "menu_name": base["menu_name"],
                    "diet_type": qty_info.get("diet_type"),
                    "area_id": qty_info.get("area_id"),
                    "bag_type": qty_info.get("bag_type"),
                    "quantity_original": qty_info.get("quantity_original"),
                    "quantity_corrected": qty_info.get("quantity_corrected"),
                    "change_note": base.get("change_note"),
                    "source_row_index": source_row_index,
                }
            )

    return lines
