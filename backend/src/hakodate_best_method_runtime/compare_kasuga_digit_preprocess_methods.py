#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

def _resolve_backend_root() -> Path:
    path = Path(__file__).resolve()
    for candidate in path.parents:
        if (candidate / "src").exists() and (candidate / "requirements.txt").exists():
            return candidate
        if (candidate / "backend" / "src").exists():
            return candidate / "backend"
    return path.parents[2]


BACKEND_ROOT = _resolve_backend_root()
WORKSPACE = BACKEND_ROOT.parent if (BACKEND_ROOT.parent / "tmp").exists() else BACKEND_ROOT
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from run_text_recognizer_trial import _clean_text_for_number, _load_text_recognizer  # noqa: E402
from src.services.hakodate_cell_ocr_batch_service import (  # noqa: E402
    _bgr_from_pil,
    _build_preprocess_for_ocr,
    _load_overlay_font,
)
from src.services.hakodate_step_review_pipeline_service import _write_pdf_from_pages  # noqa: E402


TARGET_FACILITY_CODE = "FAC00003"
TARGET_ORDER_ID = "ORD9d8f9c2b"
DEFAULT_BASE = WORKSPACE / "tmp" / "outer_quad_eval_correct_20260426"
DEFAULT_MANIFEST = DEFAULT_BASE / "step123_no_code_change_20260427" / "manifest.json"
DEFAULT_OUT = SCRIPT_DIR / "kasuga_digit_preprocess_method_comparison"
STG_API_BASE = "https://web-stg-avlnzjjrca-dt.a.run.app/api"
EVAL_WORKSHEET_ROW_START = 11
EVAL_ROW_COUNT = 40
WORKSHEET_COL_TO_STG_FIELD = {
    5: "qty.regular_2f",
    6: "qty.regular_3f",
    7: "qty.soft_2f",
    8: "qty.soft_3f",
    9: "qty.mixer_2f",
    10: "qty.mixer_3f",
    11: "remarks",
}
FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
@dataclass(frozen=True)
class MethodSpec:
    name: str
    description: str
    crop_mode: str
    pad_x: int = 0
    pad_y: int = 0
    inner_margin_ratio: float = 0.18
    frame_mode: str = "none"
    threshold_mode: str = "gray"
    component_filter: str = "none"
    adaptive_ink_crop: bool = False
    morphology_mask: bool = False
    inpaint: bool = False
    select_digit_cluster: bool = False
    snap_x_to_fax_lines: bool = False
    cluster_pad_x: int = 5
    cluster_pad_y: int = 4
    slot_width: int = 168
    slot_height: int = 104
    columns: int = 14
    center_width_ratio: float = 0.45
    center_height_ratio: float = 0.78


METHODS = [
    MethodSpec(
        name="center_crop_w45_h78_gray",
        description="セル中心だけを切る: 幅45%/高さ78%、枠消しなし。隣接数字混入を避ける。",
        crop_mode="center",
        threshold_mode="gray",
        component_filter="small_only",
        center_width_ratio=0.45,
        center_height_ratio=0.78,
    ),
    MethodSpec(
        name="center_crop_w38_h72_gray",
        description="さらに狭い中心crop: 幅38%/高さ72%、枠消しなし。",
        crop_mode="center",
        threshold_mode="gray",
        component_filter="small_only",
        center_width_ratio=0.38,
        center_height_ratio=0.72,
    ),
    MethodSpec(
        name="center_crop_w52_h86_gray",
        description="広めの中心crop: 幅52%/高さ86%、枠消しなし。",
        crop_mode="center",
        threshold_mode="gray",
        component_filter="small_only",
        center_width_ratio=0.52,
        center_height_ratio=0.86,
    ),
    MethodSpec(
        name="raw_inner_old_style",
        description="旧実験寄り: セル内側を18%縮小して枠を避ける。枠消しなし。",
        crop_mode="inner",
        inner_margin_ratio=0.18,
        threshold_mode="otsu_center",
        component_filter="line_like",
    ),
    MethodSpec(
        name="current_fixed_pad_frame_noise",
        description="現状サービス相当: x=1/y=8固定pad、既知4辺白塗り、小面積ノイズのみ。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=8,
        frame_mode="current",
        threshold_mode="gray",
        component_filter="small_only",
    ),
    MethodSpec(
        name="fax_line_snap_current_frame_noise",
        description="実FAXの縦罫線へ列境界をスナップしてから、現状相当の枠消し・ノイズ除去を行う。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=8,
        frame_mode="current",
        threshold_mode="gray",
        component_filter="small_only",
        snap_x_to_fax_lines=True,
    ),
    MethodSpec(
        name="fax_line_snap_digit_cluster_y8",
        description="実FAX縦罫線スナップ後、数字成分だけを選択してOCRする。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=8,
        frame_mode="current",
        threshold_mode="gray",
        component_filter="border_line_like",
        select_digit_cluster=True,
        snap_x_to_fax_lines=True,
    ),
    MethodSpec(
        name="fax_line_snap_corner_y4",
        description="実FAX縦罫線スナップ後、y=4 crop、既知4辺+4隅mask、線状component除去。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=4,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="border_line_like",
        snap_x_to_fax_lines=True,
    ),
    MethodSpec(
        name="digit_cluster_current_pad_y8",
        description="現状crop+既知4辺白塗り後、セル内の数字らしいインク塊だけを選択してOCRする。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=8,
        frame_mode="current",
        threshold_mode="gray",
        component_filter="border_line_like",
        select_digit_cluster=True,
    ),
    MethodSpec(
        name="digit_cluster_corner_pad_y8",
        description="y=8、既知4辺+4隅mask後、数字らしいインク塊だけを選択してOCRする。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=8,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="border_line_like",
        select_digit_cluster=True,
    ),
    MethodSpec(
        name="digit_cluster_corner_pad_y6",
        description="y=6、既知4辺+4隅mask後、数字らしいインク塊だけを選択してOCRする。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=6,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="border_line_like",
        select_digit_cluster=True,
    ),
    MethodSpec(
        name="digit_cluster_corner_pad_y4",
        description="y=4、既知4辺+4隅mask後、数字らしいインク塊だけを選択してOCRする。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=4,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="border_line_like",
        select_digit_cluster=True,
    ),
    MethodSpec(
        name="current_frame_pad_y4_noise",
        description="現状方式のcropだけ狭める: x=1/y=4固定pad、既知4辺白塗り、小面積ノイズのみ。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=4,
        frame_mode="current",
        threshold_mode="gray",
        component_filter="small_only",
    ),
    MethodSpec(
        name="corner_mask_pad_y4_no_cc",
        description="x=1/y=4、既知4辺+4隅maskのみ。数字成分は形状で消さない。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=4,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="small_only",
    ),
    MethodSpec(
        name="corner_mask_pad_y6_border_cc",
        description="x=1/y=6、既知4辺+4隅mask、枠/角に接触した線状componentだけ除去。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=6,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="border_line_like",
    ),
    MethodSpec(
        name="corner_mask_pad_y4_border_cc",
        description="x=1/y=4、既知4辺+4隅mask、枠/角に接触した線状componentだけ除去。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=4,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="border_line_like",
    ),
    MethodSpec(
        name="tight_pad_corner_mask_cc",
        description="固定padを狭め、既知4辺+4隅を大きめに白塗り、線形状componentを除去。",
        crop_mode="expanded",
        pad_x=0,
        pad_y=4,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="line_like",
    ),
    MethodSpec(
        name="adaptive_ink_corner_border_cc",
        description="border-only CC後、残った数字成分の外接bboxへ再cropして余白を小さくする。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=6,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="border_line_like",
        adaptive_ink_crop=True,
    ),
    MethodSpec(
        name="adaptive_ink_after_corner_mask",
        description="corner mask後、残った数字成分の外接bboxへ再cropして余白を小さくする。",
        crop_mode="expanded",
        pad_x=1,
        pad_y=6,
        frame_mode="corner_overscan",
        threshold_mode="gray",
        component_filter="line_like",
        adaptive_ink_crop=True,
    ),
    MethodSpec(
        name="morphology_border_line_removal",
        description="一般手法: Otsu反転画像から水平/垂直openingで罫線候補を取り、白塗りする。",
        crop_mode="expanded",
        pad_x=2,
        pad_y=6,
        frame_mode="corner_overscan",
        threshold_mode="adaptive_center",
        component_filter="line_like",
        morphology_mask=True,
    ),
    MethodSpec(
        name="inpaint_known_and_morph_mask",
        description="一般手法: 既知枠mask+morphology罫線maskをTELEA inpaintで埋める。",
        crop_mode="expanded",
        pad_x=2,
        pad_y=6,
        frame_mode="corner_overscan",
        threshold_mode="adaptive_center",
        component_filter="line_like",
        morphology_mask=True,
        inpaint=True,
    ),
]


