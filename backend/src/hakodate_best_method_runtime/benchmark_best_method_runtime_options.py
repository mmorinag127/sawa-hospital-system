#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

import cv2
from PIL import Image


def _resolve_backend_root() -> Path:
    path = Path(__file__).resolve()
    for candidate in path.parents:
        if (candidate / "src").exists() and (candidate / "requirements.txt").exists():
            return candidate
        if (candidate / "backend" / "src").exists():
            return candidate / "backend"
    return path.parents[2]


BACKEND_ROOT = _resolve_backend_root()
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(BACKEND_ROOT))

import compare_kasuga_digit_preprocess_methods as cmp  # noqa: E402
from src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities import (  # noqa: E402
    ROWS_START,
    _build_truth_for_facility,
    _draw_overlay,
    _get_text_recognizer,
    _restore_display_bboxes,
    _resolve_item_paths,
    _snap_regions_x_to_fax_lines_all_targets,
    build_best_method_for_manifest_item,
)
from src.hakodate_best_method_runtime.run_text_recognizer_corner_noise_trial import (  # noqa: E402
    _expanded_cell_box,
    _foreground_centered,
    _preprocess_corner_component_crop_for_recognizer,
    _safe_int_box,
    build_recognizer_contact_sheet,
    run_text_recognizer_direct,
)
from src.services.hakodate_cell_ocr_batch_service import _build_preprocess_for_ocr  # noqa: E402


DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "best_method_runtime_benchmark"
DEFAULT_CACHED_DRAFT_DIR = SCRIPT_DIR / "best_method_overlay_all_facilities" / "draft_sheets"
DEFAULT_PAGES = "1,6,10,14"


