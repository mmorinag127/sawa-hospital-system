import os
from typing import Optional


def _get_provider(template: dict | None = None) -> str:
    if template:
        provider = template.get("token_ocr_provider")
        if provider:
            return str(provider).lower()
    return os.getenv("OCR_FALLBACK_PROVIDER", "tesseract").lower()


def _ocr_tesseract(crop) -> str:
    import pytesseract

    return pytesseract.image_to_string(
        crop,
        config="--psm 7 -c tessedit_char_whitelist=0123456789",
    )


def ocr_digits(crop, template: dict | None = None) -> Optional[str]:
    provider = _get_provider(template)
    if provider != "tesseract":
        provider = "tesseract"
    return _ocr_tesseract(crop)
