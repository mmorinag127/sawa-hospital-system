from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from src.services import fax_extractor


_POSITION_FALLBACK_SOURCE = "position_fallback"
_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}")


def _clean_cell_text(value: object) -> str:
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()


def _looks_like_menu_text(value: object) -> bool:
    text = _clean_cell_text(value)
    if not text:
        return False
    if _DATE_RE.search(text):
        return False
    if fax_extractor._looks_like_daypart(text):
        return False
    return bool(re.search(r"[A-Za-z一-龥ぁ-んァ-ヶ]", text))


def _looks_like_numeric_text(value: object) -> bool:
    text = _clean_cell_text(value)
    if not text:
        return False
    normalized = (
        text.replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("l", "1")
        .replace("|", "1")
    )
    return bool(re.fullmatch(r"[0-9]+", normalized))


def _infer_body_start_row(rows: list[list[str]]) -> int:
    for row_index, row in enumerate(rows):
        joined = " ".join(_clean_cell_text(cell) for cell in row if _clean_cell_text(cell))
        if _DATE_RE.search(joined):
            return row_index
    return 0


def _column_bounds(table: dict[str, Any]) -> dict[int, list[float]]:
    bounds: dict[int, list[float]] = {}
    for cell in table.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        bbox = cell.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            col_index = int(cell.get("col_index") or cell.get("col") or 0)
        except Exception:
            continue
        current = bounds.get(col_index)
        if current is None:
            bounds[col_index] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            continue
        current[0] = min(current[0], float(bbox[0]))
        current[1] = min(current[1], float(bbox[1]))
        current[2] = max(current[2], float(bbox[2]))
        current[3] = max(current[3], float(bbox[3]))
    return bounds


def _row_bounds(table: dict[str, Any]) -> dict[int, list[float]]:
    bounds: dict[int, list[float]] = {}
    for cell in table.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        bbox = cell.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            row_index = int(cell.get("row_index") or cell.get("row") or 0)
        except Exception:
            continue
        current = bounds.get(row_index)
        if current is None:
            bounds[row_index] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            continue
        current[0] = min(current[0], float(bbox[0]))
        current[1] = min(current[1], float(bbox[1]))
        current[2] = max(current[2], float(bbox[2]))
        current[3] = max(current[3], float(bbox[3]))
    return bounds