def _load_manifest_items() -> list[tuple[int, dict[str, Any]]]:
    manifest = json.loads(cmp.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    return [(page, item) for page, item in enumerate(items, start=1)]


def _load_cached_draft_sheet(order_id: str) -> dict[str, Any]:
    path = DEFAULT_CACHED_DRAFT_DIR / f"{order_id}_draft_sheet.json"
    if not path.exists():
        raise FileNotFoundError(f"cached draft sheet missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_record_for_runtime(record: dict[str, Any]) -> dict[str, Any]:
    stripped = cmp._strip_record_for_json(record)
    stripped["ocr_text"] = str(stripped.get("raw_text") or stripped.get("pred_digits") or "").strip()
    stripped["ocr_normalized"] = str(stripped.get("pred_digits") or "").strip()
    stripped["ocr_engine"] = str(stripped.get("ocr_engine") or "yomitoku_text_recognizer_corner_noise_trial")
    stripped["source"] = "benchmark_best_method_runtime_options.py"
    return stripped


def _prepare_single_contact_slot(
    args: tuple[
        int,
        dict[str, Any],
        Any,
        Any,
        str,
        int,
        int,
        int,
        int,
        float,
        int,
        int,
    ],
) -> dict[str, Any] | None:
    (
        slot_index,
        region,
        rectified_fax_bgr,
        line_mask,
        mode,
        image_width,
        image_height,
        slot_width,
        slot_height,
        margin_ratio,
        min_ink_area,
        min_ink_height,
    ) = args
    box = region.get("bbox")
    if not isinstance(box, list):
        return None
    if mode == "dynamic":
        px_box = _expanded_cell_box(box, width=image_width, height=image_height, pad_x_px=4, pad_y_px=12)
    elif mode == "corner_cc":
        px_box = _expanded_cell_box(box, width=image_width, height=image_height, pad_x_px=2, pad_y_px=10)
    else:
        px_box = _safe_int_box(box, width=image_width, height=image_height, margin_ratio=margin_ratio)
    if not px_box:
        return None
    x0, y0, x1, y1 = px_box
    crop = rectified_fax_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    crop_line_mask = line_mask[y0:y1, x0:x1] if line_mask is not None else None
    if mode.startswith("corner_cc"):
        crop_image, ink_stats = _preprocess_corner_component_crop_for_recognizer(
            crop,
            cell_box=box,
            crop_box=px_box,
            slot_width=slot_width,
            slot_height=slot_height,
            mode=mode,
        )
    else:
        # The accepted runtime uses corner_cc. This fallback keeps the copied
        # benchmark safe if a caller requests legacy modes.
        crop_image, ink_stats = _foreground_centered(
            cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop,
            out_width=slot_width - 10,
            out_height=slot_height - 10,
        )
        if crop_line_mask is not None:
            ink_stats["line_mask_present"] = True
    slot_col = slot_index % 18
    slot_row = slot_index // 18
    slot_x = slot_col * slot_width
    slot_y = slot_row * slot_height
    paste_x = slot_x + (slot_width - crop_image.width) // 2
    paste_y = slot_y + (slot_height - crop_image.height) // 2
    is_candidate = bool(
        int(ink_stats["ink_area"]) >= min_ink_area and int(ink_stats["bbox_height"]) >= min_ink_height
    )
    polygon = [
        [paste_x, paste_y],
        [paste_x + crop_image.width, paste_y],
        [paste_x + crop_image.width, paste_y + crop_image.height],
        [paste_x, paste_y + crop_image.height],
    ]
    prepared_region = {
        **region,
        "ocr_contact_slot_index": slot_index,
        "ocr_contact_slot": [slot_x, slot_y, slot_x + slot_width, slot_y + slot_height],
        "ocr_contact_crop_box": [paste_x, paste_y, paste_x + crop_image.width, paste_y + crop_image.height],
        "ocr_cell_crop_bbox_px": [x0, y0, x1, y1],
        "recognizer_crop_mode": mode,
        "recognizer_ink_stats": ink_stats,
        "recognizer_candidate": is_candidate,
    }
    return {
        "slot_index": slot_index,
        "crop_image": crop_image,
        "paste_xy": [paste_x, paste_y],
        "polygon": polygon,
        "region": prepared_region,
        "is_candidate": is_candidate,
    }


def build_recognizer_contact_sheet_parallel(
    *,
    rectified_fax_bgr: Any,
    regions: list[dict[str, Any]],
    line_mask: Any | None,
    mode: str,
    workers: int,
    slot_width: int = 132,
    slot_height: int = 84,
    columns: int = 18,
    margin_ratio: float = 0.18,
    min_ink_area: int = 18,
    min_ink_height: int = 8,
) -> tuple[Image.Image, list[dict[str, Any]], list[list[list[int]]], list[dict[str, Any]]]:
    height, width = rectified_fax_bgr.shape[:2]
    row_count = max(1, (len(regions) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * slot_width, row_count * slot_height), "white")
    tasks = [
        (
            slot_index,
            region,
            rectified_fax_bgr,
            line_mask,
            mode,
            width,
            height,
            slot_width,
            slot_height,
            margin_ratio,
            min_ink_area,
            min_ink_height,
        )
        for slot_index, region in enumerate(regions)
    ]
    if workers <= 1:
        results = [_prepare_single_contact_slot(task) for task in tasks]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(_prepare_single_contact_slot, tasks))
    usable_regions: list[dict[str, Any]] = []
    skipped_regions: list[dict[str, Any]] = []
    polygons: list[list[list[int]]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        region = result["region"]
        if result["is_candidate"]:
            sheet.paste(result["crop_image"], tuple(result["paste_xy"]))
            polygons.append(result["polygon"])
            usable_regions.append(region)
            continue
        skipped_regions.append(
            {
                **region,
                "ocr_text": "",
                "ocr_normalized": "",
                "ocr_words": [],
                "ocr_word_count": 0,
                "recognizer_raw_text": "",
                "recognizer_score": 0.0,
                "recognizer_direction": "",
                "recognizer_accepted": False,
                "recognizer_skipped": True,
            }
        )
    return sheet, usable_regions, polygons, skipped_regions


def _run_text_recognizer_records_with_builder(
    *,
    raw_rectified_bgr: Any,
    regions: list[dict[str, Any]],
    truth: dict[str, dict[str, Any]],
    parallel_workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], Image.Image, dict[str, float]]:
    t_contact = time.perf_counter()
    if parallel_workers > 1:
        contact_sheet, usable_regions, polygons, skipped_regions = build_recognizer_contact_sheet_parallel(
            rectified_fax_bgr=raw_rectified_bgr,
            regions=regions,
            line_mask=None,
            mode="corner_cc",
            workers=parallel_workers,
            min_ink_area=18,
            min_ink_height=8,
        )
    else:
        contact_sheet, usable_regions, polygons, skipped_regions = build_recognizer_contact_sheet(
            rectified_fax_bgr=raw_rectified_bgr,
            regions=regions,
            line_mask=None,
            mode="corner_cc",
            min_ink_area=18,
            min_ink_height=8,
        )
    contact_seconds = time.perf_counter() - t_contact
    t_ocr = time.perf_counter()
    recognized_regions, metrics = run_text_recognizer_direct(
        recognizer=_get_text_recognizer(),
        contact_sheet=contact_sheet,
        regions=usable_regions,
        polygons=polygons,
        score_threshold=0.45,
        digit_score_threshold=0.05,
        candidate_digit_score_threshold=0.05,
        enable_context_repair=False,
        sequence_top_k=5,
    )
    ocr_wall_seconds = time.perf_counter() - t_ocr
    merged_regions = sorted(
        recognized_regions + skipped_regions,
        key=lambda item: int(item.get("ocr_contact_slot_index") or 0),
    )
    records: list[dict[str, Any]] = []
    for region in merged_regions:
        sheet_cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        records.append(
            {
                **region,
                "truth": truth.get(sheet_cell) or {},
                "expected_digits": str((truth.get(sheet_cell) or {}).get("expected_digits") or ""),
                "ocr_engine": "yomitoku_text_recognizer_corner_noise_trial",
                "raw_text": str(region.get("recognizer_raw_text") or region.get("ocr_text") or "").strip(),
                "pred_digits": str(region.get("ocr_normalized") or "").strip(),
                "score": float(region.get("recognizer_score") or 0.0),
                "ocr_candidate": bool(region.get("recognizer_candidate")),
                "supervised_label_source": "",
            }
        )
    metrics = {
        **metrics,
        "numeric_eval_cell_count": len(records),
        "pred_nonempty_count": sum(1 for record in records if str(record.get("pred_digits") or "").strip()),
    }
    return records, metrics, contact_sheet, {
        "contact_sheet_seconds": round(contact_seconds, 4),
        "ocr_wall_seconds": round(ocr_wall_seconds, 4),
    }


def build_minimal_variant_for_manifest_item(
    *,
    item: dict[str, Any],
    page_index: int,
    draft_sheet: dict[str, Any],
    output_dir: Path,
    render_width: int,
    parallel_workers: int,
    write_overlay: bool,
    write_records: bool,
) -> dict[str, Any]:
    item = _resolve_item_paths(item)
    facility_code = str(item.get("facility_code") or "")
    order_id = str(item.get("order_id") or "")
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    pre = _build_preprocess_for_ocr(item=item, page=page_index, render_width=render_width)
    timings["preprocess_seconds"] = round(time.perf_counter() - t0, 4)
    eval_regions = [
        region
        for region in pre["target_regions"]
        if ROWS_START <= int(region.get("worksheet_row") or 0)
    ]
    truth, field_by_col = _build_truth_for_facility(draft_sheet, eval_regions)
    t_snap = time.perf_counter()
    snapped_regions, snap_debug = _snap_regions_x_to_fax_lines_all_targets(pre["raw_rectified"], eval_regions)
    timings["snap_seconds"] = round(time.perf_counter() - t_snap, 4)
    records, ocr_metrics, _contact_sheet, ocr_timings = _run_text_recognizer_records_with_builder(
        raw_rectified_bgr=pre["raw_rectified"],
        regions=snapped_regions,
        truth=truth,
        parallel_workers=parallel_workers,
    )
    records = _restore_display_bboxes(records, snapped_regions)
    timings.update(ocr_timings)
    metrics = cmp._evaluate(records)
    metrics.update(
        {
            "ocr_seconds": ocr_metrics.get("ocr_seconds"),
            "raw_prediction_count": ocr_metrics.get("raw_prediction_count"),
            "digit_prediction_count": ocr_metrics.get("digit_prediction_count"),
            "accepted_digit_count": ocr_metrics.get("accepted_digit_count"),
            "candidate_digit_prediction_count": ocr_metrics.get("candidate_digit_prediction_count"),
            "candidate_digit_accept_count": ocr_metrics.get("candidate_digit_accept_count"),
            "digit_score_threshold": ocr_metrics.get("digit_score_threshold"),
            "candidate_digit_score_threshold": ocr_metrics.get("candidate_digit_score_threshold"),
        }
    )
    output_paths: dict[str, str] = {}
    t_overlay = time.perf_counter()
    quad_points = [
        (float(point[0]), float(point[1]))
        for point in (pre.get("rectified_quad_points") or [])
        if isinstance(point, (list, tuple)) and len(point) >= 2
    ]
    if len(quad_points) != 4:
        x0, y0, x1, y1 = [float(v) for v in item["template_bbox"]]
        quad_points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    review_page = _draw_overlay(
        raw_rectified_bgr=pre["raw_rectified"],
        regions=snapped_regions,
        records=records,
        quad_points=quad_points,
        facility_code=facility_code,
        order_id=order_id,
        details=[
            f"page={page_index} fields={field_by_col}",
            "benchmark minimal artifact path",
            f"parallel_workers={parallel_workers}",
        ],
    )
    timings["overlay_seconds"] = round(time.perf_counter() - t_overlay, 4)
    facility_dir = output_dir / f"{page_index:02d}_{facility_code}_{order_id}"
    if write_overlay or write_records:
        facility_dir.mkdir(parents=True, exist_ok=True)
    if write_overlay:
        overlay_path = facility_dir / f"minimal_w{parallel_workers}_overlay.png"
        review_page.save(overlay_path)
        output_paths["overlay"] = str(overlay_path)
    if write_records:
        records_json = [_strip_record_for_runtime(record) for record in records]
        records_path = facility_dir / f"minimal_w{parallel_workers}_records.json"
        regions_path = facility_dir / f"minimal_w{parallel_workers}_ocr_regions.json"
        records_path.write_text(json.dumps(records_json, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        regions_path.write_text(json.dumps(records_json, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        output_paths["records"] = str(records_path)
        output_paths["ocr_regions"] = str(regions_path)
    timings["total_seconds"] = round(time.perf_counter() - t0, 4)
    return {
        "page": page_index,
        "facility_code": facility_code,
        "order_id": order_id,
        "parallel_workers": parallel_workers,
        "timings": timings,
        "metrics": metrics,
        "snap_applied": bool(snap_debug.get("applied")),
        "outputs": output_paths,
    }


def run_current_debug_variant(
    *,
    item: dict[str, Any],
    page_index: int,
    draft_sheet: dict[str, Any],
    output_dir: Path,
    render_width: int,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    summary, _review_page = build_best_method_for_manifest_item(
        item=item,
        page_index=page_index,
        draft_sheet=draft_sheet,
        output_dir=output_dir,
        render_width=render_width,
    )
    total_seconds = time.perf_counter() - t0
    return {
        "page": page_index,
        "facility_code": summary.get("facility_code"),
        "order_id": summary.get("order_id"),
        "timings": {
            "total_seconds": round(total_seconds, 4),
            "ocr_seconds": (summary.get("metrics") or {}).get("ocr_seconds"),
        },
        "metrics": summary.get("metrics") or {},
        "outputs": summary.get("outputs") or {},
    }


def _parse_pages(value: str) -> set[int]:
    result: set[int] = set()
    for chunk in str(value or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        result.add(int(chunk))
    return result


def _summarize_variant(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [float((item.get("timings") or {}).get("total_seconds") or 0.0) for item in results]
    contacts = [float((item.get("timings") or {}).get("contact_sheet_seconds") or 0.0) for item in results if "contact_sheet_seconds" in (item.get("timings") or {})]
    ocrs = [float((item.get("timings") or {}).get("ocr_wall_seconds") or 0.0) for item in results if "ocr_wall_seconds" in (item.get("timings") or {})]
    return {
        "count": len(results),
        "total_mean": round(statistics.mean(totals), 4) if totals else 0.0,
        "total_median": round(statistics.median(totals), 4) if totals else 0.0,
        "contact_mean": round(statistics.mean(contacts), 4) if contacts else None,
        "ocr_wall_mean": round(statistics.mean(ocrs), 4) if ocrs else None,
    }


def _write_markdown_report(summary: dict[str, Any], path: Path) -> None:
    lines = [
        "# Hakodate runtime benchmark",
        "",
        "## Artifact classification",
        "",
        "| Artifact | Needed on order page | Production role | Debug role |",
        "| --- | --- | --- | --- |",
        "| overlay PNG | yes | left-side OCR overlay display | visual inspection |",
        "| OCR regions/records JSON | yes | evidence and sheet projection source | failure analysis |",
        "| assignment preview JSON | yes, via service response | compact UI metrics and overlay status | inspection |",
        "| contact sheet PNG | no | none | OCR crop/debug inspection |",
        "| per-facility overlay PDF | no | none | printable review |",
        "| verbose summary JSON | no | none | benchmark/debug audit |",
        "| sheet_values placeholder JSON | no | none | old trial compatibility only |",
        "",
        "## Variant summary",
        "",
        "| Variant | Runs | Mean total sec | Median total sec | Mean contact sec | Mean OCR wall sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, data in summary["variants"].items():
        lines.append(
            f"| {name} | {data['count']} | {data['total_mean']} | {data['total_median']} | "
            f"{data['contact_mean']} | {data['ocr_wall_mean']} |"
        )
    lines.extend(["", "## Raw output", "", f"- JSON: `{summary['json_path']}`", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", default=DEFAULT_PAGES)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--render-width", type=int, default=1864)
    parser.add_argument("--skip-current-debug", action="store_true")
    parser.add_argument("--no-warmup-recognizer", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_pages = _parse_pages(args.pages)
    selected_items = [
        (page, item)
        for page, item in _load_manifest_items()
        if not selected_pages or page in selected_pages
    ]
    warmup_seconds = 0.0
    if not args.no_warmup_recognizer:
        t_warmup = time.perf_counter()
        _get_text_recognizer()
        warmup_seconds = time.perf_counter() - t_warmup
    all_results: list[dict[str, Any]] = []
    for run_index in range(1, max(1, args.runs) + 1):
        for page_index, item in selected_items:
            order_id = str(item.get("order_id") or "")
            draft_sheet = _load_cached_draft_sheet(order_id)
            if not args.skip_current_debug:
                all_results.append(
                    {
                        "variant": "current_debug",
                        "run": run_index,
                        **run_current_debug_variant(
                            item=item,
                            page_index=page_index,
                            draft_sheet=draft_sheet,
                            output_dir=output_dir / "current_debug",
                            render_width=args.render_width,
                        ),
                    }
                )
            all_results.append(
                {
                    "variant": "minimal_artifacts",
                    "run": run_index,
                    **build_minimal_variant_for_manifest_item(
                        item=item,
                        page_index=page_index,
                        draft_sheet=draft_sheet,
                        output_dir=output_dir / "minimal_artifacts",
                        render_width=args.render_width,
                        parallel_workers=1,
                        write_overlay=True,
                        write_records=True,
                    ),
                }
            )
            all_results.append(
                {
                    "variant": "minimal_parallel_preprocess",
                    "run": run_index,
                    **build_minimal_variant_for_manifest_item(
                        item=item,
                        page_index=page_index,
                        draft_sheet=draft_sheet,
                        output_dir=output_dir / "minimal_parallel_preprocess",
                        render_width=args.render_width,
                        parallel_workers=max(2, args.workers),
                        write_overlay=True,
                        write_records=True,
                    ),
                }
            )
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for result in all_results:
        by_variant.setdefault(str(result.get("variant")), []).append(result)
    summary = {
        "pages": sorted(selected_pages),
        "runs": max(1, args.runs),
        "workers": max(2, args.workers),
        "recognizer_warmup_seconds": round(warmup_seconds, 4),
        "results": all_results,
        "variants": {name: _summarize_variant(items) for name, items in by_variant.items()},
    }
    json_path = output_dir / "benchmark_summary.json"
    summary["json_path"] = str(json_path)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown_report(summary, output_dir / "benchmark_report.md")
    print(json.dumps({"summary": summary["variants"], "json_path": str(json_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
