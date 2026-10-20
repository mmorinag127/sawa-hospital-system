from __future__ import annotations

import os
import sys
from pathlib import Path


def _ocr_pipeline_root() -> Path:
    configured = os.getenv("OCR_PIPELINE_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "ocr_pipeline"


def _ensure_ocr_pipeline_path() -> None:
    path = str(_ocr_pipeline_root())
    if path not in sys.path:
        sys.path.insert(0, path)


def _runtime_error(exc: Exception) -> RuntimeError:
    missing_name = getattr(exc, "name", "") or str(exc)
    return RuntimeError(
        "Yomitoku runtime dependency missing: "
        f"{missing_name}. Install the canonical OCR/runtime dependencies for price-ocr-worker."
    )


def load_yomitoku_runner():
    _ensure_ocr_pipeline_path()
    try:
        from app.yomitoku_runner import run_yomitoku  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise _runtime_error(exc) from exc
    except ImportError as exc:
        raise RuntimeError(
            "Yomitoku runtime failed to import. Install the canonical OCR/runtime dependencies for price-ocr-worker."
        ) from exc
    return run_yomitoku


def check_runtime_available() -> dict[str, str]:
    load_yomitoku_runner()
    try:
        from yomitoku import DocumentAnalyzer  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise _runtime_error(exc) from exc
    except ImportError as exc:
        raise RuntimeError(
            "Yomitoku runtime failed to import. Install the canonical OCR/runtime dependencies for price-ocr-worker."
        ) from exc
    return {
        "status": "ready",
        "provider": "yomitoku",
        "analyzer": DocumentAnalyzer.__name__,
    }


def warmup_runtime(device: str = "cpu") -> dict[str, str]:
    check_runtime_available()
    try:
        from yomitoku import DocumentAnalyzer  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise _runtime_error(exc) from exc
    except ImportError as exc:
        raise RuntimeError(
            "Yomitoku runtime failed to import. Install the canonical OCR/runtime dependencies for price-ocr-worker."
        ) from exc

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
    DocumentAnalyzer(configs=configs, device=device, visualize=False)
    return {
        "status": "ready",
        "provider": "yomitoku",
        "device": device,
    }
