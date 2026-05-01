from typing import Optional


def _get_provider(template: dict | None = None) -> str:
    if template:
        provider = template.get("token_ocr_provider")
        if provider:
            return str(provider).lower()
    return "disabled"


def ocr_digits(crop, template: dict | None = None) -> Optional[str]:
    _ = crop
    provider = _get_provider(template)
    if provider in {"", "none", "disabled"}:
        return None
    if provider == "tesseract":
        raise RuntimeError("tesseract_ocr_removed")
    raise RuntimeError(f"unsupported_ocr_fallback_provider:{provider}")
