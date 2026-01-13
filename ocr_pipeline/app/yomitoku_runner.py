from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
import tempfile
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image


@dataclass
class PageResult:
    page_index: int
    markdown_text: str
    ocr_vis: np.ndarray | None
    layout_vis: np.ndarray | None
    figure_paths: list[Path]


_ANALYZER = None
_ANALYZER_KEY: tuple[str, bool] | None = None


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


def _iter_pdf_images(pdf_bytes: bytes, dpi: int) -> Iterable[tuple[int, np.ndarray]]:
    import pypdfium2

    scale = dpi / 72
    with tempfile.TemporaryDirectory() as workdir:
        pdf_path = Path(workdir) / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)
        doc = pypdfium2.PdfDocument(str(pdf_path))
        try:
            for page_index in range(len(doc)):
                page = doc[page_index]
                bitmap = None
                try:
                    bitmap = page.render(scale=scale)
                    pil_image = bitmap.to_pil()
                    img = np.array(pil_image.convert("RGB"))[:, :, ::-1]
                    yield page_index + 1, img
                finally:
                    if bitmap is not None:
                        bitmap.close()
                    if hasattr(page, "close"):
                        page.close()
        finally:
            if hasattr(doc, "close"):
                doc.close()


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
    pdf_bytes: bytes,
    dpi: int,
    device: str,
    visualize: bool,
    ignore_line_break: bool,
    no_figure: bool,
    figure_width: int,
    figure_dir: str,
) -> tuple[list[PageResult], bytes | None, bytes | None]:
    page_iter = _iter_pdf_images(pdf_bytes, dpi)
    analyzer = _get_analyzer(device, visualize)
    page_results: list[PageResult] = []
    ocr_pdf_images: list[np.ndarray] = []
    layout_pdf_images: list[np.ndarray] = []
    seen_figures: set[str] = set()

    with tempfile.TemporaryDirectory() as workdir:
        workdir_path = Path(workdir)
        for page_idx, img in page_iter:
            results, ocr_vis, layout_vis = analyzer(img)
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
