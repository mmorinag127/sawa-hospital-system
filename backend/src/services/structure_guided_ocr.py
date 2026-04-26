from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any


def select_primary_table(tables: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not isinstance(tables, list):
        return None
    best: dict[str, Any] | None = None
    best_score = -1.0
    for table in tables:
        if not isinstance(table, dict):
            continue
        try:
            row_count = int(table.get("row_count") or 0)
            col_count = int(table.get("col_count") or 0)
        except Exception:
            continue
        if row_count <= 0 or col_count <= 0:
            continue
        bbox = table.get("bbox") or [0, 0, 0, 0]
        try:
            area = max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))
        except Exception:
            area = 0.0
        score = float(row_count * col_count) + area
        if score > best_score:
            best = table
            best_score = score
    return best


def _line_threshold(words: list[dict[str, Any]]) -> float:
    if len(words) < 2:
        return 0.05
    heights: list[float] = []
    for word in words:
        box = word.get("box")
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            heights.append(max(0.0, float(box[3]) - float(box[1])))
        except Exception:
            continue
    if heights:
        average_height = sum(heights) / len(heights)
        return max(0.03, average_height * 0.75)
    return 0.05


def _words_to_text(words: list[dict[str, Any]]) -> str:
    if not words:
        return ""
    ordered = sorted(
        words,
        key=lambda item: (
            round(float(item.get("y") or 0.0), 4),
            round(float(item.get("x") or 0.0), 4),
        ),
    )
    threshold = _line_threshold(ordered)
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_y: float | None = None
    for word in ordered:
        y_value = float(word.get("y") or 0.0)
        if current_y is None or abs(y_value - current_y) <= threshold:
            current.append(word)
            current_y = y_value if current_y is None else (current_y + y_value) / 2.0
            continue
        lines.append(current)
        current = [word]
        current_y = y_value
    if current:
        lines.append(current)
    rendered: list[str] = []
    for line in lines:
        line.sort(key=lambda item: float(item.get("x") or 0.0))
        rendered.append(" ".join(str(item.get("text") or "").strip() for item in line if str(item.get("text") or "").strip()))
    return "\n".join(token for token in rendered if token).strip()


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bbox_area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _bbox_intersection_area(box_a: list[float], box_b: list[float]) -> float:
    x0 = max(box_a[0], box_b[0])
    y0 = max(box_a[1], box_b[1])
    x1 = min(box_a[2], box_b[2])
    y1 = min(box_a[3], box_b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def normalize_menu_key(value: object) -> str:
    text = _normalize_text(value)
    text = text.replace(" ", "").replace("\n", "")
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("①", "1").replace("②", "2")
    return text


_MENU_TAIL_DIGITS_RE = re.compile(r"^(.*?)(\d{2,3})$")


def _row_values(row: object, *, width: int) -> list[str]:
    if not isinstance(row, list):
        return [""] * width
    values = [_normalize_text(cell) for cell in row[:width]]
    if len(values) < width:
        values.extend([""] * (width - len(values)))
    return values


def repair_menu_tail_quantity_shift(
    *,
    rows: list[list[str]],
    menu_col_index: int = 3,
    quantity_start_col_index: int = 4,
) -> list[list[str]]:
    repaired: list[list[str]] = []
    for raw_row in rows:
        row = [str(cell or "") for cell in raw_row] if isinstance(raw_row, list) else []
        if not row:
            repaired.append(row)
            continue
        if menu_col_index >= len(row) or quantity_start_col_index >= len(row):
            repaired.append(row)
            continue
        menu_text = _normalize_text(row[menu_col_index])
        match = _MENU_TAIL_DIGITS_RE.match(menu_text.replace("\n", " ").strip())
        if not match:
            repaired.append(row)
            continue
        quantity_cells = row[quantity_start_col_index:]
        if not any(_normalize_text(value) for value in quantity_cells):
            repaired.append(row)
            continue
        shifted = list(quantity_cells)
        extracted_digits = match.group(2)
        shifted.insert(0, extracted_digits)
        shifted = shifted[: len(quantity_cells)]
        row[menu_col_index] = match.group(1).rstrip()
        row[quantity_start_col_index:] = shifted
        repaired.append(row)
    return repaired


def _menu_similarity(observed_key: str, canonical_key: str) -> float:
    if not observed_key or not canonical_key:
        return 0.0
    if observed_key == canonical_key:
        return 1.0
    if observed_key in canonical_key or canonical_key in observed_key:
        shorter = min(len(observed_key), len(canonical_key))
        longer = max(len(observed_key), len(canonical_key))
        return shorter / longer if longer else 0.0
    return SequenceMatcher(None, observed_key, canonical_key).ratio()


def _row_match_score(observed: dict[str, Any], canonical: dict[str, Any]) -> float:
    menu_score = _menu_similarity(
        str(observed.get("menu_key") or ""),
        str(canonical.get("menu_key") or ""),
    )
    if menu_score < 0.45:
        return -1.0
    score = menu_score * 10.0
    observed_daypart = _normalize_text(observed.get("daypart"))
    canonical_daypart = _normalize_text(canonical.get("daypart"))
    if observed_daypart and canonical_daypart and observed_daypart == canonical_daypart:
        score += 1.0
    observed_aux = _normalize_text(observed.get("aux"))
    canonical_aux = _normalize_text(canonical.get("aux"))
    if observed_aux and canonical_aux and observed_aux == canonical_aux:
        score += 0.5
    return score


def _align_rows_by_menu_sequence(
    observed_rows: list[dict[str, Any]],
    canonical_rows: list[dict[str, Any]],
) -> dict[int, int]:
    observed_count = len(observed_rows)
    canonical_count = len(canonical_rows)
    dp = [[0.0 for _ in range(canonical_count + 1)] for _ in range(observed_count + 1)]
    decision = [["" for _ in range(canonical_count + 1)] for _ in range(observed_count + 1)]

    for observed_index in range(observed_count - 1, -1, -1):
        for canonical_index in range(canonical_count - 1, -1, -1):
            skip_observed = dp[observed_index + 1][canonical_index]
            skip_canonical = dp[observed_index][canonical_index + 1]
            match_score = _row_match_score(
                observed_rows[observed_index],
                canonical_rows[canonical_index],
            )
            use_match = float("-inf")
            if match_score > 0:
                use_match = match_score + dp[observed_index + 1][canonical_index + 1]

            best_value = skip_observed
            best_decision = "skip_observed"
            if skip_canonical > best_value:
                best_value = skip_canonical
                best_decision = "skip_canonical"
            if use_match > best_value:
                best_value = use_match
                best_decision = "match"

            dp[observed_index][canonical_index] = best_value
            decision[observed_index][canonical_index] = best_decision

    mapping: dict[int, int] = {}
    observed_index = 0
    canonical_index = 0
    while observed_index < observed_count and canonical_index < canonical_count:
        step = decision[observed_index][canonical_index]
        if step == "match":
            mapping[int(observed_rows[observed_index]["row_index"])] = int(
                canonical_rows[canonical_index]["row_index"]
            )
            observed_index += 1
            canonical_index += 1
            continue
        if step == "skip_canonical":
            canonical_index += 1
            continue
        observed_index += 1
    return mapping


def _fill_identity_row_fallbacks(
    mapping: dict[int, int],
    *,
    observed_rows: list[dict[str, Any]],
    canonical_rows: list[dict[str, Any]],
) -> dict[int, int]:
    filled = dict(mapping)
    canonical_by_row = {
        int(row["row_index"]): row
        for row in canonical_rows
        if isinstance(row, dict) and int(row.get("row_index") or 0) >= 0
    }
    used_targets = set(filled.values())
    for observed in observed_rows:
        row_index = int(observed.get("row_index") or 0)
        if row_index in filled:
            continue
        canonical = canonical_by_row.get(row_index)
        if not canonical:
            continue
        if row_index in used_targets:
            continue
        if not str(canonical.get("menu_key") or "").strip():
            continue
        filled[row_index] = row_index
        used_targets.add(row_index)
    return filled


def build_sequence_guided_table(
    *,
    structure_table: dict[str, Any],
    observed_table: dict[str, Any],
    canonical_rows: list[dict[str, Any]],
    header_row_count: int = 2,
    date_col_index: int = 0,
    daypart_col_index: int = 1,
    aux_col_index: int = 2,
    menu_col_index: int = 3,
    quantity_start_col_index: int = 4,
) -> dict[str, Any]:
    row_count = int(structure_table.get("row_count") or observed_table.get("row_count") or 0)
    col_count = int(structure_table.get("col_count") or observed_table.get("col_count") or 0)
    rows = [["" for _ in range(col_count)] for _ in range(row_count)]
    if row_count <= 0 or col_count <= 0:
        return {
            "row_count": row_count,
            "col_count": col_count,
            "rows": rows,
            "cells": [],
            "bbox": structure_table.get("bbox"),
            "source": "sequence_guided_assignment",
        }

    structure_rows = structure_table.get("rows") or []
    observed_rows_source = observed_table.get("rows") or []
    for row_index in range(min(header_row_count, row_count)):
        header_row = []
        if row_index < len(structure_rows) and isinstance(structure_rows[row_index], list):
            header_row = structure_rows[row_index]
        elif row_index < len(observed_rows_source) and isinstance(observed_rows_source[row_index], list):
            header_row = observed_rows_source[row_index]
        normalized_header = _row_values(header_row, width=col_count)
        rows[row_index] = normalized_header

    observed_rows: list[dict[str, Any]] = []
    for row_index, raw_row in enumerate(observed_rows_source):
        if row_index < header_row_count:
            continue
        cells = _row_values(raw_row, width=col_count)
        observed_rows.append(
            {
                "row_index": row_index,
                "date": cells[date_col_index] if date_col_index < col_count else "",
                "daypart": cells[daypart_col_index] if daypart_col_index < col_count else "",
                "aux": cells[aux_col_index] if aux_col_index < col_count else "",
                "menu_name": cells[menu_col_index] if menu_col_index < col_count else "",
                "menu_key": normalize_menu_key(cells[menu_col_index] if menu_col_index < col_count else ""),
                "cells": cells,
            }
        )

    canonical_data_rows = [
        {
            **row,
            "row_index": int(row.get("row_index") or 0),
            "menu_key": normalize_menu_key(row.get("menu_name")),
        }
        for row in canonical_rows
        if isinstance(row, dict) and int(row.get("row_index") or 0) >= header_row_count
    ]
    observed_data_rows = [row for row in observed_rows if str(row.get("menu_key") or "").strip()]
    row_mapping = _align_rows_by_menu_sequence(observed_data_rows, canonical_data_rows)
    row_mapping = _fill_identity_row_fallbacks(
        row_mapping,
        observed_rows=observed_rows,
        canonical_rows=canonical_data_rows,
    )
    canonical_by_row = {int(row["row_index"]): row for row in canonical_data_rows}

    for observed in observed_rows:
        source_row_index = int(observed.get("row_index") or 0)
        target_row_index = row_mapping.get(source_row_index)
        if target_row_index is None or not (0 <= target_row_index < row_count):
            continue
        target = canonical_by_row.get(target_row_index, {})
        target_cells = _row_values(observed.get("cells") or [], width=col_count)
        if date_col_index < col_count:
            target_cells[date_col_index] = _normalize_text(target.get("date"))
        if daypart_col_index < col_count:
            target_cells[daypart_col_index] = _normalize_text(target.get("daypart"))
        if aux_col_index < col_count:
            target_cells[aux_col_index] = _normalize_text(target.get("aux"))
        if menu_col_index < col_count:
            canonical_menu = _normalize_text(target.get("menu_name"))
            target_cells[menu_col_index] = canonical_menu or target_cells[menu_col_index]
        for col_index in range(quantity_start_col_index):
            if col_index >= col_count:
                break
            rows[target_row_index][col_index] = target_cells[col_index]
        for col_index in range(quantity_start_col_index, col_count):
            rows[target_row_index][col_index] = target_cells[col_index]

    assigned_cells: list[dict[str, Any]] = []
    for raw_cell in structure_table.get("cells") or []:
        if not isinstance(raw_cell, dict):
            continue
        row_index = int(raw_cell.get("row_index") or 0)
        col_index = int(raw_cell.get("col_index") or 0)
        text = ""
        if 0 <= row_index < row_count and 0 <= col_index < col_count:
            text = rows[row_index][col_index]
        assigned_cells.append(
            {
                "row_index": row_index,
                "col_index": col_index,
                "row_span": int(raw_cell.get("row_span") or 1),
                "col_span": int(raw_cell.get("col_span") or 1),
                "bbox": raw_cell.get("bbox"),
                "text": text,
            }
        )
    return {
        "row_count": row_count,
        "col_count": col_count,
        "rows": rows,
        "cells": assigned_cells,
        "bbox": structure_table.get("bbox"),
        "source": "sequence_guided_assignment",
        "row_mapping": row_mapping,
    }


def assign_words_to_structure_table(
    *,
    structure_table: dict[str, Any],
    words: list[dict[str, Any]],
) -> dict[str, Any]:
    row_count = int(structure_table.get("row_count") or 0)
    col_count = int(structure_table.get("col_count") or 0)
    rows = [["" for _ in range(col_count)] for _ in range(row_count)]
    assigned_cells: list[dict[str, Any]] = []
    if row_count <= 0 or col_count <= 0:
        return {
            "row_count": row_count,
            "col_count": col_count,
            "rows": rows,
            "cells": assigned_cells,
            "bbox": structure_table.get("bbox"),
            "source": "structure_guided_assignment",
        }

    raw_cells = structure_table.get("cells") or []
    normalized_words = [word for word in words if isinstance(word, dict) and str(word.get("text") or "").strip()]
    for raw_cell in raw_cells:
        if not isinstance(raw_cell, dict):
            continue
        bbox = raw_cell.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = [float(item) for item in bbox]
        except Exception:
            continue
        cell_words = [
            word
            for word in normalized_words
            if x0 <= float(word.get("x") or 0.0) <= x1 and y0 <= float(word.get("y") or 0.0) <= y1
        ]
        text = _words_to_text(cell_words)
        row_index = int(raw_cell.get("row_index") or 0)
        col_index = int(raw_cell.get("col_index") or 0)
        if 0 <= row_index < row_count and 0 <= col_index < col_count:
            rows[row_index][col_index] = text
        assigned_cells.append(
            {
                "row_index": row_index,
                "col_index": col_index,
                "row_span": int(raw_cell.get("row_span") or 1),
                "col_span": int(raw_cell.get("col_span") or 1),
                "bbox": bbox,
                "text": text,
            }
        )
    return {
        "row_count": row_count,
        "col_count": col_count,
        "rows": rows,
        "cells": assigned_cells,
        "bbox": structure_table.get("bbox"),
        "source": "structure_guided_assignment",
    }


def assign_words_to_structure_table_by_overlap(
    *,
    structure_table: dict[str, Any],
    words: list[dict[str, Any]],
) -> dict[str, Any]:
    row_count = int(structure_table.get("row_count") or 0)
    col_count = int(structure_table.get("col_count") or 0)
    rows = [["" for _ in range(col_count)] for _ in range(row_count)]
    assigned_cells: list[dict[str, Any]] = []
    if row_count <= 0 or col_count <= 0:
        return {
            "row_count": row_count,
            "col_count": col_count,
            "rows": rows,
            "cells": assigned_cells,
            "bbox": structure_table.get("bbox"),
            "source": "structure_guided_overlap_assignment",
        }

    raw_cells = [
        raw_cell
        for raw_cell in (structure_table.get("cells") or [])
        if isinstance(raw_cell, dict)
        and isinstance(raw_cell.get("bbox"), list)
        and len(raw_cell.get("bbox") or []) == 4
    ]
    normalized_words = [word for word in words if isinstance(word, dict) and str(word.get("text") or "").strip()]
    words_by_cell: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for word in normalized_words:
        raw_box = word.get("box")
        word_box: list[float] | None = None
        if isinstance(raw_box, list) and len(raw_box) == 4:
            try:
                word_box = [float(item) for item in raw_box]
            except Exception:
                word_box = None
        best_key: tuple[int, int] | None = None
        best_score = 0.0
        if word_box is not None and _bbox_area(word_box) > 0:
            word_area = _bbox_area(word_box)
            for raw_cell in raw_cells:
                cell_box = [float(item) for item in raw_cell["bbox"]]
                overlap = _bbox_intersection_area(word_box, cell_box)
                if overlap <= 0:
                    continue
                score = overlap / word_area
                if score > best_score:
                    best_score = score
                    best_key = (
                        int(raw_cell.get("row_index") or 0),
                        int(raw_cell.get("col_index") or 0),
                    )
        if best_key is None:
            x_value = float(word.get("x") or 0.0)
            y_value = float(word.get("y") or 0.0)
            for raw_cell in raw_cells:
                x0, y0, x1, y1 = [float(item) for item in raw_cell["bbox"]]
                if x0 <= x_value <= x1 and y0 <= y_value <= y1:
                    best_key = (
                        int(raw_cell.get("row_index") or 0),
                        int(raw_cell.get("col_index") or 0),
                    )
                    break
        if best_key is None:
            continue
        words_by_cell.setdefault(best_key, []).append(word)

    for raw_cell in raw_cells:
        bbox = raw_cell["bbox"]
        row_index = int(raw_cell.get("row_index") or 0)
        col_index = int(raw_cell.get("col_index") or 0)
        cell_words = words_by_cell.get((row_index, col_index), [])
        text = _words_to_text(cell_words)
        if 0 <= row_index < row_count and 0 <= col_index < col_count:
            rows[row_index][col_index] = text
        assigned_cells.append(
            {
                "row_index": row_index,
                "col_index": col_index,
                "row_span": int(raw_cell.get("row_span") or 1),
                "col_span": int(raw_cell.get("col_span") or 1),
                "bbox": bbox,
                "text": text,
            }
        )
    return {
        "row_count": row_count,
        "col_count": col_count,
        "rows": rows,
        "cells": assigned_cells,
        "bbox": structure_table.get("bbox"),
        "source": "structure_guided_overlap_assignment",
    }


def table_rows_to_markdown(rows: list[list[str]]) -> str:
    normalized = [list(map(lambda cell: str(cell or ""), row)) for row in rows if isinstance(row, list)]
    if not normalized:
        return ""
    width = max((len(row) for row in normalized), default=0)
    padded = [row + [""] * (width - len(row)) for row in normalized]
    header = padded[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
