#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "backend"))

from src.services.hakodate_cell_ocr_batch_service import (  # noqa: E402
    build_all_facility_hakodate_cell_ocr_pdf,
)


DEFAULT_BASE = WORKSPACE / "tmp" / "outer_quad_eval_correct_20260426"
DEFAULT_MANIFEST = DEFAULT_BASE / "step123_no_code_change_20260427" / "manifest.json"
DEFAULT_OUTPUT_DIR = DEFAULT_BASE / "hakodate_cell_ocr_batch_20260428"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--render-width", type=int, default=1864)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    summary = build_all_facility_hakodate_cell_ocr_pdf(
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        render_width=args.render_width,
        device=args.device,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
