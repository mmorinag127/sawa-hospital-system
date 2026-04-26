from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image

from app.pdf_render import render_pdf_to_page_images

logger = logging.getLogger(__name__)

@dataclass
class PageResult:
    page_index: int
    markdown_text: str
    ocr_vis: np.ndarray | None
    layout_vis: np.ndarray | None
    figure_paths: list[Path]
    tables: list[dict[str, Any]]


_ANALYZER = None
_ANALYZER_KEY: tuple[str, bool] | None = None


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_box(box: object, *, width: int, height: int) -> list[float] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(item) for item in box]
    except Exception:
        return None
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) > 1.0:
        if width <= 0 or height <= 0:
            return None
        x0 /= float(width)
        x1 /= float(width)
        y0 /= float(height)
        y1 /= float(height)
    x0 = max(0.0, min(1.0, x0))
    x1 = max(0.0, min(1.0, x1))
    y0 = max(0.0, min(1.0, y0))
    y1 = max(0.0, min(1.0, y1))
    return [x0, y0, x1, y1]


def _coerce_jsonable(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _coerce_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return _coerce_jsonable(value.tolist())
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _serialize_results(results: object) -> dict[str, Any] | None:
    if isinstance(results, dict):
        return _coerce_jsonable(results)

    candidate_calls = (
        ("model_dump", {"mode": "python"}),
        ("model_dump", {}),
        ("to_dict", {}),
        ("dict", {}),
        ("to_json", {}),
        ("json", {}),
    )
    for method_name, kwargs in candidate_calls:
        method = getattr(results, method_name, None)
        if not callable(method):
            continue
        try:
            serialized = method(**kwargs)
        except TypeError:
            try:
                serialized = method()
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(serialized, str):
            try:
                import json

                serialized = json.loads(serialized)
            except Exception:
                continue
        if isinstance(serialized, dict):
            return _coerce_jsonable(serialized)
    return None


def _extract_tables(
    analysis: dict[str, Any] | None,
    *,
    page_index: int,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    if not isinstance(analysis, dict):
        return []
    raw_tables = analysis.get("tables")
    if not isinstance(raw_tables, list):
        return []

    tables: list[dict[str, Any]] = []
    for table_offset, raw_table in enumerate(raw_tables, start=1):
        if not isinstance(raw_table, dict):
            continue
        raw_cells = raw_table.get("cells")
        if not isinstance(raw_cells, list):
            raw_cells = []
        row_values = [_coerce_int(cell.get("row")) for cell in raw_cells if isinstance(cell, dict)]
        row_values = [value for value in row_values if value is not None]
        col_values = [_coerce_int(cell.get("col")) for cell in raw_cells if isinstance(cell, dict)]
        col_values = [value for value in col_values if value is not None]
        row_base = min(row_values) if row_values else 0
        col_base = min(col_values) if col_values else 0
        row_count = _coerce_int(raw_table.get("n_row"))
        col_count = _coerce_int(raw_table.get("n_col"))
        if row_count is None:
            row_count = (max(row_values) - row_base + 1) if row_values else 0
        if col_count is None:
            col_count = (max(col_values) - col_base + 1) if col_values else 0
        if row_count <= 0 or col_count <= 0:
            continue

        grid = [["" for _ in range(col_count)] for _ in range(row_count)]
        cells: list[dict[str, Any]] = []
        for raw_cell in raw_cells:
            if not isinstance(raw_cell, dict):
                continue
            row_raw = _coerce_int(raw_cell.get("row"))
            col_raw = _coerce_int(raw_cell.get("col"))
            if row_raw is None or col_raw is None:
                continue
            row_index = row_raw - row_base
            col_index = col_raw - col_base
            if row_index < 0 or col_index < 0:
                continue
            if row_index >= row_count or col_index >= col_count:
                continue
            text = _coerce_text(raw_cell.get("contents")).strip()
            if text and not grid[row_index][col_index]:
                grid[row_index][col_index] = text
            row_span = max(_coerce_int(raw_cell.get("row_span")) or 1, 1)
            col_span = max(_coerce_int(raw_cell.get("col_span")) or 1, 1)
            cells.append(
                {
                    "row_index": row_index,
                    "col_index": col_index,
                    "row_span": row_span,
                    "col_span": col_span,
                    "text": text,
                    "bbox": _normalize_box(raw_cell.get("box"), width=width, height=height),
                }
            )

        tables.append(
            {
                "table_id": f"p{page_index}_t{table_offset}",
                "page_index": page_index,
                "source": "yomitoku_table_structure",
                "bbox": _normalize_box(raw_table.get("box"), width=width, height=height),
                "row_count": row_count,
                "col_count": col_count,
                "rows": grid,
                "cells": cells,
            }
        )
    return tables


def _center_from_box(box: object) -> tuple[float, float] | None:
    if isinstance(box, (list, tuple)) and len(box) == 4:
        try:
            x0, y0, x1, y1 = [float(item) for item in box]
        except Exception:
            return None
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return None


def _center_from_points(points: object) -> tuple[float, float] | None:
    if not isinstance(points, list) or not points:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        except Exception:
            continue
    if not xs or not ys:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _analysis_to_text(analysis: dict[str, Any] | None) -> str:
    if not isinstance(analysis, dict):
        return ""

    paragraphs = analysis.get("paragraphs")
    if isinstance(paragraphs, list):
        lines = []
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict):
                continue
            text = _coerce_text(paragraph.get("contents") or paragraph.get("content")).strip()
            if text:
                lines.append(text)
        if lines:
            return "\n".join(lines)

    words = analysis.get("words")
    if isinstance(words, list):
        items: list[tuple[float, float, str]] = []
        for word in words:
            if not isinstance(word, dict):
                continue
            text = _coerce_text(word.get("content") or word.get("contents")).strip()
            if not text:
                continue
            center = _center_from_box(word.get("box")) or _center_from_points(word.get("points"))
            if center is None:
                continue
            items.append((center[1], center[0], text))
        if items:
            items.sort(key=lambda item: (item[0], item[1]))
            line_threshold = max(8.0, (items[-1][0] - items[0][0]) * 0.03)
            merged_lines: list[list[tuple[float, str]]] = []
            current_line: list[tuple[float, str]] = []
            current_y: float | None = None
            for y_center, x_center, text in items:
                if current_y is None or abs(y_center - current_y) <= line_threshold:
                    current_line.append((x_center, text))
                    current_y = y_center if current_y is None else ((current_y + y_center) / 2.0)
                    continue
                merged_lines.append(current_line)
                current_line = [(x_center, text)]
                current_y = y_center
            if current_line:
                merged_lines.append(current_line)
            text_lines = []
            for line in merged_lines:
                line.sort(key=lambda item: item[0])
                text_lines.append(" ".join(text for _, text in line).strip())
            return "\n".join(item for item in text_lines if item)

    for key in ("contents", "content", "text"):
        value = analysis.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _analysis_to_words(
    analysis: dict[str, Any] | None,
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    if not isinstance(analysis, dict):
        return []
    raw_words = analysis.get("words")
    if not isinstance(raw_words, list):
        return []
    words: list[dict[str, Any]] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, dict):
            continue
        text = _coerce_text(raw_word.get("content") or raw_word.get("contents")).strip()
        if not text:
            continue
        box = _normalize_box(raw_word.get("box"), width=width, height=height)
        if box is not None:
            x_center = (box[0] + box[2]) / 2.0
            y_center = (box[1] + box[3]) / 2.0
        else:
            center = _center_from_points(raw_word.get("points"))
            if center is None or width <= 0 or height <= 0:
                continue
            x_center = center[0] / float(width)
            y_center = center[1] / float(height)
        words.append(
            {
                "text": text,
                "x": max(0.0, min(1.0, float(x_center))),
                "y": max(0.0, min(1.0, float(y_center))),
                "box": box,
            }
        )
    return words


def _ensure_hf_cache() -> None:
    if not os.getenv("HF_HOME"):
        os.environ["HF_HOME"] = "/tmp/hf"


def _get_analyzer(device: str, visualize: bool):
    global _ANALYZER  # noqa: PLW0603
    global _ANALYZER_KEY  # noqa: PLW0603
    key = (device, visualize)
    if _ANALYZER is not None and _ANALYZER_KEY == key:
        return _ANALYZER
    _ensure_hf_cache()
    from yomitoku import DocumentAnalyzer

    configs = {
        "ocr": {
            "text_detector": {},
            "text_recognizer": {},
        },
        "layout_analyzer": {
            "layout_parser": {},
            "table_structure_recognizer": {},
        },
    }
    _ANALYZER = DocumentAnalyzer(configs=configs, device=device, visualize=visualize)
    _ANALYZER_KEY = key
    return _ANALYZER


def prewarm_analyzer(*, device: str, visualize: bool) -> None:
    logger.info("Prewarming yomitoku analyzer device=%s visualize=%s", device, visualize)
    _get_analyzer(device, visualize)


def _save_pdf(images_bgr: Iterable[np.ndarray]) -> bytes:
    pil_images = [Image.fromarray(img[:, :, ::-1]) for img in images_bgr]
    if not pil_images:
        return b""
    first, rest = pil_images[0], pil_images[1:]
    buffer = BytesIO()
    first.save(buffer, format="PDF", save_all=True, append_images=rest)
    return buffer.getvalue()


def run_yomitoku(
    *,
    pdf_bytes: bytes | None,
    dpi: int,
    device: str,
    visualize: bool,
    ignore_line_break: bool,
    no_figure: bool,
    figure_width: int,
    figure_dir: str,
    page_images: Iterable[tuple[int, np.ndarray]] | None = None,
) -> tuple[list[PageResult], bytes | None, bytes | None]:
    if page_images is None:
        if pdf_bytes is None:
            raise ValueError("pdf_bytes is required when page_images is not provided")
        page_iter = render_pdf_to_page_images(pdf_bytes, dpi)
    else:
        page_iter = page_images
    analyzer = _get_analyzer(device, visualize)
    page_results: list[PageResult] = []
    ocr_pdf_images: list[np.ndarray] = []
    layout_pdf_images: list[np.ndarray] = []
    seen_figures: set[str] = set()

    with tempfile.TemporaryDirectory() as workdir:
        workdir_path = Path(workdir)
        for page_idx, img in page_iter:
            results, ocr_vis, layout_vis = analyzer(img)
            analysis = _serialize_results(results)
            height, width = img.shape[:2]
            tables = _extract_tables(
                analysis,
                page_index=page_idx,
                width=width,
                height=height,
            )
            md_path = workdir_path / f"page_{page_idx}.md"
            results.to_markdown(
                str(md_path),
                ignore_line_break=ignore_line_break,
                img=img,
                export_figure=not no_figure,
                figure_width=figure_width,
                figure_dir=figure_dir,
            )
            markdown_text = md_path.read_text(encoding="utf-8")
            figure_paths: list[Path] = []
            if not no_figure:
                fig_dir = workdir_path / figure_dir
                if fig_dir.exists():
                    all_figures = sorted(fig_dir.glob("*.png"))
                    figure_paths = [path for path in all_figures if path.name not in seen_figures]
                    for path in figure_paths:
                        seen_figures.add(path.name)
            page_results.append(
                PageResult(
                    page_index=page_idx,
                    markdown_text=markdown_text,
                    ocr_vis=ocr_vis,
                    layout_vis=layout_vis,
                    figure_paths=figure_paths,
                    tables=tables,
                )
            )
            if visualize:
                if ocr_vis is not None:
                    ocr_pdf_images.append(ocr_vis)
                if layout_vis is not None:
                    layout_pdf_images.append(layout_vis)

    ocr_pdf = _save_pdf(ocr_pdf_images) if visualize else None
    layout_pdf = _save_pdf(layout_pdf_images) if visualize else None
    return page_results, ocr_pdf, layout_pdf


def ocr_image_text(
    image_bgr: np.ndarray,
    *,
    device: str,
) -> str:
    if image_bgr.ndim == 2:
        image_bgr = cv2.cvtColor(image_bgr, cv2.COLOR_GRAY2BGR)
    analyzer = _get_analyzer(device, False)
    results, _ocr_vis, _layout_vis = analyzer(image_bgr)
    analysis = _serialize_results(results)
    return _analysis_to_text(analysis).strip()


def ocr_image_words(
    image_bgr: np.ndarray,
    *,
    device: str,
) -> list[dict[str, Any]]:
    if image_bgr.ndim == 2:
        image_bgr = cv2.cvtColor(image_bgr, cv2.COLOR_GRAY2BGR)
    analyzer = _get_analyzer(device, False)
    results, _ocr_vis, _layout_vis = analyzer(image_bgr)
    analysis = _serialize_results(results)
    height, width = image_bgr.shape[:2]
    return _analysis_to_words(analysis, width=width, height=height)
