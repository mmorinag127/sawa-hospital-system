from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np


FORBIDDEN_DOWNSTREAM_METHODS = [
    "fax_candidate_gap_sequence_match",
    "drop_one_extra_fax_line_by_min_affine_residual",
    "ordered_affine_dp_match_with_interpolated_missing_lines",
]
ORDER_FORM_TEMPLATE_Y_EDGE_COUNT = 59
TWO_STAGE_HEADER_BOUNDARY_MIN_WIDTH_RATIO = 0.12
TWO_STAGE_HEADER_BOUNDARY_Y_OFFSET_RANGE = (40, 110)


@dataclass(frozen=True)
class FixedQuadTemplateRegistrationResult:
    facility_code: str
    order_id: str
    fax_pdf: str
    template_pdf: str
    quad_source: str | None
    quad_px: list[list[float]]
    rectified_canvas_size: list[int]
    template_axes_x: list[int]
    template_axes_y_used_count: int
    template_axes_y_used_first_last: list[int]
    template_axes_y_all: list[int]
    template_outer_grid_bbox_used: list[int]
    legacy_manifest_template_bbox_not_used: list[float]
    forbidden_downstream_methods: list[str]
    outputs: dict[str, str | None]


def load_fixed_quad_manifest_item(
    manifest_path: Path,
    *,
    facility_code: str,
    order_id: str,
) -> dict[str, Any]:
    with manifest_path.open() as f:
        manifest = json.load(f)
    for item in manifest.get("results") or []:
        if item.get("facility_code") == facility_code and item.get("order_id") == order_id:
            return item
    raise ValueError(f"manifest item not found: facility={facility_code} order={order_id}")


def render_pdf_page_to_bgr(pdf_path: str, *, width: int | None = None) -> np.ndarray:
    doc = fitz.open(pdf_path)
    if doc.page_count < 1:
        raise ValueError(f"pdf has no pages: {pdf_path}")
    page = doc[0]
    scale = (float(width) / page.rect.width) if width else 1.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_RGBA2RGB)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def render_template_pdf_to_canvas(template_pdf: str, *, width: int, height: int) -> np.ndarray:
    doc = fitz.open(template_pdf)
    if doc.page_count < 1:
        raise ValueError(f"template pdf has no pages: {template_pdf}")
    page = doc[0]
    scale = float(width) / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_RGBA2RGB)
    image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if image.shape[:2] != (height, width):
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    return image


def cluster_projection(values: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    indexes = np.where(values > threshold)[0]
    if len(indexes) == 0:
        return []
    clusters: list[tuple[int, int]] = []
    start = prev = int(indexes[0])
    for value in indexes[1:]:
        current = int(value)
        if current > prev + 1:
            clusters.append((start, prev))
            start = current
        prev = current
    clusters.append((start, prev))
    return clusters


def extract_template_axes_from_image(
    template_image: np.ndarray,
    *,
    manifest_template_bbox: list[float],
) -> tuple[list[int], list[int], list[int], list[int]]:
    height, width = template_image.shape[:2]
    gray = cv2.cvtColor(template_image, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    vertical = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, 80)),
    )
    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (80, 1)),
    )
    vertical_clusters = cluster_projection(vertical.sum(axis=0) / 255, height * 0.25)
    horizontal_clusters = cluster_projection(horizontal.sum(axis=1) / 255, width * 0.25)
    xs = [int(round((start + end) / 2)) for start, end in vertical_clusters]
    ys_all = [int(round((start + end) / 2)) for start, end in horizontal_clusters]
    if not xs or not ys_all:
        raise ValueError("template axes not found")

    manifest_y0 = float(manifest_template_bbox[1])
    weak_horizontal_clusters = cluster_projection(
        horizontal.sum(axis=1) / 255,
        max(50.0, width * TWO_STAGE_HEADER_BOUNDARY_MIN_WIDTH_RATIO),
    )
    weak_ys = [int(round((start + end) / 2)) for start, end in weak_horizontal_clusters]
    offset_min, offset_max = TWO_STAGE_HEADER_BOUNDARY_Y_OFFSET_RANGE
    header_boundaries = [
        y
        for y in weak_ys
        if manifest_y0 + offset_min <= y <= manifest_y0 + offset_max
    ]
    for y in header_boundaries:
        if all(abs(y - existing) > 3 for existing in ys_all):
            ys_all.append(y)
    ys_all = sorted(ys_all)

    # Preserve the approved order-form structure: two header row bands plus
    # 56 body row bands require 59 y-edges. Some source templates have one
    # or two extra title/header ruling lines above the table, so keep the
    # bottom 59 structure edges instead of trusting a per-render bbox cutoff.
    ys_from_table = [y for y in ys_all if y >= manifest_y0 - 5]
    if len(ys_from_table) >= ORDER_FORM_TEMPLATE_Y_EDGE_COUNT:
        ys = ys_from_table[:ORDER_FORM_TEMPLATE_Y_EDGE_COUNT]
    elif len(ys_all) >= ORDER_FORM_TEMPLATE_Y_EDGE_COUNT:
        ys = ys_all[-ORDER_FORM_TEMPLATE_Y_EDGE_COUNT:]
    else:
        ys = [y for y in ys_all if y >= manifest_y0 - 5]
    if not ys:
        raise ValueError("template table y axes not found")
    return xs, ys, [int(v) for v in xs], [int(v) for v in ys_all]