def _infer_quantity_subgrid(
    table: dict[str, Any],
    *,
    template: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rows = [list(row) for row in (table.get("rows") or []) if isinstance(row, list)]
    col_count = int(table.get("col_count") or 0)
    row_count = int(table.get("row_count") or 0)
    if not rows or col_count <= 0 or row_count <= 0:
        return None
    body_start_row = _infer_body_start_row(rows)
    sampled_rows = rows[body_start_row : min(len(rows), body_start_row + 24)]
    if not sampled_rows:
        return None
    header_rows = rows[:body_start_row]
    menu_scores: list[tuple[int, int, int]] = []
    for col_index in range(col_count):
        menu_hits = 0
        numeric_hits = 0
        for row in sampled_rows:
            if col_index >= len(row):
                continue
            cell = row[col_index]
            if _looks_like_menu_text(cell):
                menu_hits += 1
            if _looks_like_numeric_text(cell):
                numeric_hits += 1
        menu_scores.append((col_index, menu_hits, numeric_hits))
    menu_col_index = max(menu_scores, key=lambda item: (item[1], -item[2]))[0]
    template_quantity_source_indexes = fax_extractor._template_explicit_quantity_source_indexes(
        template,
        observed_width=col_count,
    )
    quantity_start_col_index: int | None = (
        min(template_quantity_source_indexes) if template_quantity_source_indexes else None
    )
    if quantity_start_col_index is None:
        for col_index in range(menu_col_index + 1, col_count):
            header_text = " ".join(
                _clean_cell_text(row[col_index])
                for row in header_rows
                if col_index < len(row) and _clean_cell_text(row[col_index])
            )
            numeric_hits = sum(
                1
                for row in sampled_rows
                if col_index < len(row) and _looks_like_numeric_text(row[col_index])
            )
            if header_text or numeric_hits >= 2:
                quantity_start_col_index = col_index
                break
    if quantity_start_col_index is None:
        return None
    return {
        "body_start_row": body_start_row,
        "menu_col_index": menu_col_index,
        "quantity_start_col_index": quantity_start_col_index,
        "row_count": max(0, row_count - body_start_row),
        "quantity_col_count": (
            len(template_quantity_source_indexes)
            if template_quantity_source_indexes
            else max(0, col_count - quantity_start_col_index)
        ),
    }


def _strict_template_semantics_ready(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("template_resolution")
    if not isinstance(resolution, dict):
        return False
    resolved_template_id = str(
        resolution.get("resolved_template_id") or resolution.get("template_id") or ""
    ).strip()
    blocked = bool(
        resolution.get("blocked")
        or any(str(item or "").strip() for item in (resolution.get("blocked_reasons") or []))
    )
    table_box = payload.get("table_box")
    column_edges = payload.get("grid_column_edges")
    return bool(
        resolved_template_id
        and not blocked
        and isinstance(table_box, list)
        and len(table_box) == 4
        and isinstance(column_edges, list)
        and len(column_edges) >= 2
    )


def _resolved_position_fallback_source_indexes(
    resolution: dict[str, Any] | None,
) -> list[int]:
    if not isinstance(resolution, dict):
        return []
    resolved_value = str(
        resolution.get("resolved_value")
        or resolution.get("resolved_column_mapping_id")
        or ""
    ).strip()
    if not resolved_value:
        return []
    indexes: list[int] = []
    for token in resolved_value.split("|"):
        source_col_index, _separator, _field = token.partition(":")
        try:
            indexes.append(int(source_col_index))
        except Exception:
            continue
    return indexes


def _payload_observed_width(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    max_width = 0
    for table in fax_extractor._collect_structured_tables(payload):
        if not isinstance(table, dict):
            continue
        matrix = _normalize_matrix(table)
        if not matrix:
            continue
        max_width = max(max_width, max((len(row) for row in matrix), default=0))
    return max_width or None


def _payload_uses_position_normalized_evidence(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    artifacts = payload.get("page_correction_artifacts")
    if isinstance(artifacts, dict) and bool(artifacts.get("position_normalized")):
        return True
    correction = payload.get("page_correction")
    return bool(isinstance(correction, dict) and correction.get("applied"))


def _position_fallback_matches_template_contract(
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("column_mapping_resolution")
    if not isinstance(resolution, dict):
        return False
    if str(resolution.get("decision_source") or "").strip() != _POSITION_FALLBACK_SOURCE:
        return False
    mapped_source_indexes = _resolved_position_fallback_source_indexes(resolution)
    if not mapped_source_indexes:
        return False
    if not isinstance(template, dict):
        return True
    observed_width = _payload_observed_width(payload)
    aux_source_indexes = set(
        fax_extractor._template_explicit_source_indexes_for_roles(
            template,
            roles={"aux"},
            observed_width=observed_width,
        )
    )
    if any(source_col_index in aux_source_indexes for source_col_index in mapped_source_indexes):
        return False
    quantity_source_indexes = fax_extractor._template_explicit_quantity_source_indexes(
        template,
        observed_width=observed_width,
    )
    if not quantity_source_indexes:
        return True
    if (
        not bool(resolution.get("partial_quantity_mapping"))
        and _payload_uses_position_normalized_evidence(payload)
    ):
        return len(mapped_source_indexes) == len(quantity_source_indexes)
    if bool(resolution.get("partial_quantity_mapping")):
        return set(mapped_source_indexes).issubset(set(quantity_source_indexes))
    return list(mapped_source_indexes) == list(quantity_source_indexes)


def _invalidated_position_fallback_resolution(payload: dict[str, Any]) -> dict[str, Any]:
    resolution = payload.get("column_mapping_resolution")
    next_resolution = dict(resolution) if isinstance(resolution, dict) else {}
    next_resolution["resolved_value"] = None
    next_resolution["resolved_column_mapping_id"] = None
    next_resolution["blocked"] = True
    next_resolution["requires_user_choice"] = False
    next_resolution["partial_quantity_mapping"] = False
    next_resolution["blocked_reasons"] = ["column_mapping_contract_mismatch"]
    next_resolution["decision_source"] = _POSITION_FALLBACK_SOURCE
    next_resolution["confidence"] = 0.0
    return next_resolution


def _payload_has_position_fallback_resolution(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("column_mapping_resolution")
    if not isinstance(resolution, dict):
        return False
    return str(resolution.get("decision_source") or "").strip() == _POSITION_FALLBACK_SOURCE


def _position_fallback_already_ready(
    payload: dict[str, Any] | None,
    *,
    template: dict[str, Any] | None = None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("column_mapping_resolution")
    if not isinstance(resolution, dict):
        return False
    if bool(resolution.get("partial_quantity_mapping")):
        return False
    return bool(
        str(resolution.get("resolved_value") or resolution.get("resolved_column_mapping_id") or "").strip()
        and str(resolution.get("decision_source") or "").strip() == _POSITION_FALLBACK_SOURCE
        and not _existing_position_fallback_requires_choice(payload)
        and _position_fallback_matches_template_contract(payload, template)
    )


def _row_fields(template: dict[str, Any] | None) -> list[str]:
    if not isinstance(template, dict):
        return []
    return fax_extractor._get_row_fields(template)


def _is_quantity_field(field: object) -> bool:
    return str(field or "").strip().startswith("qty.")


def _stable_candidate_id(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return f"pcm-{digest[:12]}"


def _candidate_score(candidate: dict[str, Any]) -> float | None:
    raw = candidate.get("score")
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _normalize_position_candidates(candidates_raw: object) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(candidates_raw, list):
        return normalized
    seen_values: set[str] = set()
    for item in candidates_raw:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        normalized.append(dict(item))
    return normalized


def _position_fallback_choice_required(
    *,
    resolved_value: str | None,
    candidates: list[dict[str, Any]],
    explicit: bool = False,
) -> bool:
    if explicit:
        return True
    if len(candidates) < 2:
        return False
    top_score = _candidate_score(candidates[0])
    second_score = _candidate_score(candidates[1])
    if top_score is None or second_score is None:
        return not bool(str(resolved_value or "").strip())
    return (top_score - second_score) < 0.15 or top_score < 0.85


def _existing_position_fallback_requires_choice(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("column_mapping_resolution")
    if not isinstance(resolution, dict):
        return False
    if str(resolution.get("decision_source") or "").strip() != _POSITION_FALLBACK_SOURCE:
        return False
    candidates = _normalize_position_candidates(
        resolution.get("candidates")
        if isinstance(resolution.get("candidates"), list)
        else payload.get("column_mapping_candidates")
    )
    blocked_reasons = {
        str(item or "").strip()
        for item in (resolution.get("blocked_reasons") or [])
        if str(item or "").strip()
    }
    explicit = bool(resolution.get("requires_user_choice")) or "column_mapping_choice_required" in blocked_reasons
    resolved_value = str(
        resolution.get("resolved_value")
        or resolution.get("resolved_column_mapping_id")
        or ""
    ).strip() or None
    return _position_fallback_choice_required(
        resolved_value=resolved_value,
        candidates=candidates,
        explicit=explicit,
    )


def _normalize_matrix(table_payload: dict[str, Any]) -> list[list[str]] | None:
    raw_rows = table_payload.get("rows")
    if isinstance(raw_rows, list) and raw_rows:
        return fax_extractor._normalize_table_matrix_rows(raw_rows)
    raw_cells = table_payload.get("cells")
    if isinstance(raw_cells, list) and raw_cells:
        return fax_extractor._matrix_from_structured_cells(
            raw_cells,
            row_count_hint=table_payload.get("row_count")
            if isinstance(table_payload.get("row_count"), int)
            else table_payload.get("n_row"),
            col_count_hint=table_payload.get("col_count")
            if isinstance(table_payload.get("col_count"), int)
            else table_payload.get("n_col"),
        )
    return None


def _column_header_text(matrix: list[list[str]], source_col_index: int) -> str:
    tokens: list[str] = []
    for row in matrix[:3]:
        if source_col_index >= len(row):
            continue
        cell = str(row[source_col_index] or "").strip()
        if cell and cell not in tokens:
            tokens.append(cell)
    return " / ".join(tokens[:2])


def _field_label(field: str) -> str:
    token = str(field or "").strip()
    mapping = {
        "date_mmdd": "日付",
        "date": "日付",
        "daypart": "区分",
        "menu": "メニュー",
        "menu_name": "メニュー",
        "remarks": "備考",
        "note": "備考",
    }
    if token in mapping:
        return mapping[token]
    return token


def _mapping_value(quantity_pairs: list[tuple[int, str]]) -> str:
    return "|".join(f"{source_col_index}:{field}" for source_col_index, field in quantity_pairs)


def _find_first_quantity_field(fields: list[str], marker: str) -> str | None:
    token = str(marker or "").strip().lower()
    if not token:
        return None
    for field in fields:
        normalized = str(field or "").strip().lower()
        if _is_quantity_field(normalized) and token in normalized:
            return field
    return None


def _select_header_quantity_field(header_text: str, fields: list[str]) -> str | None:
    direct = fax_extractor._field_from_header(header_text, set(fields))
    if _is_quantity_field(direct):
        return direct
    normalized = fax_extractor._normalize_header_token(header_text)
    if not normalized:
        return None
    if "肉" in normalized and "魚" not in normalized:
        return _find_first_quantity_field(fields, "no_meat")
    if "魚" in normalized and "肉" not in normalized:
        return _find_first_quantity_field(fields, "no_fish")
    if "change1" in normalized or "変更1" in normalized:
        return _find_first_quantity_field(fields, "change_1")
    if "change2" in normalized or "変更2" in normalized:
        return _find_first_quantity_field(fields, "change_2")
    return None


def _merged_header_for_matrix(matrix: list[list[str]], fields: list[str]) -> list[str]:
    normalized_rows = [list(row) for row in matrix if isinstance(row, list)]
    if not normalized_rows:
        return []
    fields_set = set(fields)
    header_height: int | None = None
    for idx, row in enumerate(normalized_rows[:6]):
        if fax_extractor._looks_like_data_row(row, fields_set):
            header_height = idx
            break
    if header_height is None:
        header_height = 1 if len(normalized_rows) > 1 else 0
    header_rows = normalized_rows[:header_height]
    data = normalized_rows[header_height:]
    if not data and normalized_rows:
        header_rows = normalized_rows[:1]
        data = normalized_rows[1:]
    header = fax_extractor._merge_header_group(header_rows)
    if data and fax_extractor._count_mapped_header_cells(header, fields_set) < 2:
        for idx, candidate in enumerate(data[:3]):
            if fax_extractor._count_mapped_header_cells(candidate, fields_set) >= 2:
                header = candidate
                data = data[idx + 1 :]
                break
    while header and data and fax_extractor._is_subheader_row(data[0]):
        header = fax_extractor._merge_header_rows(header, data[0])
        data = data[1:]
    return [str(cell or "").strip() for cell in header]


def _header_quantity_pairs_from_range(
    *,
    matrix: list[list[str]],
    fields: list[str],
    lower_bound: int,
    upper_bound: int,
    allowed_source_indexes: set[int] | None = None,
) -> list[tuple[int, str]]:
    header = _merged_header_for_matrix(matrix, fields)
    if not header:
        return []
    upper_bound = min(upper_bound, len(header))
    if upper_bound <= lower_bound:
        return []

    mapped_by_field: dict[str, int] = {}
    change_candidate_cols: list[int] = []
    for source_col_index in range(lower_bound, upper_bound):
        if allowed_source_indexes is not None and source_col_index not in allowed_source_indexes:
            continue
        header_text = str(header[source_col_index] or "").strip()
        if not header_text:
            continue
        normalized = fax_extractor._normalize_header_token(header_text)
        field = _select_header_quantity_field(header_text, fields)
        if field and field not in mapped_by_field:
            mapped_by_field[field] = source_col_index
            continue
        if "変更" in header_text or "change" in normalized:
            change_candidate_cols.append(source_col_index)

    unresolved_change_fields = [
        field
        for field in fields
        if _is_quantity_field(field)
        and "change_" in str(field or "")
        and field not in mapped_by_field
    ]
    for source_col_index, field in zip(sorted(change_candidate_cols), unresolved_change_fields):
        mapped_by_field[field] = source_col_index

    pairs: list[tuple[int, str]] = []
    seen_sources: set[int] = set()
    for field in fields:
        if not _is_quantity_field(field):
            continue
        source_col_index = mapped_by_field.get(field)
        if source_col_index is None or source_col_index in seen_sources:
            continue
        pairs.append((source_col_index, field))
        seen_sources.add(source_col_index)
    return pairs


def _augment_quantity_pairs_from_header(
    *,
    matrix: list[list[str]],
    fields: list[str],
    mapped_indexes: dict[int, int],
    quantity_pairs: list[tuple[int, str]],
    allowed_source_indexes: set[int] | None = None,
) -> list[tuple[int, str]]:
    header = _merged_header_for_matrix(matrix, fields)
    if not header:
        return quantity_pairs
    menu_source_indexes = [
        src_idx
        for src_idx, dest_idx in mapped_indexes.items()
        if 0 <= dest_idx < len(fields) and fields[dest_idx] in {"menu", "menu_name"}
    ]
    note_source_indexes = [
        src_idx
        for src_idx, dest_idx in mapped_indexes.items()
        if 0 <= dest_idx < len(fields) and fields[dest_idx] in {"remarks", "note"}
    ]
    lower_bound = (max(menu_source_indexes) + 1) if menu_source_indexes else 0
    upper_bound = min(note_source_indexes) if note_source_indexes else len(header)
    upper_bound = min(upper_bound, len(header))
    if upper_bound <= lower_bound:
        return quantity_pairs

    filtered_pairs = [
        (source_col_index, field)
        for source_col_index, field in quantity_pairs
        if lower_bound <= source_col_index < upper_bound
        and (allowed_source_indexes is None or source_col_index in allowed_source_indexes)
    ]
    mapped_by_field = {field: src_idx for src_idx, field in filtered_pairs}

    change_candidate_cols: list[int] = []
    for source_col_index in range(lower_bound, upper_bound):
        if allowed_source_indexes is not None and source_col_index not in allowed_source_indexes:
            continue
        if source_col_index in {item[0] for item in filtered_pairs}:
            continue
        header_text = str(header[source_col_index] or "").strip()
        if not header_text:
            continue
        normalized = fax_extractor._normalize_header_token(header_text)
        field = _select_header_quantity_field(header_text, fields)
        if field and field not in mapped_by_field:
            mapped_by_field[field] = source_col_index
            continue
        if "変更" in header_text or "change" in normalized:
            change_candidate_cols.append(source_col_index)

    unresolved_change_fields = [
        field
        for field in fields
        if _is_quantity_field(field)
        and "change_" in str(field or "")
        and field not in mapped_by_field
    ]
    for source_col_index, field in zip(sorted(change_candidate_cols), unresolved_change_fields):
        mapped_by_field[field] = source_col_index

    augmented_pairs = list(filtered_pairs)
    existing_sources = {item[0] for item in augmented_pairs}
    for field in fields:
        source_col_index = mapped_by_field.get(field)
        if not _is_quantity_field(field) or source_col_index is None or source_col_index in existing_sources:
            continue
        augmented_pairs.append((source_col_index, field))
        existing_sources.add(source_col_index)
    augmented_pairs.sort(key=lambda item: item[0])
    return augmented_pairs


def _filter_quantity_pairs_to_allowed_sources(
    quantity_pairs: list[tuple[int, str]],
    allowed_source_indexes: set[int] | None,
) -> list[tuple[int, str]]:
    if not allowed_source_indexes:
        return list(quantity_pairs)
    filtered: list[tuple[int, str]] = []
    seen_fields: set[str] = set()
    seen_sources: set[int] = set()
    for source_col_index, field in quantity_pairs:
        if source_col_index not in allowed_source_indexes:
            continue
        if source_col_index in seen_sources or field in seen_fields:
            continue
        filtered.append((source_col_index, field))
        seen_sources.add(source_col_index)
        seen_fields.add(field)
    return filtered


def _source_column_has_numeric_signal(
    *,
    matrix: list[list[str]],
    body_start_row: int,
    source_col_index: int,
) -> bool:
    start_row = max(0, int(body_start_row))
    for row in matrix[start_row:]:
        if not isinstance(row, list) or source_col_index >= len(row):
            continue
        value = _clean_cell_text(row[source_col_index])
        if not value:
            continue
        if _looks_like_numeric_text(value):
            return True
        if re.fullmatch(r"\d+\s*(切|枚|個)", value):
            return True
    return False


def _table_grid_metadata(table_payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_cells = table_payload.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        return None
    col_bounds = _column_bounds(table_payload)
    row_bounds = _row_bounds(table_payload)
    if not col_bounds or not row_bounds:
        return None
    sorted_col_indexes = sorted(col_bounds.keys())
    sorted_row_indexes = sorted(row_bounds.keys())
    first_col = col_bounds[sorted_col_indexes[0]]
    last_col = col_bounds[sorted_col_indexes[-1]]
    first_row = row_bounds[sorted_row_indexes[0]]
    last_row = row_bounds[sorted_row_indexes[-1]]
    return {
        "table_box": [
            float(first_col[0]),
            float(first_row[1]),
            float(last_col[2]),
            float(last_row[3]),
        ],
        "grid_column_edges": [float(col_bounds[idx][0]) for idx in sorted_col_indexes] + [float(last_col[2])],
        "grid_row_edges": [float(row_bounds[idx][1]) for idx in sorted_row_indexes] + [float(last_row[3])],
    }


def _fallback_structured_table_mapping(
    matrix: list[list[str]],
    template: dict[str, Any],
) -> dict[str, Any] | None:
    fields = _row_fields(template)
    if not fields:
        return None
    normalized_rows = fax_extractor._normalize_table_matrix_rows(matrix)
    if not normalized_rows:
        return None
    fields_set = set(fields)
    header_height: int | None = None
    for idx, row in enumerate(normalized_rows[:6]):
        if fax_extractor._looks_like_data_row(row, fields_set):
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
    header = fax_extractor._merge_header_group(header_rows)
    while header and data and fax_extractor._is_subheader_row(data[0]):
        header = fax_extractor._merge_header_rows(header, data[0])
        data = data[1:]
        header_height += 1

    mapped_indexes: dict[int, int] = {}
    used_dest_indexes: set[int] = set()
    for idx, cell in enumerate(header):
        field = fax_extractor._field_from_header(cell, fields_set)
        if not field:
            continue
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

    mapped_indexes = fax_extractor._infer_mapped_indexes(
        data=data,
        fields=fields,
        mapped_indexes=mapped_indexes,
    )
    mapped_indexes = fax_extractor._prefer_positional_quantity_mapping_when_width_matches(
        header=header,
        data=data,
        fields=fields,
        mapped_indexes=mapped_indexes,
    )
    mapped_indexes = fax_extractor._realign_structural_mapping_by_observed_content(
        data=data,
        fields=fields,
        mapped_indexes=mapped_indexes,
    )
    if not mapped_indexes:
        return None

    row_map: dict[int, int] = {}
    for raw_row_index, row in enumerate(data, start=header_height):
        if not isinstance(row, list):
            continue
        if not any(_clean_cell_text(cell) for cell in row):
            continue
        row_map[raw_row_index] = len(row_map)
    if not row_map:
        return None
    return {
        "fields": fields,
        "mapped_indexes": mapped_indexes,
        "row_map": row_map,
        "projection_variant": "position_fallback_observed",
    }


def _position_candidate_for_table(
    *,
    table_payload: dict[str, Any],
    matrix: list[list[str]],
    template: dict[str, Any],
    prefer_observed_header_fill: bool = False,
) -> dict[str, Any] | None:
    resolved = fax_extractor._resolve_structured_table_mapping(matrix, template)
    mapping_meta = resolved[0] if isinstance(resolved, tuple) and len(resolved) == 2 else None
    if not isinstance(mapping_meta, dict):
        mapping_meta = _fallback_structured_table_mapping(matrix, template)
    if not isinstance(mapping_meta, dict):
        return None
    fields = mapping_meta.get("fields")
    mapped_indexes = mapping_meta.get("mapped_indexes")
    if not isinstance(fields, list) or not isinstance(mapped_indexes, dict):
        return None
    observed_width = max((len(row) for row in matrix), default=0)
    template_quantity_source_indexes = fax_extractor._template_explicit_quantity_source_indexes(
        template,
        observed_width=observed_width,
    )
    strict_template_quantity_alignment = fax_extractor._template_requires_strict_quantity_source_alignment(
        template,
        observed_width=observed_width,
    )
    if (
        strict_template_quantity_alignment
        and not template_quantity_source_indexes
    ):
        return None
    allowed_quantity_source_indexes = (
        set(template_quantity_source_indexes) if template_quantity_source_indexes else None
    )

    quantity_dest_indexes = {
        idx for idx, field in enumerate(fields) if _is_quantity_field(field)
    }
    if not quantity_dest_indexes:
        return None
    quantity_fields = [fields[idx] for idx in sorted(quantity_dest_indexes)]

    quantity_pairs: list[tuple[int, str]] = []
    mapped_structural_fields: set[str] = set()
    for raw_source_col, raw_dest_idx in mapped_indexes.items():
        try:
            source_col_index = int(raw_source_col)
            dest_idx = int(raw_dest_idx)
        except Exception:
            continue
        if dest_idx < 0 or dest_idx >= len(fields):
            continue
        field = str(fields[dest_idx] or "").strip()
        if not field:
            continue
        if dest_idx in quantity_dest_indexes:
            quantity_pairs.append((source_col_index, field))
        elif field in {"date_mmdd", "date", "daypart", "menu", "menu_name"}:
            mapped_structural_fields.add(field)

    subgrid = _infer_quantity_subgrid(table_payload, template=template)
    if strict_template_quantity_alignment and template_quantity_source_indexes:
        quantity_pairs = list(zip(template_quantity_source_indexes, quantity_fields))
        semantic_quantity_pairs = list(quantity_pairs)
    else:
        quantity_pairs = _augment_quantity_pairs_from_header(
            matrix=matrix,
            fields=fields,
            mapped_indexes=mapped_indexes,
            quantity_pairs=quantity_pairs,
        )
        semantic_quantity_pairs = list(quantity_pairs)
    if subgrid:
        lower_bound = int(subgrid["quantity_start_col_index"])
        upper_bound = (
            len(matrix[0]) if prefer_observed_header_fill and matrix
            else max(template_quantity_source_indexes) + 1
            if template_quantity_source_indexes
            else len(matrix[0]) if matrix else lower_bound
        )
        if not (strict_template_quantity_alignment and template_quantity_source_indexes):
            header_quantity_pairs = _header_quantity_pairs_from_range(
                matrix=matrix,
                fields=fields,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
            )
            if header_quantity_pairs:
                semantic_quantity_pairs = header_quantity_pairs
        sequential_source_candidates = (
            list(template_quantity_source_indexes)
            if strict_template_quantity_alignment and template_quantity_source_indexes
            else list(range(lower_bound, upper_bound))
        )
        sequential_source_indexes = (
            list(sequential_source_candidates)
            if strict_template_quantity_alignment and template_quantity_source_indexes
            else [
                source_col_index
                for source_col_index in sequential_source_candidates
                if _source_column_has_numeric_signal(
                    matrix=matrix,
                    body_start_row=int(subgrid.get("body_start_row") or 0),
                    source_col_index=source_col_index,
                )
            ]
        )
        sequential_quantity_pairs = [
            (source_col_index, field)
            for source_col_index, field in zip(sequential_source_indexes, quantity_fields)
        ]
        semantic_quantity_fields = {field for _, field in semantic_quantity_pairs}
        semantic_quantity_confident = len(semantic_quantity_fields) >= min(3, len(quantity_fields))
        if semantic_quantity_confident:
            semantic_sources = {source_col_index for source_col_index, _field in semantic_quantity_pairs}
            mapped_by_field = {field: source_col_index for source_col_index, field in semantic_quantity_pairs}
            missing_fields = [field for field in quantity_fields if field not in mapped_by_field]
            numeric_unmapped_sources = [
                source_col_index
                for source_col_index in sequential_source_indexes
                if source_col_index not in semantic_sources
            ]
            quantity_pairs = list(semantic_quantity_pairs)
            for source_col_index, field in zip(numeric_unmapped_sources, missing_fields):
                quantity_pairs.append((source_col_index, field))
                mapped_by_field[field] = source_col_index
            mapped_sources = {source_col_index for source_col_index, _field in quantity_pairs}
            remaining_fields = [field for field in quantity_fields if field not in mapped_by_field]
            if remaining_fields:
                header = _merged_header_for_matrix(matrix, fields)
                header_fill_start = max(semantic_sources) + 1 if semantic_sources else lower_bound
                header_fill_candidates = (
                    list(range(header_fill_start, min(upper_bound, len(header))))
                    if prefer_observed_header_fill
                    else [
                        source_col_index
                        for source_col_index in template_quantity_source_indexes
                        if source_col_index >= header_fill_start
                    ]
                    if template_quantity_source_indexes
                    else list(range(header_fill_start, min(upper_bound, len(header))))
                )
                header_fill_sources = [
                    source_col_index
                    for source_col_index in header_fill_candidates
                    if source_col_index < len(header)
                    and source_col_index not in mapped_sources
                    and bool(str(header[source_col_index] or "").strip())
                ]
                for source_col_index, field in zip(header_fill_sources, remaining_fields):
                    quantity_pairs.append((source_col_index, field))
                    mapped_by_field[field] = source_col_index
                    mapped_sources.add(source_col_index)
        else:
            quantity_pairs = sequential_quantity_pairs
    if not quantity_pairs:
        return None
    quantity_pairs.sort(key=lambda item: item[0])
    mapped_quantity_fields = {field for _, field in quantity_pairs}
    quantity_coverage = len(mapped_quantity_fields) / max(len(quantity_dest_indexes), 1)
    structural_coverage = len(mapped_structural_fields)
    partial_quantity_mapping = quantity_coverage < 1.0
    if subgrid:
        semantic_quantity_fields = {field for _, field in semantic_quantity_pairs}
        if len(semantic_quantity_fields) < min(3, len(quantity_fields)):
            partial_quantity_mapping = True
    if structural_coverage < 2:
        return None
    if partial_quantity_mapping and len(mapped_quantity_fields) <= 0:
        return None
    if partial_quantity_mapping and structural_coverage < 3:
        return None

    grid_metadata = _table_grid_metadata(table_payload)
    if not isinstance(grid_metadata, dict):
        return None

    mapping_value = _mapping_value(quantity_pairs)
    header_tokens = [
        _column_header_text(matrix, source_col_index) or _field_label(field)
        for source_col_index, field in quantity_pairs
    ]
    score = 0.8 if not partial_quantity_mapping else 0.66
    if grid_metadata:
        score += 0.08
    if subgrid:
        score += 0.07
    if structural_coverage >= 3:
        score += 0.05
    score = min(score, 0.99)
    evidence_ref = {
        "page_index": int(table_payload.get("page_index") or 1),
        "table_id": str(table_payload.get("table_id") or "").strip() or None,
        "source_col_indexes": [source_col_index for source_col_index, _ in quantity_pairs],
        "mapped_fields": [field for _, field in quantity_pairs],
    }
    label = " / ".join(header_tokens)
    return {
        "candidate": {
            "candidate_id": _stable_candidate_id(mapping_value),
            "candidate_type": "position_fallback_candidate",
            "value": mapping_value,
            "label": label or mapping_value,
            "score": round(score, 3),
            "reason": "structured_cell_position_mapping",
            "evidence_ref": evidence_ref,
            "decision_source": _POSITION_FALLBACK_SOURCE,
            "auto_selectable": True,
            "partial_quantity_mapping": partial_quantity_mapping,
            "mapped_quantity_fields": [field for _, field in quantity_pairs],
            "expected_quantity_fields": [fields[idx] for idx in sorted(quantity_dest_indexes)],
        },
        "grid_metadata": grid_metadata,
        "quantity_subgrid": subgrid,
        "table_id": evidence_ref["table_id"],
    }


def build_position_fallback_artifacts(
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None,
    *,
    template_id: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not isinstance(template, dict):
        return None
    if _strict_template_semantics_ready(payload) or _position_fallback_already_ready(payload, template=template):
        return None
    row_fields = _row_fields(template)
    if not row_fields:
        return None
    resolved_template_id = (
        str(template_id or template.get("template_id") or "").strip()
        or "facility_template_position_fallback"
    )
    existing_template_resolution = payload.get("template_resolution")
    if isinstance(existing_template_resolution, dict):
        existing_resolved_template_id = str(
            existing_template_resolution.get("resolved_template_id")
            or existing_template_resolution.get("template_id")
            or ""
        ).strip()
        if existing_resolved_template_id and existing_resolved_template_id != resolved_template_id:
            return None

    structured_tables = fax_extractor._collect_structured_tables(payload)
    if not structured_tables:
        return None

    prefer_observed_header_fill = _payload_uses_position_normalized_evidence(payload)

    candidate_rows: list[dict[str, Any]] = []
    for table_payload in structured_tables:
        if not isinstance(table_payload, dict):
            continue
        matrix = _normalize_matrix(table_payload)
        if not matrix:
            continue
        candidate_row = _position_candidate_for_table(
            table_payload=table_payload,
            matrix=matrix,
            template=template,
            prefer_observed_header_fill=prefer_observed_header_fill,
        )
        if not isinstance(candidate_row, dict):
            continue
        candidate_rows.append(candidate_row)

    if not candidate_rows:
        return None

    deduped_candidate_rows: list[dict[str, Any]] = []
    seen_values: set[str] = set()
    for item in sorted(
        candidate_rows,
        key=lambda current: float(current["candidate"].get("score") or 0.0),
        reverse=True,
    ):
        candidate = dict(item["candidate"])
        value = str(candidate.get("value") or "").strip()
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        deduped_candidate_rows.append(item)

    if not deduped_candidate_rows:
        return None

    candidates = [dict(item["candidate"]) for item in deduped_candidate_rows]
    top_candidate_row = deduped_candidate_rows[0]
    top_candidate = candidates[0]
    top_score = _candidate_score(top_candidate)
    second_score = _candidate_score(candidates[1]) if len(candidates) > 1 else None
    requires_user_choice = bool(
        len(candidates) >= 2
        and (
            top_score is None
            or second_score is None
            or (top_score - second_score) < 0.15
            or top_score < 0.85
        )
    )
    template_resolution = {
        "resolved_template_id": resolved_template_id,
        "candidate_template_ids": [resolved_template_id],
        "confidence": float(top_candidate.get("score") or 0.0),
        "blocked": False,
        "blocked_reasons": [],
        "decision_source": _POSITION_FALLBACK_SOURCE,
        "evidence_ref": top_candidate.get("evidence_ref"),
    }
    column_mapping_resolution = {
        "resolved_value": None if requires_user_choice else top_candidate.get("value"),
        "resolved_column_mapping_id": None if requires_user_choice else top_candidate.get("value"),
        "confidence": float(top_candidate.get("score") or 0.0),
        "blocked": bool(requires_user_choice),
        "blocked_reasons": ["column_mapping_choice_required"] if requires_user_choice else [],
        "candidates": candidates,
        "decision_source": _POSITION_FALLBACK_SOURCE,
        "evidence_ref": top_candidate.get("evidence_ref"),
        "requires_user_choice": requires_user_choice,
        "partial_quantity_mapping": bool(top_candidate.get("partial_quantity_mapping")),
        "mapped_quantity_fields": list(top_candidate.get("mapped_quantity_fields") or []),
        "expected_quantity_fields": list(top_candidate.get("expected_quantity_fields") or []),
    }
    top_grid_metadata = (
        top_candidate_row.get("grid_metadata")
        if isinstance(top_candidate_row.get("grid_metadata"), dict)
        else {}
    )
    top_quantity_subgrid = None
    if top_candidate_row.get("quantity_subgrid") is not None:
        spec = top_candidate_row["quantity_subgrid"]
        top_quantity_subgrid = {
            "page_index": int(top_candidate_row["candidate"].get("evidence_ref", {}).get("page_index") or 1),
            "source": _POSITION_FALLBACK_SOURCE,
            "body_start_row": int(spec["body_start_row"]),
            "menu_col_index": int(spec["menu_col_index"]),
            "quantity_start_col_index": int(spec["quantity_start_col_index"]),
            "row_count": int(spec["row_count"]),
            "quantity_col_count": int(spec["quantity_col_count"]),
        }
    return {
        "template_resolution": template_resolution,
        "column_mapping_resolution": column_mapping_resolution,
        "column_mapping_candidates": candidates,
        "table_box": None if requires_user_choice else (top_grid_metadata or {}).get("table_box"),
        "grid_column_edges": None if requires_user_choice else (top_grid_metadata or {}).get("grid_column_edges"),
        "grid_row_edges": None if requires_user_choice else (top_grid_metadata or {}).get("grid_row_edges"),
        "quantity_subgrid_passes": None if requires_user_choice else ([top_quantity_subgrid] if top_quantity_subgrid else None),
    }


def augment_payload_with_position_fallback(
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None,
    *,
    template_id: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return payload
    artifacts = build_position_fallback_artifacts(
        payload,
        template,
        template_id=template_id,
    )
    if not isinstance(artifacts, dict):
        if not _payload_has_position_fallback_resolution(payload):
            return payload
        if _position_fallback_matches_template_contract(payload, template):
            return payload
        augmented = copy.deepcopy(payload)
        augmented["column_mapping_resolution"] = _invalidated_position_fallback_resolution(payload)
        augmented["column_mapping_candidates"] = []
        augmented["quantity_subgrid_passes"] = None
        return augmented

    augmented = copy.deepcopy(payload)
    existing_template_resolution = augmented.get("template_resolution")
    existing_column_mapping_resolution = augmented.get("column_mapping_resolution")
    replace_position_fallback_fields = bool(
        isinstance(existing_column_mapping_resolution, dict)
        and str(existing_column_mapping_resolution.get("decision_source") or "").strip() == _POSITION_FALLBACK_SOURCE
    )
    if (
        not isinstance(existing_template_resolution, dict)
        or (
            isinstance(existing_template_resolution, dict)
            and str(existing_template_resolution.get("decision_source") or "").strip() == _POSITION_FALLBACK_SOURCE
        )
    ):
        augmented["template_resolution"] = copy.deepcopy(artifacts["template_resolution"])
    if not isinstance(existing_column_mapping_resolution, dict) or replace_position_fallback_fields:
        augmented["column_mapping_resolution"] = copy.deepcopy(artifacts["column_mapping_resolution"])
    if not isinstance(augmented.get("column_mapping_candidates"), list) or replace_position_fallback_fields:
        augmented["column_mapping_candidates"] = copy.deepcopy(artifacts["column_mapping_candidates"])
    if (
        (not isinstance(augmented.get("table_box"), list) or replace_position_fallback_fields)
        and isinstance(artifacts.get("table_box"), list)
    ):
        augmented["table_box"] = copy.deepcopy(artifacts["table_box"])
    if (
        (not isinstance(augmented.get("grid_column_edges"), list) or replace_position_fallback_fields)
        and isinstance(artifacts.get("grid_column_edges"), list)
    ):
        augmented["grid_column_edges"] = copy.deepcopy(artifacts["grid_column_edges"])
    if (
        (not isinstance(augmented.get("grid_row_edges"), list) or replace_position_fallback_fields)
        and isinstance(artifacts.get("grid_row_edges"), list)
    ):
        augmented["grid_row_edges"] = copy.deepcopy(artifacts["grid_row_edges"])
    if (
        (not isinstance(augmented.get("quantity_subgrid_passes"), list) or replace_position_fallback_fields)
        and isinstance(artifacts.get("quantity_subgrid_passes"), list)
    ):
        augmented["quantity_subgrid_passes"] = copy.deepcopy(artifacts["quantity_subgrid_passes"])
    return augmented


def candidate_resolution_uses_position_fallback(candidate_resolution: dict[str, Any] | None) -> bool:
    if not isinstance(candidate_resolution, dict):
        return False
    resolutions = candidate_resolution.get("resolutions")
    if not isinstance(resolutions, dict):
        return False
    template = resolutions.get("template")
    column_mapping = resolutions.get("column_mapping")
    if not isinstance(column_mapping, dict):
        return False
    if str(column_mapping.get("decision_source") or "").strip() != _POSITION_FALLBACK_SOURCE:
        return False
    if bool(column_mapping.get("partial_quantity_mapping")):
        return False
    blocked_reasons = {
        str(item or "").strip()
        for item in (column_mapping.get("blocked_reasons") or [])
        if str(item or "").strip()
    }
    resolved_value = str(column_mapping.get("resolved_value") or "").strip() or None
    candidates = _normalize_position_candidates(column_mapping.get("candidates"))
    if _position_fallback_choice_required(
        resolved_value=resolved_value,
        candidates=candidates,
        explicit=bool(column_mapping.get("requires_user_choice")) or "column_mapping_choice_required" in blocked_reasons,
    ):
        return False
    if not resolved_value:
        return False
    if isinstance(template, dict) and template.get("blocked") and not str(template.get("resolved_value") or "").strip():
        return False
    return True


def payload_uses_ready_position_fallback(
    payload: dict[str, Any] | None,
    *,
    template: dict[str, Any] | None = None,
) -> bool:
    if not _position_fallback_already_ready(payload, template=template):
        return False
    resolution = payload.get("template_resolution") if isinstance(payload, dict) else None
    if not isinstance(resolution, dict):
        return True
    resolved_template_id = str(
        resolution.get("resolved_template_id") or resolution.get("template_id") or ""
    ).strip()
    blocked = bool(
        resolution.get("blocked")
        or any(str(item or "").strip() for item in (resolution.get("blocked_reasons") or []))
    )
    if blocked and not resolved_template_id:
        return False
    return True


def payload_uses_partial_position_fallback(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("column_mapping_resolution")
    if not isinstance(resolution, dict):
        return False
    if str(resolution.get("decision_source") or "").strip() != _POSITION_FALLBACK_SOURCE:
        return False
    if not bool(resolution.get("partial_quantity_mapping")):
        return False
    return bool(
        str(resolution.get("resolved_value") or resolution.get("resolved_column_mapping_id") or "").strip()
    )


def payload_position_fallback_requires_choice(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("column_mapping_resolution")
    if not isinstance(resolution, dict):
        return False
    if str(resolution.get("decision_source") or "").strip() != _POSITION_FALLBACK_SOURCE:
        return False
    blocked_reasons = {
        str(item or "").strip()
        for item in (resolution.get("blocked_reasons") or [])
        if str(item or "").strip()
    }
    resolved_value = str(
        resolution.get("resolved_value")
        or resolution.get("resolved_column_mapping_id")
        or ""
    ).strip() or None
    candidates = _normalize_position_candidates(
        resolution.get("candidates")
        if isinstance(resolution.get("candidates"), list)
        else payload.get("column_mapping_candidates")
    )
    return _position_fallback_choice_required(
        resolved_value=resolved_value,
        candidates=candidates,
        explicit=bool(resolution.get("requires_user_choice")) or "column_mapping_choice_required" in blocked_reasons,
    )


def stable_mapping_signature(value: object) -> str:
    return hashlib.sha1(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
