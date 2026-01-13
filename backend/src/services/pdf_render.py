from io import BytesIO


def render_pdf_to_png_bytes(pdf_bytes: bytes, dpi: int = 350, page: int = 1) -> bytes:
    try:
        import pdfplumber
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("pdfplumber is required to render PDFs") from exc

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages")
        page_index = max(page - 1, 0)
        page_obj = pdf.pages[min(page_index, len(pdf.pages) - 1)]
        image = page_obj.to_image(resolution=dpi).original
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
