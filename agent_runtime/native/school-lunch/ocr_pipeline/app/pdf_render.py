from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np


def render_pdf_to_png_bytes(pdf_bytes: bytes, dpi: int = 350, page: int = 1) -> bytes:
    with tempfile.TemporaryDirectory() as workdir:
        base = Path(workdir)
        pdf_path = base / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)

        out_prefix = base / "page"
        cmd = [
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-png",
            "-rx",
            str(dpi),
            "-ry",
            str(dpi),
            str(pdf_path),
            str(out_prefix),
        ]
        subprocess.check_call(cmd)

        png_path = base / f"page-{page}.png"
        return png_path.read_bytes()


def render_pdf_to_page_images(pdf_bytes: bytes, dpi: int) -> list[tuple[int, np.ndarray]]:
    import pypdfium2

    scale = dpi / 72.0
    page_images: list[tuple[int, np.ndarray]] = []
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
                    image_bgr = np.array(pil_image.convert("RGB"))[:, :, ::-1]
                    page_images.append((page_index + 1, image_bgr))
                finally:
                    if bitmap is not None:
                        bitmap.close()
                    if hasattr(page, "close"):
                        page.close()
        finally:
            if hasattr(doc, "close"):
                doc.close()
    return page_images
