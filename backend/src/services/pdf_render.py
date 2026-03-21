from io import BytesIO
from math import sqrt

from PIL import Image


_PDF_RENDER_MIN_DPI = 96


def _cap_render_dpi(
    requested_dpi: int,
    *,
    page_width_points: float,
    page_height_points: float,
    max_pixels: int | None,
) -> int:
    if not max_pixels or max_pixels <= 0:
        return requested_dpi
    if page_width_points <= 0 or page_height_points <= 0:
        return requested_dpi
    width_in = page_width_points / 72.0
    height_in = page_height_points / 72.0
    if width_in <= 0 or height_in <= 0:
        return requested_dpi
    requested_pixels = width_in * height_in * float(requested_dpi) * float(requested_dpi)
    if requested_pixels <= max_pixels:
        return requested_dpi
    capped = int(sqrt(float(max_pixels) / (width_in * height_in)))
    return max(_PDF_RENDER_MIN_DPI, min(requested_dpi, capped))


def _downscale_image_if_needed(image: Image.Image, *, max_pixels: int | None) -> Image.Image:
    if not max_pixels or max_pixels <= 0:
        return image
    width, height = image.size
    current_pixels = width * height
    if current_pixels <= max_pixels:
        return image
    scale = sqrt(float(max_pixels) / float(current_pixels))
    target = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(target, Image.Resampling.LANCZOS)


def render_pdf_to_png_bytes(
    pdf_bytes: bytes,
    dpi: int = 350,
    page: int = 1,
    *,
    max_pixels: int | None = None,
) -> bytes:
    try:
        import pdfplumber
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("pdfplumber is required to render PDFs") from exc

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages")
        page_index = max(page - 1, 0)
        page_obj = pdf.pages[min(page_index, len(pdf.pages) - 1)]
        effective_dpi = _cap_render_dpi(
            dpi,
            page_width_points=float(getattr(page_obj, "width", 0.0) or 0.0),
            page_height_points=float(getattr(page_obj, "height", 0.0) or 0.0),
            max_pixels=max_pixels,
        )
        image = page_obj.to_image(resolution=effective_dpi).original
        image = _downscale_image_if_needed(image, max_pixels=max_pixels)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