def draw_fixed_quad_overlay(original: np.ndarray, quad: np.ndarray) -> np.ndarray:
    image = original.copy()
    cv2.polylines(image, [quad.astype(np.int32)], True, (0, 0, 255), 6, cv2.LINE_AA)
    labels = ["TL", "TR", "BR", "BL"]
    colors = [(0, 0, 255), (255, 0, 0), (0, 160, 0), (0, 140, 255)]
    for point, label, color in zip(quad, labels, colors):
        x, y = map(int, point)
        cv2.circle(image, (x, y), 12, color, -1, cv2.LINE_AA)
        cv2.putText(
            image,
            label,
            (x + 10, y + 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            color,
            3,
            cv2.LINE_AA,
        )
    cv2.putText(
        image,
        "STEP1 original FAX + fixed accepted 4 points",
        (30, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.1,
        (0, 0, 255),
        3,
        cv2.LINE_AA,
    )
    return image


def draw_rectified_fax_overlay(rectified: np.ndarray, table_bbox: list[int]) -> np.ndarray:
    x0, y0, x1, y1 = table_bbox
    image = rectified.copy()
    cv2.rectangle(image, (x0, y0), (x1, y1), (0, 0, 255), 4, cv2.LINE_AA)
    cv2.putText(
        image,
        "STEP2 rectified FAX by accepted 4 points -> actual template outer grid",
        (30, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 0, 255),
        3,
        cv2.LINE_AA,
    )
    return image


def draw_template_grid_overlay(rectified: np.ndarray, xs: list[int], ys: list[int]) -> np.ndarray:
    x0, y0, x1, y1 = [xs[0], ys[0], xs[-1], ys[-1]]
    image = rectified.copy()
    for x in xs:
        if x0 <= x <= x1:
            cv2.line(image, (x, y0), (x, y1), (0, 255, 0), 2, cv2.LINE_AA)
    for y in ys:
        if y0 <= y <= y1:
            cv2.line(image, (x0, y), (x1, y), (0, 255, 0), 2, cv2.LINE_AA)
    cv2.rectangle(image, (x0, y0), (x1, y1), (0, 180, 0), 3, cv2.LINE_AA)
    cv2.putText(
        image,
        "STEP3 rectified FAX + template ruling grid (uniform green)",
        (30, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 160, 0),
        3,
        cv2.LINE_AA,
    )
    return image


def rectify_fax_to_template_grid(
    original: np.ndarray,
    *,
    quad_px: list[list[float]],
    table_bbox: list[int],
    canvas_width: int,
    canvas_height: int,
) -> np.ndarray:
    quad = np.array(quad_px, dtype=np.float32)
    dst = np.array(
        [
            [table_bbox[0], table_bbox[1]],
            [table_bbox[2], table_bbox[1]],
            [table_bbox[2], table_bbox[3]],
            [table_bbox[0], table_bbox[3]],
        ],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(
        original,
        transform,
        (canvas_width, canvas_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def write_fixed_quad_registration_outputs(
    *,
    output_dir: Path,
    facility_code: str,
    order_id: str,
    step1: np.ndarray,
    step2: np.ndarray,
    step3: np.ndarray,
    result: FixedQuadTemplateRegistrationResult,
) -> FixedQuadTemplateRegistrationResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "step1": output_dir / "01_original_fax_fixed_4points.png",
        "step2": output_dir / "02_rectified_fax_by_fixed_4points.png",
        "step3": output_dir / "03_rectified_fax_template_grid_overlay.png",
    }
    cv2.imwrite(str(paths["step1"]), step1)
    cv2.imwrite(str(paths["step2"]), step2)
    cv2.imwrite(str(paths["step3"]), step3)

    pdf_path = output_dir / f"{facility_code}_{order_id}_original_quad_rectify_template_overlay.pdf"
    pdf = fitz.open()
    for key in ("step1", "step2", "step3"):
        image = cv2.imread(str(paths[key]))
        height, width = image.shape[:2]
        page = pdf.new_page(width=width, height=height)
        page.insert_image(fitz.Rect(0, 0, width, height), filename=str(paths[key]))
    pdf.save(str(pdf_path))

    updated = FixedQuadTemplateRegistrationResult(
        **{
            **asdict(result),
            "outputs": {key: str(value) for key, value in paths.items()} | {"pdf": str(pdf_path)},
        }
    )
    summary_path = output_dir / "summary.json"
    with summary_path.open("w") as f:
        json.dump(asdict(updated), f, ensure_ascii=False, indent=2)
    return updated


def build_fixed_quad_template_registration(
    *,
    facility_code: str,
    order_id: str,
    fax_pdf: str,
    template_pdf: str,
    quad_px: list[list[float]],
    manifest_template_bbox: list[float],
    canvas_width: int,
    canvas_height: int,
    render_width: int,
    quad_source: str | None = None,
    output_dir: Path | None = None,
) -> tuple[FixedQuadTemplateRegistrationResult, dict[str, np.ndarray]]:
    original = render_pdf_page_to_bgr(fax_pdf, width=render_width)
    template = render_template_pdf_to_canvas(
        template_pdf,
        width=canvas_width,
        height=canvas_height,
    )
    xs, ys, _xs_all, ys_all = extract_template_axes_from_image(
        template,
        manifest_template_bbox=manifest_template_bbox,
    )
    table_bbox = [xs[0], ys[0], xs[-1], ys[-1]]
    rectified = rectify_fax_to_template_grid(
        original,
        quad_px=quad_px,
        table_bbox=table_bbox,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    quad = np.array(quad_px, dtype=np.float32)
    step_images = {
        "step1": draw_fixed_quad_overlay(original, quad),
        "step2": draw_rectified_fax_overlay(rectified, table_bbox),
        "step3": draw_template_grid_overlay(rectified, xs, ys),
    }
    result = FixedQuadTemplateRegistrationResult(
        facility_code=facility_code,
        order_id=order_id,
        fax_pdf=fax_pdf,
        template_pdf=template_pdf,
        quad_source=quad_source,
        quad_px=quad_px,
        rectified_canvas_size=[canvas_width, canvas_height],
        template_axes_x=xs,
        template_axes_y_used_count=len(ys),
        template_axes_y_used_first_last=[ys[0], ys[-1]],
        template_axes_y_all=ys_all,
        template_outer_grid_bbox_used=table_bbox,
        legacy_manifest_template_bbox_not_used=manifest_template_bbox,
        forbidden_downstream_methods=FORBIDDEN_DOWNSTREAM_METHODS,
        outputs={"step1": None, "step2": None, "step3": None, "pdf": None},
    )
    if output_dir is not None:
        result = write_fixed_quad_registration_outputs(
            output_dir=output_dir,
            facility_code=facility_code,
            order_id=order_id,
            step1=step_images["step1"],
            step2=step_images["step2"],
            step3=step_images["step3"],
            result=result,
        )
    return result, step_images


def replay_fixed_quad_template_overlay(
    *,
    manifest_path: Path,
    facility_code: str,
    order_id: str,
    output_dir: Path,
    render_width: int,
) -> dict[str, Any]:
    item = load_fixed_quad_manifest_item(
        manifest_path,
        facility_code=facility_code,
        order_id=order_id,
    )

    existing_step2 = cv2.imread(item["step2_png"])
    if existing_step2 is None:
        raise ValueError(f"step2 canvas not found: {item['step2_png']}")
    canvas_height, canvas_width = existing_step2.shape[:2]

    result, _images = build_fixed_quad_template_registration(
        facility_code=facility_code,
        order_id=order_id,
        fax_pdf=item["fax_pdf"],
        template_pdf=item["template_pdf"],
        quad_px=item["quad_px"],
        manifest_template_bbox=item["template_bbox"],
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        render_width=render_width,
        quad_source=item.get("quad_source"),
        output_dir=output_dir,
    )
    return asdict(result)
