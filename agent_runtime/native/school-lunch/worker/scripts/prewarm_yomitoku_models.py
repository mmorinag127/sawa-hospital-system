#!/usr/bin/env python3
from __future__ import annotations

import os

os.environ.setdefault("HF_HOME", "/opt/hf")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from yomitoku import DocumentAnalyzer  # noqa: E402


def main() -> None:
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
    DocumentAnalyzer(configs=configs, device="cpu", visualize=False)
    print("yomitoku models cached")


if __name__ == "__main__":
    main()
