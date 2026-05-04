#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WORKSPACE = Path(__file__).resolve().parents[4]
ROOT = WORKSPACE / "tmp" / "ord4cfa1982_local_fresh_debug"
STEP_DIR = ROOT / "regression_by_commit" / "5f2f3d8_stepreview" / "step_review" / "04_FAC00004_ORD4cfa1982"
BEST_METHOD = ROOT / "fresh_run" / "04_FAC00004_ORD4cfa1982" / "best_method_overlay.png"
OUT_DIR = ROOT / "first_row_diagnostics"


ARTIFACTS = [
    ("step1 original + accepted 4 points", STEP_DIR / "step1.png"),
    ("step2 rectified by accepted 4 points", STEP_DIR / "step2.png"),
    ("step3 extracted fax lines", STEP_DIR / "step3.png"),
    ("step4 matched axes after peak alignment", STEP_DIR / "step4.png"),
    ("step5 merge-aware grid", STEP_DIR / "step5.png"),
    ("step6 target cells", STEP_DIR / "step6.png"),
    ("best_method service overlay", BEST_METHOD),
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


def _crop_top_body(image: Image.Image) -> Image.Image:
    width, height = image.size
    top = max(0, int(height * 0.11))
    bottom = min(height, int(height * 0.42))
    return image.crop((0, top, width, bottom))


def _panel(title: str, path: Path) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(path)
    image = Image.open(path).convert("RGB")
    crop = _crop_top_body(image)
    target_w = 760
    scale = target_w / max(1, crop.width)
    crop = crop.resize((target_w, int(crop.height * scale)), Image.Resampling.LANCZOS)
    font = _font(28)
    small = _font(18)
    panel = Image.new("RGB", (crop.width + 24, crop.height + 82), "white")
    draw = ImageDraw.Draw(panel)
    draw.text((12, 8), title, fill=(0, 0, 0), font=font)
    draw.text((12, 44), str(path), fill=(80, 80, 80), font=small)
    panel.paste(crop, (12, 76))
    return panel


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    panels = [_panel(title, path) for title, path in ARTIFACTS]
    cols = 2
    rows = (len(panels) + cols - 1) // cols
    col_w = max(panel.width for panel in panels)
    row_h = max(panel.height for panel in panels)
    canvas = Image.new("RGB", (cols * col_w, rows * row_h), "white")
    for idx, panel in enumerate(panels):
        x = (idx % cols) * col_w
        y = (idx // cols) * row_h
        canvas.paste(panel, (x, y))
    out = OUT_DIR / "ord4cfa1982_step1_to_step6_and_best_method_montage.png"
    canvas.save(out)
    print(out)


if __name__ == "__main__":
    main()