def _load_manifest_item(manifest_path: Path) -> tuple[int, dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    for page, item in enumerate(items, start=1):
        if str(item.get("facility_code")) == TARGET_FACILITY_CODE and str(item.get("order_id")) == TARGET_ORDER_ID:
            return page, item
    raise ValueError(f"target not found: {TARGET_FACILITY_CODE} {TARGET_ORDER_ID}")


def _operator_auth_header_from_gcloud() -> str:
    raw = subprocess.check_output(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            "worker-stg",
            "--project=sawahospitalsystem",
            "--region=asia-northeast2",
            "--format=json",
        ],
        text=True,
    )
    service = json.loads(raw)
    env: dict[str, str] = {}
    for container in service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
        for item in container.get("env", []):
            if "value" in item:
                env[str(item.get("name"))] = str(item.get("value"))
    audience = env.get("GOOGLE_OAUTH_CLIENT_ID")
    if not audience:
        raise ValueError("worker-stg GOOGLE_OAUTH_CLIENT_ID is not available")
    token = subprocess.check_output(
        ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
        text=True,
    ).strip()
    if not token:
        raise ValueError("Google OIDC identity token could not be minted")
    return f"Bearer {token}"


def _fetch_stg_draft_sheet(out_dir: Path) -> dict[str, Any]:
    out_path = out_dir / "stg_ORD9d8f9c2b_draft_sheet.json"
    header = _operator_auth_header_from_gcloud()
    request = urllib.request.Request(
        f"{STG_API_BASE}/orders/{TARGET_ORDER_ID}/draft-sheet",
        headers={"Authorization": header},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read()
    out_path.write_bytes(payload)
    return json.loads(payload)


def _normalize_expected(value: object) -> str:
    text = str(value or "").strip().translate(FULLWIDTH_DIGIT_TRANS)
    return re.sub(r"[^0-9]", "", text)


def _build_ground_truth(stg_draft_sheet: dict[str, Any], regions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    fields = list(stg_draft_sheet.get("fields") or [])
    rows = list(stg_draft_sheet.get("rows") or [])
    field_indexes = {str(field): index for index, field in enumerate(fields)}
    truth: dict[str, dict[str, Any]] = {}
    for region in regions:
        sheet_cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        try:
            worksheet_row = int(region.get("worksheet_row") or 0)
            worksheet_col = int(region.get("worksheet_col") or 0)
        except Exception:
            continue
        row_index = worksheet_row - EVAL_WORKSHEET_ROW_START
        field = WORKSHEET_COL_TO_STG_FIELD.get(worksheet_col)
        if row_index < 0 or row_index >= EVAL_ROW_COUNT or field is None:
            continue
        expected = ""
        if row_index < len(rows) and field in field_indexes:
            row = rows[row_index]
            if isinstance(row, list) and field_indexes[field] < len(row):
                expected = str(row[field_indexes[field]] or "")
        target = (region.get("logical_targets") or [{}])[0]
        truth[sheet_cell] = {
            "sheet_cell": sheet_cell,
            "worksheet_row": worksheet_row,
            "worksheet_col": worksheet_col,
            "row_index": row_index,
            "field": field,
            "expected_text": expected,
            "expected_digits": _normalize_expected(expected),
            "date": target.get("date"),
            "daypart": target.get("daypart"),
            "menu_name": target.get("menu_name"),
            "field_label": region.get("field_label"),
            "eval_numeric": field != "remarks",
        }
    return truth


def _int_box_from_inner(box: list[float], *, width: int, height: int, margin_ratio: float) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = [float(value) for value in box]
    if x1 <= x0 or y1 <= y0:
        return None
    mx = (x1 - x0) * max(0.0, min(margin_ratio, 0.35))
    my = (y1 - y0) * max(0.0, min(margin_ratio, 0.35))
    ix0 = max(0, min(width, int(round(x0 + mx))))
    iy0 = max(0, min(height, int(round(y0 + my))))
    ix1 = max(0, min(width, int(round(x1 - mx))))
    iy1 = max(0, min(height, int(round(y1 - my))))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return ix0, iy0, ix1, iy1


def _int_box_from_expanded(
    box: list[float],
    *,
    width: int,
    height: int,
    pad_x: int,
    pad_y: int,
) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = [float(value) for value in box]
    if x1 <= x0 or y1 <= y0:
        return None
    ix0 = max(0, min(width, int(math.floor(x0 - pad_x))))
    iy0 = max(0, min(height, int(math.floor(y0 - pad_y))))
    ix1 = max(0, min(width, int(math.ceil(x1 + pad_x))))
    iy1 = max(0, min(height, int(math.ceil(y1 + pad_y))))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return ix0, iy0, ix1, iy1


def _int_box_from_center(
    box: list[float],
    *,
    width: int,
    height: int,
    width_ratio: float,
    height_ratio: float,
) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = [float(value) for value in box]
    if x1 <= x0 or y1 <= y0:
        return None
    cell_w = x1 - x0
    cell_h = y1 - y0
    crop_w = max(18.0, cell_w * max(0.2, min(width_ratio, 0.95)))
    crop_h = max(18.0, cell_h * max(0.2, min(height_ratio, 0.98)))
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    ix0 = max(0, min(width, int(round(cx - crop_w / 2.0))))
    iy0 = max(0, min(height, int(round(cy - crop_h / 2.0))))
    ix1 = max(0, min(width, int(round(cx + crop_w / 2.0))))
    iy1 = max(0, min(height, int(round(cy + crop_h / 2.0))))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return ix0, iy0, ix1, iy1


def _known_frame_mask(
    shape: tuple[int, int],
    *,
    cell_box: list[float],
    crop_box: tuple[int, int, int, int],
    corner_overscan: bool,
) -> np.ndarray:
    height, width = shape
    crop_x0, crop_y0, _crop_x1, _crop_y1 = crop_box
    x0, y0, x1, y1 = [float(value) for value in cell_box]
    rel_x0 = int(round(x0 - crop_x0))
    rel_y0 = int(round(y0 - crop_y0))
    rel_x1 = int(round(x1 - crop_x0))
    rel_y1 = int(round(y1 - crop_y0))
    thickness = max(4, int(round(min(max(1, x1 - x0), max(1, y1 - y0)) * 0.075)))
    if corner_overscan:
        thickness = max(thickness, 6)
    mask = np.zeros((height, width), dtype=np.uint8)

    def fill_rect(rx0: int, ry0: int, rx1: int, ry1: int) -> None:
        ax0 = max(0, min(width, rx0))
        ay0 = max(0, min(height, ry0))
        ax1 = max(0, min(width, rx1))
        ay1 = max(0, min(height, ry1))
        if ax1 > ax0 and ay1 > ay0:
            mask[ay0:ay1, ax0:ax1] = 255

    fill_rect(rel_x0 - thickness, rel_y0 - thickness, rel_x1 + thickness, rel_y0 + thickness)
    fill_rect(rel_x0 - thickness, rel_y1 - thickness, rel_x1 + thickness, rel_y1 + thickness)
    fill_rect(rel_x0 - thickness, rel_y0 - thickness, rel_x0 + thickness, rel_y1 + thickness)
    fill_rect(rel_x1 - thickness, rel_y0 - thickness, rel_x1 + thickness, rel_y1 + thickness)
    if corner_overscan:
        corner = max(thickness * 3, 12)
        for cx, cy in [(rel_x0, rel_y0), (rel_x1, rel_y0), (rel_x0, rel_y1), (rel_x1, rel_y1)]:
            fill_rect(cx - corner, cy - corner, cx + corner + 1, cy + corner + 1)
    return mask


def _threshold_ink(gray: np.ndarray, mode: str = "otsu") -> np.ndarray:
    if gray.size == 0:
        return np.zeros_like(gray)
    if mode == "adaptive":
        block = max(11, (min(gray.shape[:2]) // 2) * 2 + 1)
        block = min(block, 41)
        binary = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block,
            8,
        )
    else:
        _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return 255 - binary


def _morphology_line_mask(gray: np.ndarray) -> np.ndarray:
    ink = _threshold_ink(gray, "otsu")
    height, width = ink.shape[:2]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(8, width // 3), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, height // 3)))
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, horizontal_kernel, iterations=1)
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
    line_mask = cv2.bitwise_or(horizontal, vertical)
    border_roi = np.zeros_like(line_mask)
    edge = max(5, int(round(min(width, height) * 0.22)))
    border_roi[:edge, :] = 255
    border_roi[-edge:, :] = 255
    border_roi[:, :edge] = 255
    border_roi[:, -edge:] = 255
    return cv2.bitwise_and(line_mask, border_roi)


def _component_cleanup(gray: np.ndarray, mode: str) -> tuple[np.ndarray, dict[str, Any]]:
    if mode == "none":
        ink = _threshold_ink(gray, "otsu")
        return gray, _ink_stats(ink)
    ink = _threshold_ink(gray, "otsu")
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    height, width = ink.shape[:2]
    cleaned = gray.copy()
    kept = np.zeros_like(ink)
    removed_count = 0
    for label in range(1, num_labels):
        x, y, w, h, area = [int(v) for v in stats[label]]
        remove = area < 5
        if mode in {"line_like", "border_line_like"}:
            touches_edge = x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1
            horizontal_line = w >= max(10, int(width * 0.42)) and h <= max(4, int(height * 0.16))
            vertical_line = h >= max(10, int(height * 0.42)) and w <= max(4, int(width * 0.12))
            corner_noise = touches_edge and area <= max(70, int(width * height * 0.025)) and min(w, h) <= 8
            if mode == "border_line_like":
                remove = remove or ((horizontal_line or vertical_line or corner_noise) and touches_edge)
            else:
                remove = remove or horizontal_line or vertical_line or corner_noise
        if remove:
            cleaned[labels == label] = 255
            removed_count += 1
        else:
            kept[labels == label] = 255
    out_stats = _ink_stats(kept)
    out_stats["removed_component_count"] = removed_count
    out_stats["component_count"] = max(0, num_labels - 1)
    return cleaned, out_stats


def _ink_stats(ink: np.ndarray) -> dict[str, Any]:
    points = cv2.findNonZero(ink)
    if points is None:
        return {"ink_area": 0, "bbox": None, "bbox_width": 0, "bbox_height": 0}
    x, y, w, h = cv2.boundingRect(points)
    return {
        "ink_area": int(np.count_nonzero(ink)),
        "bbox": [int(x), int(y), int(w), int(h)],
        "bbox_width": int(w),
        "bbox_height": int(h),
    }


def _crop_to_ink(gray: np.ndarray, *, pad_x: int = 4, pad_y: int = 3) -> np.ndarray:
    ink = _threshold_ink(gray, "otsu")
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    useful = np.zeros_like(ink)
    height, width = ink.shape[:2]
    for label in range(1, num_labels):
        x, y, w, h, area = [int(v) for v in stats[label]]
        if area < 5:
            continue
        if w >= max(10, int(width * 0.55)) and h <= max(4, int(height * 0.14)):
            continue
        if h >= max(10, int(height * 0.55)) and w <= max(4, int(width * 0.12)):
            continue
        useful[labels == label] = 255
    points = cv2.findNonZero(useful)
    if points is None:
        return gray
    x, y, w, h = cv2.boundingRect(points)
    x0 = max(0, x - pad_x)
    y0 = max(0, y - pad_y)
    x1 = min(width, x + w + pad_x)
    y1 = min(height, y + h + pad_y)
    if x1 <= x0 or y1 <= y0:
        return gray
    return gray[y0:y1, x0:x1]


def _digit_component_candidates(gray: np.ndarray) -> list[dict[str, Any]]:
    ink = _threshold_ink(gray, "otsu")
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    height, width = ink.shape[:2]
    candidates: list[dict[str, Any]] = []
    for label in range(1, num_labels):
        x, y, w, h, area = [int(v) for v in stats[label]]
        if area < 5:
            continue
        touches_edge = x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1
        horizontal_line = w >= max(10, int(width * 0.45)) and h <= max(4, int(height * 0.18))
        vertical_line = h >= max(10, int(height * 0.48)) and w <= max(4, int(width * 0.14))
        corner_noise = touches_edge and area <= max(80, int(width * height * 0.03)) and min(w, h) <= 9
        if (horizontal_line or vertical_line or corner_noise) and touches_edge:
            continue
        if h < 5 or w < 2:
            continue
        if w > int(width * 0.72) and h < int(height * 0.36):
            continue
        cx, cy = [float(v) for v in centroids[label]]
        candidates.append(
            {
                "label": label,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "area": area,
                "cx": cx,
                "cy": cy,
                "touches_edge": touches_edge,
            }
        )
    return candidates


def _merge_digit_components(components: list[dict[str, Any]], *, width: int, height: int) -> list[list[dict[str, Any]]]:
    if not components:
        return []
    ordered = sorted(components, key=lambda item: (item["x"], item["y"]))
    groups: list[list[dict[str, Any]]] = []
    for component in ordered:
        if not groups:
            groups.append([component])
            continue
        last_group = groups[-1]
        gx0 = min(int(item["x"]) for item in last_group)
        gy0 = min(int(item["y"]) for item in last_group)
        gx1 = max(int(item["x"]) + int(item["w"]) for item in last_group)
        gy1 = max(int(item["y"]) + int(item["h"]) for item in last_group)
        gap = int(component["x"]) - gx1
        overlap = max(0, min(gy1, int(component["y"]) + int(component["h"])) - max(gy0, int(component["y"])))
        min_h = max(1, min(gy1 - gy0, int(component["h"])))
        center_gap_y = abs(float(component["cy"]) - ((gy0 + gy1) / 2.0))
        merge_gap = max(7, int(round(width * 0.075)))
        if gap <= merge_gap and (overlap / min_h >= 0.18 or center_gap_y <= max(10, height * 0.22)):
            last_group.append(component)
        else:
            groups.append([component])
    return groups


def _group_bbox(group: list[dict[str, Any]]) -> tuple[int, int, int, int, int]:
    x0 = min(int(item["x"]) for item in group)
    y0 = min(int(item["y"]) for item in group)
    x1 = max(int(item["x"]) + int(item["w"]) for item in group)
    y1 = max(int(item["y"]) + int(item["h"]) for item in group)
    area = sum(int(item["area"]) for item in group)
    return x0, y0, x1, y1, area


def _crop_to_digit_cluster(gray: np.ndarray, *, pad_x: int = 5, pad_y: int = 4) -> tuple[np.ndarray, dict[str, Any]]:
    height, width = gray.shape[:2]
    components = _digit_component_candidates(gray)
    groups = _merge_digit_components(components, width=width, height=height)
    if not groups:
        return gray, {"selected": False, "candidate_group_count": 0, "reason": "no_digit_component"}

    center_x = width / 2.0
    center_y = height / 2.0
    scored: list[tuple[float, list[dict[str, Any]], dict[str, Any]]] = []
    for group in groups:
        x0, y0, x1, y1, area = _group_bbox(group)
        bw = x1 - x0
        bh = y1 - y0
        if bw <= 0 or bh <= 0:
            continue
        group_cx = (x0 + x1) / 2.0
        group_cy = (y0 + y1) / 2.0
        touches_edge = x0 <= 1 or y0 <= 1 or x1 >= width - 1 or y1 >= height - 1
        edge_penalty = 0.6 if touches_edge else 0.0
        # Handwritten digits in these cells are expected near the cell center, but not exactly centered.
        # Penalize edge-side blobs strongly enough to reject neighboring-cell leakage.
        dx = abs(group_cx - center_x) / max(1.0, width / 2.0)
        dy = abs(group_cy - center_y) / max(1.0, height / 2.0)
        size_penalty = 0.0
        if bh < max(7, int(height * 0.16)):
            size_penalty += 0.5
        if bw > width * 0.62:
            size_penalty += 0.35
        if bh > height * 0.92:
            size_penalty += 0.35
        area_bonus = -min(0.25, area / max(1.0, width * height) * 5.0)
        score = dx + 0.45 * dy + edge_penalty + size_penalty + area_bonus
        scored.append(
            (
                score,
                group,
                {
                    "bbox": [int(x0), int(y0), int(x1), int(y1)],
                    "area": int(area),
                    "component_count": len(group),
                    "touches_edge": bool(touches_edge),
                    "score": round(float(score), 4),
                },
            )
        )
    if not scored:
        return gray, {"selected": False, "candidate_group_count": len(groups), "reason": "no_scored_group"}
    _score, selected_group, selected_meta = min(scored, key=lambda item: item[0])
    x0, y0, x1, y1, _area = _group_bbox(selected_group)
    cx0 = max(0, x0 - pad_x)
    cy0 = max(0, y0 - pad_y)
    cx1 = min(width, x1 + pad_x)
    cy1 = min(height, y1 + pad_y)
    if cx1 <= cx0 or cy1 <= cy0:
        return gray, {"selected": False, "candidate_group_count": len(groups), "reason": "invalid_selected_crop"}
    selected_meta.update(
        {
            "selected": True,
            "candidate_group_count": len(groups),
            "crop_box": [int(cx0), int(cy0), int(cx1), int(cy1)],
        }
    )
    return gray[cy0:cy1, cx0:cx1], selected_meta


def _prepare_final_image(gray: np.ndarray, method: MethodSpec) -> tuple[Image.Image, dict[str, Any]]:
    work = gray.copy()
    cluster_stats: dict[str, Any] | None = None
    if method.select_digit_cluster:
        work, cluster_stats = _crop_to_digit_cluster(work, pad_x=method.cluster_pad_x, pad_y=method.cluster_pad_y)
    if method.adaptive_ink_crop:
        work = _crop_to_ink(work)
    if method.threshold_mode == "otsu_center":
        ink = _threshold_ink(work, "otsu")
        binary = 255 - ink
        stats = _ink_stats(ink)
        return Image.fromarray(binary).convert("RGB"), stats
    if method.threshold_mode == "adaptive_center":
        ink = _threshold_ink(work, "adaptive")
        binary = 255 - ink
        stats = _ink_stats(ink)
        return Image.fromarray(binary).convert("RGB"), stats
    ink = _threshold_ink(work, "otsu")
    stats = _ink_stats(ink)
    if cluster_stats is not None:
        stats["digit_cluster"] = cluster_stats
    return Image.fromarray(work).convert("RGB"), stats


def _preprocess_cell(
    rectified: np.ndarray,
    region: dict[str, Any],
    method: MethodSpec,
) -> tuple[Image.Image, dict[str, Any]]:
    height, width = rectified.shape[:2]
    box = region.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("region bbox missing")
    if method.crop_mode == "inner":
        crop_box = _int_box_from_inner(box, width=width, height=height, margin_ratio=method.inner_margin_ratio)
    elif method.crop_mode == "center":
        crop_box = _int_box_from_center(
            box,
            width=width,
            height=height,
            width_ratio=method.center_width_ratio,
            height_ratio=method.center_height_ratio,
        )
    else:
        crop_box = _int_box_from_expanded(box, width=width, height=height, pad_x=method.pad_x, pad_y=method.pad_y)
    if crop_box is None:
        raise ValueError("invalid crop box")
    x0, y0, x1, y1 = crop_box
    crop = rectified[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()
    if method.frame_mode in {"current", "corner_overscan"}:
        frame_mask = _known_frame_mask(
            gray.shape[:2],
            cell_box=box,
            crop_box=crop_box,
            corner_overscan=method.frame_mode == "corner_overscan",
        )
        if method.inpaint:
            mask = frame_mask.copy()
        else:
            gray[frame_mask > 0] = 255
            mask = frame_mask
    else:
        mask = np.zeros(gray.shape[:2], dtype=np.uint8)
    if method.morphology_mask:
        morph_mask = _morphology_line_mask(gray)
        mask = cv2.bitwise_or(mask, morph_mask)
        if method.inpaint:
            gray = cv2.inpaint(gray, mask, 3, cv2.INPAINT_TELEA)
        else:
            gray[morph_mask > 0] = 255
    if not method.inpaint:
        gray[mask > 0] = 255
    gray, component_stats = _component_cleanup(gray, method.component_filter)
    final_image, ink_stats = _prepare_final_image(gray, method)
    return final_image, {
        **ink_stats,
        "component_stats": component_stats,
        "crop_box": [int(x0), int(y0), int(x1), int(y1)],
    }


def _extract_vertical_line_peaks(
    rectified: np.ndarray,
    regions: list[dict[str, Any]],
) -> list[tuple[int, float]]:
    if not regions:
        return []
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY) if rectified.ndim == 3 else rectified.copy()
    x0 = max(0, int(min(float(r["bbox"][0]) for r in regions)) - 80)
    x1 = min(gray.shape[1], int(max(float(r["bbox"][2]) for r in regions)) + 50)
    y0 = max(0, int(min(float(r["bbox"][1]) for r in regions)) - 40)
    y1 = min(gray.shape[0], int(max(float(r["bbox"][3]) for r in regions)) + 40)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return []
    _threshold, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 45))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
    vertical = cv2.dilate(vertical, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5)), iterations=1)
    projection = vertical.sum(axis=0).astype(np.float32) / 255.0
    if projection.size == 0:
        return []
    smooth = np.convolve(projection, np.ones(7, dtype=np.float32) / 7.0, mode="same")
    threshold = max(20.0, float(np.percentile(smooth, 95)) * 0.35)
    segments: list[tuple[int, int]] = []
    in_segment = False
    start = 0
    for index, value in enumerate(smooth):
        if value >= threshold and not in_segment:
            start = index
            in_segment = True
        elif (value < threshold or index == len(smooth) - 1) and in_segment:
            end = index if value < threshold else index + 1
            if end - start >= 2:
                segments.append((start, end))
            in_segment = False
    peaks: list[tuple[int, float]] = []
    for start, end in segments:
        segment = smooth[start:end]
        if segment.size == 0:
            continue
        local = int(np.argmax(segment))
        peaks.append((x0 + start + local, float(segment[local])))
    merged: list[tuple[int, float]] = []
    for peak_x, score in peaks:
        if not merged or peak_x - merged[-1][0] > 10:
            merged.append((peak_x, score))
        elif score > merged[-1][1]:
            merged[-1] = (peak_x, score)
    return merged


