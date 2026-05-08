#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from copy import deepcopy

from PIL import Image, ImageDraw, ImageFont


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from src.services import config_service, master_order_form_template_service  # noqa: E402
from src.services.workbook_pdf_renderer import render_workbook_path_to_pdf, render_worksheet_to_image  # noqa: E402


def _safe_name(value: object) -> str:
    text = str(value or "").strip()
    for bad in ("/", "\\", ":", "*", "?", '"', "<", ">", "|", " "):
        text = text.replace(bad, "_")
    return text or "unknown"


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _make_contact_sheet(items: list[dict[str, Any]], output_path: Path) -> None:
    thumbs: list[tuple[dict[str, Any], Image.Image]] = []
    for item in items:
        image = Image.open(item["png"]).convert("RGB")
        max_width = 760
        ratio = max_width / float(image.width)
        thumb = image.resize((max_width, int(round(image.height * ratio))))
        thumbs.append((item, thumb))
    if not thumbs:
        return
    label_h = 58
    gap = 18
    cols = 2
    cell_w = max(thumb.width for _item, thumb in thumbs)
    cell_h = max(thumb.height for _item, thumb in thumbs) + label_h
    rows = (len(thumbs) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell_w + (cols + 1) * gap, rows * cell_h + (rows + 1) * gap), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(22)
    small = _font(18)
    for idx, (item, thumb) in enumerate(thumbs):
        row = idx // cols
        col = idx % cols
        x = gap + col * (cell_w + gap)
        y = gap + row * (cell_h + gap)
        title = f"{idx + 1:02d} {item['facility_id']} {item['facility_name']}"
        draw.text((x, y), title, fill=(0, 0, 0), font=font)
        draw.text((x, y + 28), f"columns={item['generated_column_count']} merged={item['configured_body_merged_ranges']}", fill=(60, 60, 60), font=small)
        canvas.paste(thumb, (x, y + label_h))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--week-value", default="")
    parser.add_argument("--facility-id", action="append", default=[])
    parser.add_argument("--source", choices=("master", "resolved"), default="master")
    args = parser.parse_args()

    output_dir = args.output_dir or (
        BACKEND_ROOT.parent
        / "tmp"
        / f"facility_template_artifacts_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    master = config_service.load_facility_master()
    facility_ids = set(str(item).strip() for item in args.facility_id if str(item).strip())
    rows: list[dict[str, Any]] = []
    for facility in master.get("facilities", []):
        if not isinstance(facility, dict):
            continue
        facility_id = str(facility.get("facility_id") or "").strip()
        if facility_ids and facility_id not in facility_ids:
            continue
        facility_config = (
            config_service.get_facility_config(facility_id)
            if args.source == "resolved"
            else config_service._build_facility_config(  # noqa: SLF001
                facility_id=facility_id,
                facility=deepcopy(facility),
            )
        )
        if not facility_config:
            raise RuntimeError(f"facility config missing: {facility_id}")
        facility_name = str(facility_config.get("facility_name") or facility_config.get("name") or "").strip()
        stem = f"{facility_id}_{_safe_name(facility_name)}"
        xlsx_path = output_dir / f"{stem}.xlsx"
        pdf_path = output_dir / f"{stem}.pdf"
        png_path = output_dir / f"{stem}.png"
        workbook = master_order_form_template_service.build_facility_template_workbook(
            facility_config=facility_config,
            week_value=args.week_value or None,
        )
        workbook.save(xlsx_path)
        render_workbook_path_to_pdf(
            xlsx_path,
            output_path=pdf_path,
            sheet_name=master_order_form_template_service.FACILITY_TEMPLATE_SHEET_NAME,
        )
        image = render_worksheet_to_image(
            workbook[master_order_form_template_service.FACILITY_TEMPLATE_SHEET_NAME],
        ).convert("RGB")
        image.save(png_path)
        schema = workbook["generated_template_schema"]
        schema_values = {str(row[0].value): row[1].value for row in schema.iter_rows(min_row=1, max_col=2)}
        row = {
            "facility_id": facility_id,
            "facility_name": facility_name,
            "xlsx": str(xlsx_path),
            "pdf": str(pdf_path),
            "png": str(png_path),
            "generated_column_count": schema_values.get("generated_column_count"),
            "configured_body_merged_ranges": schema_values.get("configured_body_merged_ranges"),
        }
        rows.append(row)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_path = output_dir / "facility_templates_contact.png"
    _make_contact_sheet(rows, contact_path)
    print(json.dumps({"output_dir": str(output_dir), "summary": str(summary_path), "contact_sheet": str(contact_path), "count": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
