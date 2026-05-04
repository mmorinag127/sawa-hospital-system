#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WORKSPACE = Path(__file__).resolve().parents[4]
OUT_DIR = WORKSPACE / "tmp" / "ord4cfa1982_local_fresh_debug" / "first_row_diagnostics"


ARTIFACTS = [
    (
        "previous OK all14 step6: FAC00004 ORDf2b6d176",
        WORKSPACE
        / "tmp"
        / "outer_quad_eval_correct_20260426"
        / "formal_step_review_pipeline_boundary_kept_body_mapping_fixed_20260428"
        / "04_FAC00004_ORDf2b6d176"
        / "step6.png",
    ),
    (
        "current fresh step6: FAC00004 ORD4cfa1982",
        WORKSPACE
        / "tmp"
        / "ord4cfa1982_local_fresh_debug"
        / "regression_by_commit"
        / "5f2f3d8_stepreview"
        / "step_review"
        / "04_FAC00004_ORD4cfa1982"
        / "step6.png",
    ),
    (
        "current fresh best_method overlay: FAC00004 ORD4cfa1982",
        WORKSPACE
        / "tmp"
        / "ord4cfa1982_local_fresh_debug"
        / "fresh_run"
        / "04_FAC00004_ORD4cfa1982"
        / "best_method_overlay.png",
    ),
]


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _crop_top_table(image: Image.Image) -> Image.Image:
    width, height = image.size
    # Keep the whole table width and top body rows. This is intentionally
    # visual-only; no OK/NG decision is made by coordinates here.
    left = 0
    top = max(0, int(height * 0.10))
    right = width
    bottom = min(height, int(height * 0.48))
    return image.crop((left, top, right, bottom))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    font = _font(28)
    small_font = _font(20)
    panels: list[Image.Image] = []
    for title, path in ARTIFACTS:
        if not path.exists():
            raise FileNotFoundError(path)
        image = Image.open(path).convert("RGB")
        crop = _crop_top_table(image)
        max_w = 980
        scale = min(1.0, max_w / max(1, crop.width))
        if scale < 1.0:
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.Resampling.LANCZOS)
        panel = Image.new("RGB", (crop.width + 24, crop.height + 84), "white")
        draw = ImageDraw.Draw(panel)
        draw.text((12, 10), title, fill=(0, 0, 0), font=font)
        draw.text((12, 46), str(path), fill=(80, 80, 80), font=small_font)
        panel.paste(crop, (12, 78))
        panels.append(panel)

    total_w = sum(panel.width for panel in panels)
    total_h = max(panel.height for panel in panels)
    canvas = Image.new("RGB", (total_w, total_h), "white")
    x = 0
    for panel in panels:
        canvas.paste(panel, (x, 0))
        x += panel.width
    out = OUT_DIR / "first_row_artifact_comparison.png"
    canvas.save(out)
    print(out)


if __name__ == "__main__":
    main()