def _snap_regions_x_to_fax_lines(rectified: np.ndarray, regions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    quantity_regions = [
        region
        for region in regions
        if isinstance(region.get("bbox"), list)
        and len(region["bbox"]) == 4
        and 5 <= int(region.get("worksheet_col") or 0) <= 11
    ]
    if not quantity_regions:
        return regions, {"applied": False, "reason": "no_quantity_regions"}
    original_boundaries = sorted(
        {
            int(round(float(region["bbox"][0])))
            for region in quantity_regions
        }
        | {
            int(round(float(region["bbox"][2])))
            for region in quantity_regions
        }
    )
    if len(original_boundaries) < 2:
        return regions, {"applied": False, "reason": "insufficient_boundaries"}
    peaks = _extract_vertical_line_peaks(rectified, quantity_regions)
    if not peaks:
        return regions, {"applied": False, "reason": "no_fax_line_peaks", "original_boundaries": original_boundaries}
    snapped_boundaries: list[int] = []
    assignments: list[dict[str, Any]] = []
    for index, boundary in enumerate(original_boundaries):
        tolerance = 55 if 0 < index < len(original_boundaries) - 1 else 35
        candidates = [(x, score) for x, score in peaks if abs(x - boundary) <= tolerance]
        if candidates:
            # Use the strongest actual FAX line near the template boundary; nearest can pick a stale template line.
            chosen_x, chosen_score = max(candidates, key=lambda item: item[1])
        else:
            chosen_x, chosen_score = boundary, 0.0
        snapped_boundaries.append(int(chosen_x))
        assignments.append(
            {
                "template_x": int(boundary),
                "snapped_x": int(chosen_x),
                "delta": int(chosen_x - boundary),
                "score": round(float(chosen_score), 2),
                "candidate_count": len(candidates),
            }
        )
    # Keep boundaries monotonic and avoid zero-width cells.
    for index in range(1, len(snapped_boundaries)):
        min_gap = 35
        if snapped_boundaries[index] <= snapped_boundaries[index - 1] + min_gap:
            snapped_boundaries[index] = original_boundaries[index]
            assignments[index]["snapped_x"] = int(original_boundaries[index])
            assignments[index]["delta"] = 0
            assignments[index]["score"] = 0.0
            assignments[index]["fallback_reason"] = "non_monotonic_after_snap"
    boundary_by_original = {original: snapped for original, snapped in zip(original_boundaries, snapped_boundaries)}
    snapped_regions: list[dict[str, Any]] = []
    for region in regions:
        copied = dict(region)
        box = copied.get("bbox")
        if isinstance(box, list) and len(box) == 4 and 5 <= int(copied.get("worksheet_col") or 0) <= 11:
            left = int(round(float(box[0])))
            right = int(round(float(box[2])))
            new_box = list(box)
            new_box[0] = float(boundary_by_original.get(left, left))
            new_box[2] = float(boundary_by_original.get(right, right))
            if new_box[2] > new_box[0]:
                copied["bbox"] = new_box
                copied["x_snap"] = {
                    "original_left": left,
                    "original_right": right,
                    "snapped_left": int(new_box[0]),
                    "snapped_right": int(new_box[2]),
                }
        snapped_regions.append(copied)
    return snapped_regions, {
        "applied": True,
        "original_boundaries": original_boundaries,
        "snapped_boundaries": snapped_boundaries,
        "assignments": assignments,
        "peaks": [{"x": int(x), "score": round(float(score), 2)} for x, score in peaks],
    }


def _fit_for_slot(image: Image.Image, *, width: int, height: int) -> tuple[Image.Image, list[int]]:
    canvas = Image.new("RGB", (width, height), "white")
    work = image.convert("RGB")
    work.thumbnail((width - 10, height - 10), Image.Resampling.NEAREST)
    x = (width - work.width) // 2
    y = (height - work.height) // 2
    canvas.paste(work, (x, y))
    return canvas, [x, y, x + work.width, y + work.height]


def _build_contact_sheet(
    rectified: np.ndarray,
    regions: list[dict[str, Any]],
    truth: dict[str, dict[str, Any]],
    method: MethodSpec,
    out_dir: Path,
) -> tuple[Image.Image, list[dict[str, Any]], list[list[list[int]]]]:
    row_count = max(1, math.ceil(len(regions) / method.columns))
    sheet = Image.new("RGB", (method.columns * method.slot_width, row_count * method.slot_height), "white")
    prepared: list[dict[str, Any]] = []
    polygons: list[list[list[int]]] = []
    for index, region in enumerate(regions):
        cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        processed, stats = _preprocess_cell(rectified, region, method)
        slot_col = index % method.columns
        slot_row = index // method.columns
        slot_x = slot_col * method.slot_width
        slot_y = slot_row * method.slot_height
        fitted, crop_box = _fit_for_slot(processed, width=method.slot_width, height=method.slot_height)
        sheet.paste(fitted, (slot_x, slot_y))
        paste_box = [
            slot_x + crop_box[0],
            slot_y + crop_box[1],
            slot_x + crop_box[2],
            slot_y + crop_box[3],
        ]
        ink_area = int(stats.get("ink_area") or 0)
        ink_height = int(stats.get("bbox_height") or 0)
        expected_digits = truth.get(cell, {}).get("expected_digits", "")
        candidate = ink_area >= 6 and ink_height >= 4
        prepared_region = {
            **region,
            "truth": truth.get(cell, {}),
            "processed_image": processed,
            "method_ink_stats": stats,
            "ocr_contact_slot_index": index,
            "ocr_contact_slot": [slot_x, slot_y, slot_x + method.slot_width, slot_y + method.slot_height],
            "ocr_contact_crop_box": paste_box,
            "ocr_candidate": bool(candidate),
            "expected_digits": expected_digits,
        }
        prepared.append(prepared_region)
        if candidate:
            polygons.append(
                [
                    [paste_box[0], paste_box[1]],
                    [paste_box[2], paste_box[1]],
                    [paste_box[2], paste_box[3]],
                    [paste_box[0], paste_box[3]],
                ]
            )
        else:
            polygons.append([])
    contact_path = out_dir / f"{method.name}_contact_sheet.png"
    sheet.save(contact_path)
    return sheet, prepared, polygons


def _run_yomitoku_direct(
    recognizer: Any,
    sheet: Image.Image,
    regions: list[dict[str, Any]],
    polygons: list[list[list[int]]],
) -> list[dict[str, Any]]:
    active_regions: list[dict[str, Any]] = []
    active_polygons: list[list[list[int]]] = []
    for region, polygon in zip(regions, polygons):
        if polygon:
            active_regions.append(region)
            active_polygons.append(polygon)
    predictions = {str(region.get("sheet_cell")): {"raw_text": "", "digits": "", "score": 0.0} for region in regions}
    if active_regions:
        results, _vis = recognizer(_bgr_from_pil(sheet), points=active_polygons)
        contents = list(getattr(results, "contents", []))
        scores = [float(value) for value in getattr(results, "scores", [])]
        for index, region in enumerate(active_regions):
            raw_text = str(contents[index] if index < len(contents) else "").strip()
            digits = _clean_text_for_number(raw_text)
            score = float(scores[index] if index < len(scores) else 0.0)
            predictions[str(region.get("sheet_cell"))] = {"raw_text": raw_text, "digits": digits, "score": score}
    return [
        {
            **region,
            "ocr_engine": "yomitoku_text_recognizer_direct",
            "raw_text": predictions[str(region.get("sheet_cell"))]["raw_text"],
            "pred_digits": predictions[str(region.get("sheet_cell"))]["digits"],
            "score": predictions[str(region.get("sheet_cell"))]["score"],
        }
        for region in regions
    ]


def _knn_feature(image: Image.Image) -> np.ndarray:
    gray = np.array(image.convert("L"))
    if gray.size == 0:
        return np.zeros((28 * 28,), dtype=np.float32)
    _threshold, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    points = cv2.findNonZero(ink)
    if points is not None:
        x, y, w, h = cv2.boundingRect(points)
        gray = gray[max(0, y - 3) : min(gray.shape[0], y + h + 3), max(0, x - 3) : min(gray.shape[1], x + w + 3)]
    height, width = gray.shape[:2]
    side = max(height, width, 1) + 8
    canvas = np.full((side, side), 255, dtype=np.uint8)
    y0 = (side - height) // 2
    x0 = (side - width) // 2
    canvas[y0 : y0 + height, x0 : x0 + width] = gray
    resized = cv2.resize(canvas, (28, 28), interpolation=cv2.INTER_AREA)
    return ((255 - resized).astype(np.float32) / 255.0).reshape(-1)


def _run_opencv_knn_leave_one_out(regions: list[dict[str, Any]], *, k: int = 5) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for region in regions:
        truth = region.get("truth") or {}
        if not truth.get("eval_numeric") or not isinstance(region.get("processed_image"), Image.Image):
            continue
        samples.append(
            {
                "cell": str(region.get("sheet_cell")),
                "expected": str(region.get("expected_digits") or ""),
                "feature": _knn_feature(region["processed_image"]),
            }
        )
    if not samples:
        return [
            {
                **region,
                "ocr_engine": "opencv_knn_leave_one_out_k5",
                "raw_text": "",
                "pred_digits": "",
                "score": 0.0,
            }
            for region in regions
        ]
    features = np.stack([sample["feature"] for sample in samples]).astype(np.float32)
    by_cell = {sample["cell"]: index for index, sample in enumerate(samples)}
    predictions: dict[str, dict[str, Any]] = {}
    for index, sample in enumerate(samples):
        mask = np.ones(len(samples), dtype=bool)
        mask[index] = False
        train_features = features[mask]
        train_samples = [other for other_index, other in enumerate(samples) if other_index != index]
        distances = ((train_features - sample["feature"]) ** 2).sum(axis=1)
        nearest_indexes = np.argsort(distances)[: max(1, min(k, len(train_samples)))]
        votes: dict[str, float] = {}
        for nearest_index in nearest_indexes:
            label = train_samples[int(nearest_index)]["expected"]
            weight = 1.0 / (float(distances[int(nearest_index)]) + 1e-6)
            votes[label] = votes.get(label, 0.0) + weight
        pred, vote = max(votes.items(), key=lambda item: item[1])
        total_vote = sum(votes.values()) or 1.0
        predictions[sample["cell"]] = {
            "raw_text": pred,
            "pred_digits": pred,
            "score": round(float(vote / total_vote), 4),
        }
    assigned = []
    for region in regions:
        cell = str(region.get("sheet_cell"))
        pred = predictions.get(cell, {"raw_text": "", "pred_digits": "", "score": 0.0})
        assigned.append(
            {
                **region,
                "ocr_engine": "opencv_knn_leave_one_out_k5",
                "raw_text": pred["raw_text"],
                "pred_digits": pred["pred_digits"],
                "score": pred["score"],
                "supervised_label_source": "stg_draft_sheet_leave_one_out",
            }
        )
    return assigned


def _evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    eval_records = [r for r in records if (r.get("truth") or {}).get("eval_numeric")]
    expected_nonempty = [r for r in eval_records if str(r.get("expected_digits") or "")]
    expected_blank = [r for r in eval_records if not str(r.get("expected_digits") or "")]
    pred_nonempty = [r for r in eval_records if str(r.get("pred_digits") or "")]
    exact = [
        r
        for r in eval_records
        if str(r.get("expected_digits") or "") == str(r.get("pred_digits") or "")
    ]
    nonempty_exact = [
        r
        for r in expected_nonempty
        if str(r.get("expected_digits") or "") == str(r.get("pred_digits") or "")
    ]
    false_negative = [r for r in expected_nonempty if not str(r.get("pred_digits") or "")]
    wrong_digit = [
        r
        for r in expected_nonempty
        if str(r.get("pred_digits") or "") and str(r.get("expected_digits") or "") != str(r.get("pred_digits") or "")
    ]
    false_positive = [r for r in expected_blank if str(r.get("pred_digits") or "")]
    return {
        "numeric_eval_cell_count": len(eval_records),
        "expected_nonempty_count": len(expected_nonempty),
        "expected_blank_count": len(expected_blank),
        "pred_nonempty_count": len(pred_nonempty),
        "exact_all_count": len(exact),
        "exact_all_rate": round(len(exact) / max(1, len(eval_records)), 4),
        "nonempty_exact_count": len(nonempty_exact),
        "nonempty_exact_rate": round(len(nonempty_exact) / max(1, len(expected_nonempty)), 4),
        "false_negative_count": len(false_negative),
        "wrong_digit_count": len(wrong_digit),
        "false_positive_blank_count": len(false_positive),
        "candidate_count": sum(1 for r in eval_records if r.get("ocr_candidate")),
    }


def _apply_soft_pair_spillover_postprocess(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_cell = {str(record.get("sheet_cell")): dict(record) for record in records}
    for row in range(EVAL_WORKSHEET_ROW_START, EVAL_WORKSHEET_ROW_START + EVAL_ROW_COUNT):
        left = by_cell.get(f"G{row}")
        right = by_cell.get(f"H{row}")
        if not left or not right:
            continue
        if str(left.get("pred_digits") or "") == "1":
            if not str(right.get("pred_digits") or ""):
                right["pred_digits"] = "1"
                right["raw_text"] = "1|soft_pair_spillover_from_left"
                right["score"] = min(float(left.get("score") or 0.0), 0.5)
                right["postprocess"] = "soft_pair_spillover_from_left"
            left["pred_digits"] = ""
            left["raw_text"] = ""
            left["score"] = 0.0
            left["postprocess"] = "soft_pair_left_cleared"
    return [by_cell.get(str(record.get("sheet_cell")), record) for record in records]


def _strip_record_for_json(record: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in record.items():
        if key == "processed_image":
            continue
        if key in {"bbox", "ocr_contact_slot", "ocr_contact_crop_box"}:
            out[key] = value
        elif key in {
            "region_id",
            "target_cell_id",
            "sheet_cell",
            "worksheet_row",
            "worksheet_col",
            "grid_row_index",
            "grid_col_index",
            "role",
            "field",
            "field_label",
            "date",
            "daypart",
            "menu_name",
            "merged_cell",
            "logical_targets",
            "covered_sheet_cells",
            "x_snap",
            "ocr_contact_slot_index",
            "ocr_cell_crop_bbox_px",
            "expected_digits",
            "pred_digits",
            "raw_text",
            "score",
            "ocr_engine",
            "ocr_candidate",
            "postprocess",
            "supervised_label_source",
            "method_ink_stats",
            "truth",
            "recognizer_raw_text",
            "recognizer_score",
            "recognizer_decision_source",
            "recognizer_candidates",
            "recognizer_accepted_candidate",
            "recognizer_candidate_accepted",
            "recognizer_ink_stats",
        }:
            out[key] = value
    return out


def _write_records_csv(records: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "method",
        "engine",
        "sheet_cell",
        "row_index",
        "field",
        "date",
        "daypart",
        "menu_name",
        "expected",
        "pred",
        "raw",
        "score",
        "candidate",
        "match",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            truth = r.get("truth") or {}
            expected = str(r.get("expected_digits") or "")
            pred = str(r.get("pred_digits") or "")
            writer.writerow(
                {
                    "method": r.get("method"),
                    "engine": r.get("ocr_engine"),
                    "sheet_cell": r.get("sheet_cell"),
                    "row_index": truth.get("row_index"),
                    "field": truth.get("field"),
                    "date": truth.get("date"),
                    "daypart": truth.get("daypart"),
                    "menu_name": truth.get("menu_name"),
                    "expected": expected,
                    "pred": pred,
                    "raw": r.get("raw_text"),
                    "score": r.get("score"),
                    "candidate": r.get("ocr_candidate"),
                    "match": expected == pred,
                }
            )


def _draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    try:
        draw.text(xy, text, font=font, fill=fill)
    except Exception:
        draw.text(xy, text.encode("ascii", "ignore").decode("ascii") or "?", font=font, fill=fill)


def _make_summary_page(summary: dict[str, Any], out_path: Path) -> Image.Image:
    font = _load_overlay_font(22)
    small = _load_overlay_font(17)
    image = Image.new("RGB", (1800, 1200), "white")
    draw = ImageDraw.Draw(image)
    y = 28
    _draw_text(draw, (30, y), "Kasuga Matsushige digit OCR preprocessing comparison", font=font, fill=(0, 0, 0))
    y += 42
    _draw_text(
        draw,
        (30, y),
        f"facility={summary['facility_code']} order={summary['order_id']} labels=stg draft-sheet",
        font=small,
        fill=(40, 40, 40),
    )
    y += 46
    headers = ["method", "engine", "nonempty exact", "all exact", "FN", "wrong", "FP blank", "pred", "sec"]
    xs = [30, 470, 780, 980, 1140, 1230, 1330, 1480, 1580]
    for x, h in zip(xs, headers):
        _draw_text(draw, (x, y), h, font=small, fill=(0, 0, 0))
    y += 28
    draw.line((30, y, 1720, y), fill=(0, 0, 0), width=2)
    y += 10
    for item in summary["results"]:
        metrics = item["metrics"]
        color = (0, 0, 0)
        values = [
            item["method"],
            item["engine"].replace("_contact_sheet_", "_"),
            f"{metrics['nonempty_exact_count']}/{metrics['expected_nonempty_count']} ({metrics['nonempty_exact_rate']:.1%})",
            f"{metrics['exact_all_count']}/{metrics['numeric_eval_cell_count']} ({metrics['exact_all_rate']:.1%})",
            str(metrics["false_negative_count"]),
            str(metrics["wrong_digit_count"]),
            str(metrics["false_positive_blank_count"]),
            str(metrics["pred_nonempty_count"]),
            str(item["elapsed_seconds"]),
        ]
        for x, value in zip(xs, values):
            _draw_text(draw, (x, y), value[:42], font=small, fill=color)
        y += 30
        if y > 1120:
            break
    image.save(out_path)
    return image


def _make_failure_page(records: list[dict[str, Any]], title: str, out_path: Path, max_items: int = 80) -> Image.Image:
    font = _load_overlay_font(15)
    title_font = _load_overlay_font(22)
    failures = [
        r
        for r in records
        if (r.get("truth") or {}).get("eval_numeric")
        and str(r.get("expected_digits") or "") != str(r.get("pred_digits") or "")
    ][:max_items]
    cell_w, cell_h = 180, 128
    columns = 6
    rows = max(1, math.ceil(len(failures) / columns))
    image = Image.new("RGB", (columns * cell_w, rows * cell_h + 54), "white")
    draw = ImageDraw.Draw(image)
    _draw_text(draw, (18, 16), title, font=title_font, fill=(0, 0, 0))
    for index, record in enumerate(failures):
        x = (index % columns) * cell_w
        y = 54 + (index // columns) * cell_h
        draw.rectangle((x + 4, y + 4, x + cell_w - 4, y + cell_h - 4), outline=(210, 210, 210))
        thumb = record["processed_image"].convert("RGB")
        thumb.thumbnail((cell_w - 16, 56), Image.Resampling.NEAREST)
        image.paste(thumb, (x + (cell_w - thumb.width) // 2, y + 8))
        truth = record.get("truth") or {}
        _draw_text(draw, (x + 8, y + 70), f"{record.get('sheet_cell')} {truth.get('field')}", font=font, fill=(0, 0, 0))
        _draw_text(
            draw,
            (x + 8, y + 91),
            f"exp={record.get('expected_digits') or '∅'} pred={record.get('pred_digits') or '∅'}",
            font=font,
            fill=(180, 0, 0),
        )
    image.save(out_path)
    return image


def run(
    output_dir: Path,
    *,
    device: str,
    only: str | None = None,
    engine_only: str | None = None,
    include_supervised_knn: bool = False,
    apply_soft_pair_spillover: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    page, item = _load_manifest_item(DEFAULT_MANIFEST)
    pre = _build_preprocess_for_ocr(item=item, page=page, render_width=1864)
    all_regions = pre["target_regions"]
    eval_regions = [
        region
        for region in all_regions
        if EVAL_WORKSHEET_ROW_START <= int(region.get("worksheet_row") or 0) < EVAL_WORKSHEET_ROW_START + EVAL_ROW_COUNT
        and int(region.get("worksheet_col") or 0) in WORKSHEET_COL_TO_STG_FIELD
    ]
    stg_draft = _fetch_stg_draft_sheet(output_dir)
    truth = _build_ground_truth(stg_draft, eval_regions)
    recognizer = _load_text_recognizer(device)
    all_flat_records: list[dict[str, Any]] = []
    result_items: list[dict[str, Any]] = []
    pdf_pages: list[Image.Image] = []
    method_dirs = output_dir / "methods"
    method_dirs.mkdir(exist_ok=True)
    active_methods = [method for method in METHODS if only is None or re.search(only, method.name)]
    for method in active_methods:
        method_dir = method_dirs / method.name
        method_dir.mkdir(exist_ok=True)
        method_regions = eval_regions
        snap_debug: dict[str, Any] | None = None
        if method.snap_x_to_fax_lines:
            method_regions, snap_debug = _snap_regions_x_to_fax_lines(pre["raw_rectified"], eval_regions)
            (method_dir / "x_snap_debug.json").write_text(
                json.dumps(snap_debug, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        contact_sheet, prepared, polygons = _build_contact_sheet(pre["raw_rectified"], method_regions, truth, method, method_dir)
        engine_runners: list[tuple[str, Any]] = [
            ("yomitoku_text_recognizer_direct", lambda: _run_yomitoku_direct(recognizer, contact_sheet, prepared, polygons)),
        ]
        if include_supervised_knn:
            engine_runners.append(("opencv_knn_leave_one_out_k5", lambda: _run_opencv_knn_leave_one_out(prepared, k=5)))
        if engine_only is not None:
            engine_runners = [(name, runner) for name, runner in engine_runners if re.search(engine_only, name)]
        for engine_name, runner in engine_runners:
            t0 = time.perf_counter()
            records = runner()
            elapsed = time.perf_counter() - t0
            if apply_soft_pair_spillover:
                records = _apply_soft_pair_spillover_postprocess(records)
            for record in records:
                record["method"] = method.name
                if apply_soft_pair_spillover:
                    record["postprocess_enabled"] = "soft_pair_spillover"
            metrics = _evaluate(records)
            all_flat_records.extend(records)
            records_path = method_dir / f"{engine_name}_records.json"
            records_path.write_text(
                json.dumps([_strip_record_for_json(record) for record in records], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            failures_png = method_dir / f"{engine_name}_failures.png"
            failure_page = _make_failure_page(records, f"{method.name} / {engine_name}", failures_png)
            pdf_pages.append(failure_page)
            result_items.append(
                {
                    "method": method.name,
                    "description": method.description,
                    "engine": engine_name,
                    "elapsed_seconds": round(float(elapsed), 3),
                    "metrics": metrics,
                    "snap_x_to_fax_lines": bool(method.snap_x_to_fax_lines),
                    "postprocess": "soft_pair_spillover" if apply_soft_pair_spillover else None,
                    "snap_debug": snap_debug,
                    "outputs": {
                        "contact_sheet": str(method_dir / f"{method.name}_contact_sheet.png"),
                        "records": str(records_path),
                        "failures_png": str(failures_png),
                    },
                }
            )
    summary = {
        "facility_code": TARGET_FACILITY_CODE,
        "order_id": TARGET_ORDER_ID,
        "stg_label_source": f"{STG_API_BASE}/orders/{TARGET_ORDER_ID}/draft-sheet",
        "stg_label_cache": str(output_dir / "stg_ORD9d8f9c2b_draft_sheet.json"),
        "eval_rows": EVAL_ROW_COUNT,
        "eval_regions": len(eval_regions),
        "numeric_eval_regions": sum(1 for region in eval_regions if truth.get(str(region.get("sheet_cell")), {}).get("eval_numeric")),
        "expected_nonempty_numeric": sum(
            1
            for region in eval_regions
            if truth.get(str(region.get("sheet_cell")), {}).get("eval_numeric")
            and truth.get(str(region.get("sheet_cell")), {}).get("expected_digits")
        ),
        "excluded_note_regions": sum(
            1 for region in eval_regions if not truth.get(str(region.get("sheet_cell")), {}).get("eval_numeric")
        ),
        "results": sorted(
            result_items,
            key=lambda item: (
                -item["metrics"]["nonempty_exact_rate"],
                item["metrics"]["false_negative_count"],
                item["metrics"]["wrong_digit_count"],
                item["metrics"]["false_positive_blank_count"],
            ),
        ),
    }
    summary_path = output_dir / "comparison_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_records_csv(all_flat_records, output_dir / "all_method_cell_results.csv")
    summary_page = _make_summary_page(summary, output_dir / "comparison_summary.png")
    pdf_pages.insert(0, summary_page)
    pdf_path = output_dir / "comparison_review.pdf"
    _write_pdf_from_pages(pdf_pages, pdf_path)
    summary["outputs"] = {
        "summary_json": str(summary_path),
        "summary_png": str(output_dir / "comparison_summary.png"),
        "all_results_csv": str(output_dir / "all_method_cell_results.csv"),
        "review_pdf": str(pdf_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--only", default=None, help="Regex filter for method names.")
    parser.add_argument("--engine-only", default=None, help="Regex filter for OCR engine names.")
    parser.add_argument("--include-supervised-knn", action="store_true")
    parser.add_argument("--apply-soft-pair-spillover", action="store_true")
    args = parser.parse_args()
    summary = run(
        args.output_dir,
        device=args.device,
        only=args.only,
        engine_only=args.engine_only,
        include_supervised_knn=args.include_supervised_knn,
        apply_soft_pair_spillover=args.apply_soft_pair_spillover,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
