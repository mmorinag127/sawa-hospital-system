#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from src.services.hakodate_fixed_quad_registration_service import (  # noqa: E402
    build_fixed_quad_template_registration,
    render_template_pdf_to_canvas,
)
from src.services.hakodate_step_review_pipeline_service import (  # noqa: E402
    _bbox_quad_points,
    _bgr_to_rgb_image,
    _draw_quad_points,
    _make_review_canvas,
    _split_line_masks,
    _write_pdf_from_pages,
)


def _load_template_axes_from_summary(path: Path | None) -> tuple[list[int] | None, list[int] | None, list[float]]:
    if path is None:
        return None, None, [12.0, 435.0, 2353.0, 4009.0]
    data = json.loads(path.read_text(encoding="utf-8"))
    xs = [int(value) for value in data.get("template_axes_x") or []]
    ys = [int(value) for value in data.get("template_axes_y_all") or []]
    bbox = data.get("legacy_manifest_template_bbox_not_used") or [12.0, 435.0, 2353.0, 4009.0]
    if len(xs) < 2:
        xs = None
    if len(ys) < 2:
        ys = None
    return xs, ys, [float(value) for value in bbox]


def _header_bounds(table_bbox: list[int], template_ys: list[int]) -> tuple[int, int, int, int]:
    x0, _table_y0, x1, _table_y1 = table_bbox
    sorted_ys = sorted(int(value) for value in template_ys)
    if len(sorted_ys) >= 4:
        y0 = sorted_ys[0] - 18
        y1 = sorted_ys[2] + 12
    else:
        y0 = table_bbox[1] - 18
        y1 = table_bbox[1] + 240
    return int(x0), max(0, int(y0)), int(x1), max(0, int(y1))


def _piecewise_map(value: float, anchors: list[dict[str, float | int]]) -> float | None:
    if not anchors:
        return None
    ordered = sorted(anchors, key=lambda item: float(item["template"]))
    if value <= float(ordered[0]["template"]):
        if len(ordered) == 1:
            return float(ordered[0]["fax"])
        left, right = ordered[0], ordered[1]
    elif value >= float(ordered[-1]["template"]):
        if len(ordered) == 1:
            return float(ordered[-1]["fax"])
        left, right = ordered[-2], ordered[-1]
    else:
        left = ordered[0]
        right = ordered[-1]
        for before, after in zip(ordered, ordered[1:]):
            if float(before["template"]) <= value <= float(after["template"]):
                left, right = before, after
                break
    source_span = float(right["template"]) - float(left["template"])
    if abs(source_span) < 1e-6:
        return float(left["fax"])
    ratio = (value - float(left["template"])) / source_span
    return round(float(left["fax"]) + ratio * (float(right["fax"]) - float(left["fax"])), 2)


def _template_header_segments(
    *,
    template_bgr: np.ndarray,
    table_bbox: list[int],
    template_ys: list[int],
    template_points: list[dict[str, Any]],
) -> dict[str, list[dict[str, float | str | list[float]]]]:
    h_mask, v_mask = _split_line_masks(template_bgr)
    image_h, image_w = h_mask.shape[:2]
    x0, y0, x1, y1 = _header_bounds(table_bbox, template_ys)
    x0 = max(0, min(image_w, x0))
    x1 = max(0, min(image_w, x1))
    y0 = max(0, min(image_h, y0))
    y1 = max(0, min(image_h, y1))
    if x0 >= x1 or y0 >= y1:
        return {"horizontal": [], "vertical": []}

    by_y: dict[int, list[dict[str, Any]]] = {}
    by_x: dict[int, list[dict[str, Any]]] = {}
    for point in template_points:
        by_y.setdefault(int(point["y_index"]), []).append(point)
        by_x.setdefault(int(point["x_index"]), []).append(point)

    horizontal: list[dict[str, float | str | list[float]]] = []
    vertical: list[dict[str, float | str | list[float]]] = []

    for y_index, points in by_y.items():
        ordered = sorted(points, key=lambda item: float(item["x"]))
        for left, right in zip(ordered, ordered[1:]):
            lx = int(round(float(left["x"])))
            rx = int(round(float(right["x"])))
            y = int(round(float(left["y"])))
            if rx <= lx:
                continue
            window = h_mask[max(0, y - 5) : min(image_h, y + 6), max(0, lx) : min(image_w, rx + 1)]
            if window.size == 0:
                continue
            column_hits = np.count_nonzero(window, axis=0) > 0
            coverage = float(np.count_nonzero(column_hits)) / float(max(1, rx - lx + 1))
            if coverage < 0.45:
                continue
            horizontal.append(
                {
                    "orientation": "horizontal",
                    "bbox": [float(lx), float(y - 5), float(rx - lx), 10.0],
                    "x1": float(lx),
                    "y1": float(y),
                    "x2": float(rx),
                    "y2": float(y),
                    "coverage": round(coverage, 3),
                    "template_y_index": float(y_index),
                }
            )

    for x_index, points in by_x.items():
        ordered = sorted(points, key=lambda item: float(item["y"]))
        for upper, lower in zip(ordered, ordered[1:]):
            x = int(round(float(upper["x"])))
            uy = int(round(float(upper["y"])))
            ly = int(round(float(lower["y"])))
            if ly <= uy:
                continue
            window = v_mask[max(0, uy) : min(image_h, ly + 1), max(0, x - 5) : min(image_w, x + 6)]
            if window.size == 0:
                continue
            row_hits = np.count_nonzero(window, axis=1) > 0
            coverage = float(np.count_nonzero(row_hits)) / float(max(1, ly - uy + 1))
            if coverage < 0.45:
                continue
            vertical.append(
                {
                    "orientation": "vertical",
                    "bbox": [float(x - 5), float(uy), 10.0, float(ly - uy)],
                    "x1": float(x),
                    "y1": float(uy),
                    "x2": float(x),
                    "y2": float(ly),
                    "coverage": round(coverage, 3),
                    "template_x_index": float(x_index),
                }
            )

    return {
        "horizontal": sorted(horizontal, key=lambda item: (float(item["y1"]), float(item["x1"]))),
        "vertical": sorted(vertical, key=lambda item: (float(item["x1"]), float(item["y1"]))),
    }


def _template_header_intersections(
    *,
    template_xs: list[int],
    template_ys: list[int],
    template_bgr: np.ndarray,
) -> list[dict[str, Any]]:
    if len(template_xs) < 2 or len(template_ys) < 3:
        return []
    header_ys = sorted(int(value) for value in template_ys[:3])
    h_mask, v_mask = _split_line_masks(template_bgr)
    image_h, image_w = h_mask.shape[:2]
    points: list[dict[str, Any]] = []
    for y_index, y in enumerate(header_ys):
        for x_index, x in enumerate(sorted(int(value) for value in template_xs)):
            x0 = max(0, x - 9)
            x1 = min(image_w, x + 10)
            y0 = max(0, y - 9)
            y1 = min(image_h, y + 10)
            if x0 >= x1 or y0 >= y1:
                continue
            h_window = h_mask[y0:y1, x0:x1]
            v_window = v_mask[y0:y1, x0:x1]
            intersection_window = cv2.bitwise_and(
                cv2.dilate(h_window, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)), iterations=1),
                cv2.dilate(v_window, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5)), iterations=1),
            )
            if int(np.count_nonzero(intersection_window)) == 0:
                continue
            points.append(
                {
                    "id": f"T{y_index + 1}-{x_index + 1}",
                    "x": float(x),
                    "y": float(y),
                    "x_index": x_index,
                    "y_index": y_index,
                }
            )
    return points


def _cluster_axis_values(points: list[dict[str, Any]], axis: str, tolerance_px: float = 18.0) -> list[dict[str, Any]]:
    indexed_values = sorted(
        [(index, float(point[axis])) for index, point in enumerate(points)],
        key=lambda item: item[1],
    )
    clusters: list[list[tuple[int, float]]] = []
    for index, value in indexed_values:
        if not clusters:
            clusters.append([(index, value)])
            continue
        center = float(np.median([item[1] for item in clusters[-1]]))
        if abs(value - center) <= tolerance_px:
            clusters[-1].append((index, value))
        else:
            clusters.append([(index, value)])

    result: list[dict[str, Any]] = []
    for cluster_index, cluster in enumerate(clusters):
        values = [item[1] for item in cluster]
        result.append(
            {
                "cluster_index": cluster_index,
                "value": round(float(np.median(values)), 2),
                "count": len(cluster),
                "point_indexes": [item[0] for item in cluster],
            }
        )
    return result


def _assign_point_clusters(
    *,
    fax_points: list[dict[str, Any]],
    x_clusters: list[dict[str, Any]],
    y_clusters: list[dict[str, Any]],
) -> dict[tuple[int, int], dict[str, Any]]:
    point_to_x: dict[int, int] = {}
    point_to_y: dict[int, int] = {}
    for cluster in x_clusters:
        for point_index in cluster["point_indexes"]:
            point_to_x[int(point_index)] = int(cluster["cluster_index"])
    for cluster in y_clusters:
        for point_index in cluster["point_indexes"]:
            point_to_y[int(point_index)] = int(cluster["cluster_index"])

    by_axis: dict[tuple[int, int], dict[str, Any]] = {}
    for point_index, point in enumerate(fax_points):
        if point_index not in point_to_x or point_index not in point_to_y:
            continue
        key = (point_to_x[point_index], point_to_y[point_index])
        current = by_axis.get(key)
        if current is None or int(point.get("area") or 0) > int(current.get("area") or 0):
            by_axis[key] = point
            by_axis[key]["fax_point_index"] = point_index
            by_axis[key]["x_cluster_index"] = key[0]
            by_axis[key]["y_cluster_index"] = key[1]
    return by_axis


def _filter_x_clusters_by_y_coverage(
    *,
    x_clusters: list[dict[str, Any]],
    y_clusters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    point_to_y: dict[int, int] = {}
    for cluster in y_clusters:
        for point_index in cluster["point_indexes"]:
            point_to_y[int(point_index)] = int(cluster["cluster_index"])

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for cluster in x_clusters:
        y_cluster_indexes = sorted({point_to_y[int(point_index)] for point_index in cluster["point_indexes"] if int(point_index) in point_to_y})
        enriched = dict(cluster)
        enriched["raw_cluster_index"] = int(cluster["cluster_index"])
        enriched["covered_y_cluster_indexes"] = y_cluster_indexes
        if len(y_cluster_indexes) >= 2:
            enriched["cluster_index"] = len(accepted)
            accepted.append(enriched)
        else:
            enriched["reject_reason"] = "x_cluster_touches_less_than_two_header_y_levels"
            rejected.append(enriched)
    return accepted, rejected


def _align_axis_by_structure(
    *,
    template_values: list[int],
    fax_clusters: list[dict[str, Any]],
    axis: str,
) -> dict[str, Any]:
    template_count = len(template_values)
    fax_count = len(fax_clusters)
    if template_count != fax_count:
        return {
            "axis": axis,
            "status": "count_mismatch",
            "template_count": template_count,
            "fax_count": fax_count,
            "mapping": {},
            "unmatched_template_indexes": list(range(template_count)),
            "unmatched_fax_cluster_indexes": [int(cluster["cluster_index"]) for cluster in fax_clusters],
        }

    if axis == "x":
        template_order = list(reversed(range(template_count)))
        fax_order = list(reversed(range(fax_count)))
    else:
        template_order = list(range(template_count))
        fax_order = list(range(fax_count))
    mapping = {template_index: fax_index for template_index, fax_index in zip(template_order, fax_order)}
    return {
        "axis": axis,
        "status": "matched_by_order",
        "template_count": template_count,
        "fax_count": fax_count,
        "mapping": mapping,
        "unmatched_template_indexes": [],
        "unmatched_fax_cluster_indexes": [],
    }


def _match_template_to_fax_intersections(
    *,
    template_points: list[dict[str, Any]],
    fax_points: list[dict[str, Any]],
    template_xs: list[int],
    template_header_ys: list[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_x_clusters = _cluster_axis_values(fax_points, "x")
    y_clusters = _cluster_axis_values(fax_points, "y")
    filtered_x_clusters, rejected_x_clusters = _filter_x_clusters_by_y_coverage(x_clusters=raw_x_clusters, y_clusters=y_clusters)
    if len(raw_x_clusters) > len(template_xs) and len(filtered_x_clusters) == len(template_xs):
        x_clusters = filtered_x_clusters
        x_cluster_source = "filtered_extra_x_clusters_by_header_y_coverage"
    elif len(raw_x_clusters) > len(template_xs) and len(template_xs) >= 5:
        fixed_left_count = 4
        left_clusters = [dict(cluster) for cluster in raw_x_clusters[:fixed_left_count]]
        rest_filtered, rest_rejected = _filter_x_clusters_by_y_coverage(
            x_clusters=raw_x_clusters[fixed_left_count:],
            y_clusters=y_clusters,
        )
        left_and_filtered = left_clusters + rest_filtered
        if len(left_and_filtered) == len(template_xs):
            x_clusters = []
            for cluster in left_and_filtered:
                enriched = dict(cluster)
                enriched["cluster_index"] = len(x_clusters)
                enriched.setdefault("raw_cluster_index", int(cluster["cluster_index"]))
                x_clusters.append(enriched)
            rejected_x_clusters = rest_rejected
            x_cluster_source = "fixed_left_four_then_filtered_extra_x_clusters_by_header_y_coverage"
        else:
            x_clusters = raw_x_clusters
            rejected_x_clusters = []
            x_cluster_source = "raw_x_clusters"
    else:
        x_clusters = raw_x_clusters
        rejected_x_clusters = []
        x_cluster_source = "raw_x_clusters"
    x_alignment = _align_axis_by_structure(template_values=template_xs, fax_clusters=x_clusters, axis="x")
    y_alignment = _align_axis_by_structure(template_values=template_header_ys, fax_clusters=y_clusters, axis="y")
    point_by_axis = _assign_point_clusters(fax_points=fax_points, x_clusters=x_clusters, y_clusters=y_clusters)

    matches: list[dict[str, Any]] = []
    used_fax: set[int] = set()
    both_axes_matched = x_alignment["status"] == "matched_by_order" and y_alignment["status"] == "matched_by_order"
    for template in template_points:
        if not both_axes_matched:
            matches.append(
                {
                    "template": template,
                    "fax": None,
                    "distance": None,
                    "status": "unresolved_axis_count_mismatch",
                }
            )
            continue
        fax_x_index = int(x_alignment["mapping"][int(template["x_index"])])
        fax_y_index = int(y_alignment["mapping"][int(template["y_index"])])
        fax = point_by_axis.get((fax_x_index, fax_y_index))
        if fax is None:
            matches.append(
                {
                    "template": template,
                    "fax": None,
                    "distance": None,
                    "status": "missing_structural_intersection",
                    "fax_axis_key": [fax_x_index, fax_y_index],
                }
            )
            continue
        fax_index = int(fax.get("fax_point_index") or 0)
        used_fax.add(fax_index)
        distance = float(((float(fax["x"]) - float(template["x"])) ** 2 + (float(fax["y"]) - float(template["y"])) ** 2) ** 0.5)
        matches.append(
            {
                "template": template,
                "fax": fax,
                "distance": round(distance, 2),
                "status": "matched_by_structural_axis_order",
                "fax_axis_key": [fax_x_index, fax_y_index],
            }
        )
    for fax_index, fax in enumerate(fax_points):
        if fax_index not in used_fax:
            matches.append(
                {
                    "template": None,
                    "fax": fax,
                    "distance": None,
                    "status": "extra_fax",
                }
            )
    evidence = {
        "matching_method": "structural_axis_order_no_nearest_neighbor",
        "status": "both_axes_matched" if both_axes_matched else "axis_count_mismatch_unresolved",
        "template_x_count": len(template_xs),
        "template_header_y_count": len(template_header_ys),
        "raw_fax_x_cluster_count": len(raw_x_clusters),
        "fax_x_cluster_count": len(x_clusters),
        "fax_x_cluster_source": x_cluster_source,
        "fax_y_cluster_count": len(y_clusters),
        "raw_fax_x_clusters": raw_x_clusters,
        "fax_x_clusters": x_clusters,
        "rejected_fax_x_clusters": rejected_x_clusters,
        "fax_y_clusters": y_clusters,
        "x_alignment": x_alignment,
        "y_alignment": y_alignment,
    }
    return matches, evidence


def _matched_status(status: str | None) -> bool:
    return status == "matched_by_structural_axis_order"


def _build_header_axis_correction(
    *,
    template_xs: list[int],
    template_header_ys: list[int],
    matches: list[dict[str, Any]],
    evidence: dict[str, Any],
    table_bbox: list[int],
) -> dict[str, Any]:
    x_samples: dict[int, list[float]] = {index: [] for index in range(len(template_xs))}
    y_samples: dict[int, list[float]] = {index: [] for index in range(len(template_header_ys))}
    for match in matches:
        if not _matched_status(match.get("status")):
            continue
        template = match.get("template") or {}
        fax = match.get("fax") or {}
        x_index = int(template.get("x_index"))
        y_index = int(template.get("y_index"))
        if x_index in x_samples:
            x_samples[x_index].append(float(fax.get("x")))
        if y_index in y_samples:
            y_samples[y_index].append(float(fax.get("y")))

    x_anchors: list[dict[str, float | int]] = []
    y_anchors: list[dict[str, float | int]] = []
    left_x, top_y, right_x, _bottom_y = [float(value) for value in table_bbox]
    last_x_index = len(template_xs) - 1
    x_alignment = evidence.get("x_alignment") or {}
    x_mapping = x_alignment.get("mapping") or {}
    fax_x_clusters = evidence.get("fax_x_clusters") or []
    y_alignment = evidence.get("y_alignment") or {}
    y_mapping = y_alignment.get("mapping") or {}
    fax_y_clusters = evidence.get("fax_y_clusters") or []
    for index, template_x in enumerate(template_xs):
        samples = x_samples.get(index) or []
        mapped_cluster: dict[str, Any] | None = None
        if x_alignment.get("status") == "matched_by_order" and index in x_mapping:
            mapped_index = int(x_mapping[index])
            mapped_cluster = next((cluster for cluster in fax_x_clusters if int(cluster["cluster_index"]) == mapped_index), None)
        if index == 0:
            x_anchors.append(
                {
                    "index": index,
                    "template": float(template_x),
                    "fax": left_x,
                    "source": "fixed_outer_left_pink_bbox",
                }
            )
        elif index == last_x_index:
            x_anchors.append(
                {
                    "index": index,
                    "template": float(template_x),
                    "fax": right_x,
                    "source": "fixed_outer_right_pink_bbox",
                }
            )
        elif mapped_cluster is not None:
            x_anchors.append(
                {
                    "index": index,
                    "template": float(template_x),
                    "fax": round(float(mapped_cluster["value"]), 2),
                    "source": "matched_x_axis_cluster_order",
                }
            )
        elif samples:
            x_anchors.append(
                {
                    "index": index,
                    "template": float(template_x),
                    "fax": round(float(np.median(samples)), 2),
                    "source": "matched_header_intersections",
                }
            )
    for index, template_y in enumerate(template_header_ys):
        samples = y_samples.get(index) or []
        mapped_cluster = None
        if y_alignment.get("status") == "matched_by_order" and index in y_mapping:
            mapped_index = int(y_mapping[index])
            mapped_cluster = next((cluster for cluster in fax_y_clusters if int(cluster["cluster_index"]) == mapped_index), None)
        if index == 0:
            y_anchors.append(
                {
                    "index": index,
                    "template": float(template_y),
                    "fax": top_y,
                    "source": "fixed_outer_top_pink_bbox",
                }
            )
        elif mapped_cluster is not None:
            y_anchors.append(
                {
                    "index": index,
                    "template": float(template_y),
                    "fax": round(float(mapped_cluster["value"]), 2),
                    "source": "matched_y_axis_cluster_order",
                }
            )
        elif samples:
            y_anchors.append(
                {
                    "index": index,
                    "template": float(template_y),
                    "fax": round(float(np.median(samples)), 2),
                    "source": "matched_header_intersections",
                }
            )

    corrected_xs: list[float | None] = [_piecewise_map(float(template_x), x_anchors) for template_x in template_xs]
    corrected_ys: list[float | None] = [_piecewise_map(float(template_y), y_anchors) for template_y in template_header_ys]
    x_offsets: list[float] = []
    y_offsets: list[float] = []
    for index, corrected in enumerate(corrected_xs):
        if corrected is not None:
            x_offsets.append(float(corrected) - float(template_xs[index]))
    for index, corrected in enumerate(corrected_ys):
        if corrected is not None:
            y_offsets.append(float(corrected) - float(template_header_ys[index]))

    return {
        "correction_method": "piecewise_linear_template_to_fax_from_structural_intersection_matches",
        "scope": "header_only_with_outer_pink_bbox_contact_points_fixed",
        "fixed_outer_bbox": table_bbox,
        "template_xs": template_xs,
        "template_header_ys": template_header_ys,
        "x_anchors": x_anchors,
        "y_anchors": y_anchors,
        "corrected_header_xs": corrected_xs,
        "corrected_header_ys": corrected_ys,
        "x_offsets_px": [None if value is None else round(float(value) - float(template_xs[index]), 2) for index, value in enumerate(corrected_xs)],
        "y_offsets_px": [None if value is None else round(float(value) - float(template_header_ys[index]), 2) for index, value in enumerate(corrected_ys)],
        "median_abs_x_offset_px": None if not x_offsets else round(float(np.median(np.abs(x_offsets))), 2),
        "median_abs_y_offset_px": None if not y_offsets else round(float(np.median(np.abs(y_offsets))), 2),
        "max_abs_x_offset_px": None if not x_offsets else round(float(np.max(np.abs(x_offsets))), 2),
        "max_abs_y_offset_px": None if not y_offsets else round(float(np.max(np.abs(y_offsets))), 2),
        "matched_x_axis_count": sum(1 for value in corrected_xs if value is not None),
        "matched_y_axis_count": sum(1 for value in corrected_ys if value is not None),
    }


def _cluster_intersections(mask: np.ndarray) -> list[dict[str, Any]]:
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    points: list[dict[str, Any]] = []
    for label in range(1, count):
        x, y, w, h, area = [int(value) for value in stats[label]]
        if area < 4:
            continue
        cx, cy = centroids[label]
        points.append(
            {
                "x": round(float(cx), 2),
                "y": round(float(cy), 2),
                "bbox": [x, y, w, h],
                "area": area,
            }
        )
    return sorted(points, key=lambda item: (float(item["y"]), float(item["x"])))


def detect_header_intersections(
    rectified_bgr: np.ndarray,
    *,
    table_bbox: list[int],
    template_ys: list[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    h_mask, v_mask = _split_line_masks(rectified_bgr)
    x0, y0, x1, y1 = _header_bounds(table_bbox, template_ys)
    h = h_mask[y0:y1, x0:x1]
    v = v_mask[y0:y1, x0:x1]
    if h.size == 0 or v.size == 0:
        return [], {"reason": "empty_header_roi", "header_roi": [x0, y0, x1, y1]}

    h_dilated = cv2.dilate(h, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 5)), iterations=1)
    v_dilated = cv2.dilate(v, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 7)), iterations=1)
    intersections = cv2.bitwise_and(h_dilated, v_dilated)
    points = _cluster_intersections(intersections)
    for point in points:
        point["x"] = round(float(point["x"]) + x0, 2)
        point["y"] = round(float(point["y"]) + y0, 2)
        bx, by, bw, bh = point["bbox"]
        point["bbox"] = [int(bx + x0), int(by + y0), int(bw), int(bh)]
    return points, {
        "header_roi": [x0, y0, x1, y1],
        "horizontal_pixels": int(np.count_nonzero(h)),
        "vertical_pixels": int(np.count_nonzero(v)),
        "intersection_count": len(points),
    }


def _draw_header_intersections(
    rectified_bgr: np.ndarray,
    *,
    points: list[dict[str, Any]],
    template_points: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    evidence: dict[str, Any],
    quad_points: list[tuple[float, float]],
) -> Image.Image:
    image = _bgr_to_rgb_image(rectified_bgr).convert("RGBA")
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.load_default()
    x0, y0, x1, y1 = [int(value) for value in evidence.get("header_roi") or [0, 0, 0, 0]]
    draw.rectangle((x0, y0, x1, y1), outline=(255, 128, 0, 230), width=5)
    for cluster in evidence.get("fax_x_clusters") or []:
        x = float(cluster["value"])
        draw.line((x, y0, x, y1), fill=(255, 0, 0, 90), width=2)
        draw.text((x + 4, y0 + 4), f"FX{int(cluster['cluster_index']) + 1}", fill=(180, 0, 0, 230), font=font)
    for cluster in evidence.get("fax_y_clusters") or []:
        y = float(cluster["value"])
        draw.line((x0, y, x1, y), fill=(255, 96, 0, 90), width=2)
        draw.text((x0 + 6, y + 4), f"FY{int(cluster['cluster_index']) + 1}", fill=(180, 70, 0, 230), font=font)
    for template in template_points:
        x = float(template["x"])
        y = float(template["y"])
        draw.rectangle((x - 7, y - 7, x + 7, y + 7), outline=(0, 80, 255, 230), width=3)
    for index, point in enumerate(points, start=1):
        x = float(point["x"])
        y = float(point["y"])
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline=(255, 0, 0, 240), width=4)
        draw.line((x - 14, y, x + 14, y), fill=(255, 0, 0, 220), width=3)
        draw.line((x, y - 14, x, y + 14), fill=(255, 0, 0, 220), width=3)
        draw.text((x + 10, y - 16), str(index), fill=(255, 0, 0, 240), font=font)
    for match in matches:
        if not _matched_status(match.get("status")):
            continue
        template = match.get("template") or {}
        fax = match.get("fax") or {}
        tx = float(template.get("x") or 0)
        ty = float(template.get("y") or 0)
        fx = float(fax.get("x") or 0)
        fy = float(fax.get("y") or 0)
        draw.line((tx, ty, fx, fy), fill=(0, 170, 0, 180), width=2)
    composed = Image.alpha_composite(image, layer).convert("RGB")
    return _draw_quad_points(composed, quad_points, prefix="Q")


def _draw_header_axis_correction(
    rectified_bgr: np.ndarray,
    *,
    evidence: dict[str, Any],
    correction: dict[str, Any],
    template_segments: dict[str, list[dict[str, Any]]],
    quad_points: list[tuple[float, float]],
) -> Image.Image:
    image = _bgr_to_rgb_image(rectified_bgr).convert("RGBA")
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    roi_x0, roi_y0, roi_x1, roi_y1 = [int(value) for value in evidence.get("header_roi") or [0, 0, 0, 0]]
    bbox_x0, bbox_y0, bbox_x1, _bbox_y1 = [int(round(float(value))) for value in correction.get("fixed_outer_bbox") or [roi_x0, roi_y0, roi_x1, roi_y1]]
    draw.rectangle((roi_x0, roi_y0, roi_x1, roi_y1), outline=(255, 128, 0, 80), width=2)

    x_anchors = correction.get("x_anchors") or []
    y_anchors = correction.get("y_anchors") or []

    def mapped_point(x: float, y: float) -> tuple[float, float] | None:
        mapped_x = _piecewise_map(float(x), x_anchors)
        mapped_y = _piecewise_map(float(y), y_anchors)
        if mapped_x is None or mapped_y is None:
            return None
        return float(mapped_x), float(mapped_y)

    for segment in template_segments.get("horizontal") or []:
        start = mapped_point(float(segment["x1"]), float(segment["y1"]))
        end = mapped_point(float(segment["x2"]), float(segment["y2"]))
        if start is None or end is None:
            continue
        draw.line((start[0], start[1], end[0], end[1]), fill=(0, 210, 0, 235), width=4)
    for segment in template_segments.get("vertical") or []:
        start = mapped_point(float(segment["x1"]), float(segment["y1"]))
        end = mapped_point(float(segment["x2"]), float(segment["y2"]))
        if start is None or end is None:
            continue
        draw.line((start[0], start[1], end[0], end[1]), fill=(0, 210, 0, 235), width=4)

    composed = Image.alpha_composite(image, layer).convert("RGB")
    return _draw_quad_points(composed, quad_points, prefix="Q")


def _crop_header_debug_image(image: Image.Image, *, evidence: dict[str, Any], correction: dict[str, Any]) -> Image.Image:
    roi_x0, roi_y0, roi_x1, roi_y1 = [int(value) for value in evidence.get("header_roi") or [0, 0, image.width, image.height]]
    bbox = correction.get("fixed_outer_bbox") or [roi_x0, roi_y0, roi_x1, roi_y1]
    bbox_x0, bbox_y0, bbox_x1, _bbox_y1 = [int(round(float(value))) for value in bbox]
    pad_x = 48
    pad_top = 28
    pad_bottom = 140
    left = max(0, min(roi_x0, bbox_x0) - pad_x)
    top = max(0, min(roi_y0, bbox_y0) - pad_top)
    right = min(image.width, max(roi_x1, bbox_x1) + pad_x)
    bottom = min(image.height, max(roi_y1, bbox_y0) + pad_bottom)
    return image.crop((left, top, right, bottom))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fax-pdf", required=True, type=Path)
    parser.add_argument("--template-pdf", required=True, type=Path)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--facility-code", default="FAC00008")
    parser.add_argument("--order-id", default="ORDab6c77ff")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--render-width", default=1864, type=int)
    args = parser.parse_args()

    template_xs, template_ys, manifest_bbox = _load_template_axes_from_summary(args.summary_json)
    registration, images = build_fixed_quad_template_registration(
        facility_code=args.facility_code,
        order_id=args.order_id,
        fax_pdf=str(args.fax_pdf),
        template_pdf=str(args.template_pdf),
        quad_px=None,
        manifest_template_bbox=manifest_bbox,
        canvas_width=2362,
        canvas_height=4273,
        render_width=args.render_width,
        quad_source=None,
        output_dir=None,
        template_axes_x=template_xs,
        template_axes_y=template_ys,
    )
    rectified = images["step2"]
    template_bgr = render_template_pdf_to_canvas(
        str(args.template_pdf),
        width=int(registration.rectified_canvas_size[0]),
        height=int(registration.rectified_canvas_size[1]),
    )
    table_bbox = [int(value) for value in registration.template_outer_grid_bbox_used]
    ys = [int(value) for value in registration.template_axes_y_all]
    quad_points = _bbox_quad_points(table_bbox)
    points, evidence = detect_header_intersections(rectified, table_bbox=table_bbox, template_ys=ys)
    template_points = _template_header_intersections(
        template_xs=[int(value) for value in registration.template_axes_x],
        template_ys=ys,
        template_bgr=template_bgr,
    )
    template_segments = _template_header_segments(
        template_bgr=template_bgr,
        table_bbox=table_bbox,
        template_ys=ys,
        template_points=template_points,
    )
    header_template_xs = sorted(int(value) for value in registration.template_axes_x)
    header_template_ys = sorted(int(value) for value in ys[:3])
    matches, match_evidence = _match_template_to_fax_intersections(
        template_points=template_points,
        fax_points=points,
        template_xs=header_template_xs,
        template_header_ys=header_template_ys,
    )
    evidence.update(match_evidence)
    correction = _build_header_axis_correction(
        template_xs=header_template_xs,
        template_header_ys=header_template_ys,
        matches=matches,
        evidence=evidence,
        table_bbox=table_bbox,
    )
    evidence["header_axis_correction"] = correction

    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = args.output_dir / f"{args.facility_code}_{args.order_id}_header_intersections.json"
    evidence_path.write_text(
        json.dumps(
            {
                "facility_code": args.facility_code,
                "order_id": args.order_id,
                "quad_px": registration.quad_px,
                "rectified_canvas_size": registration.rectified_canvas_size,
                "template_outer_grid_bbox_used": registration.template_outer_grid_bbox_used,
                "template_axes_x": registration.template_axes_x,
                "template_axes_y_header": ys[:4],
                "evidence": evidence,
                "points": points,
                "template_points": template_points,
                "template_segments": template_segments,
                "matches": matches,
                "header_axis_correction": correction,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    page1 = _make_review_canvas(
        title="Header intersection debug STEP1: original FAX + detected 4 points",
        facility_code=args.facility_code,
        order_id=args.order_id,
        image=_bgr_to_rgb_image(images["step1"]),
        details=[
            f"quad_source={registration.quad_source}",
            f"quad_px={registration.quad_px}",
        ],
        target_width=1800,
    )
    header_overlay = _draw_header_intersections(
        rectified,
        points=points,
        template_points=template_points,
        matches=matches,
        evidence=evidence,
        quad_points=quad_points,
    )
    page2 = _make_review_canvas(
        title="Header intersection debug STEP2: rectified FAX + detected header intersections",
        facility_code=args.facility_code,
        order_id=args.order_id,
        image=header_overlay,
        details=[
            "orange=header ROI, red crosses=detected FAX intersections, red/orange faint lines=FAX axis clusters, blue squares=template intersections, green lines=structural matches",
            f"method={evidence.get('matching_method')} status={evidence.get('status')}",
            f"header_roi={evidence.get('header_roi')} fax_intersections={len(points)} template_intersections={len(template_points)} matched={sum(1 for item in matches if _matched_status(item.get('status')))}",
            f"template_x={evidence.get('template_x_count')} fax_x={evidence.get('fax_x_cluster_count')} template_y={evidence.get('template_header_y_count')} fax_y={evidence.get('fax_y_cluster_count')}",
            f"evidence_json={evidence_path}",
        ],
        target_width=1800,
    )
    corrected_overlay = _draw_header_axis_correction(
        rectified,
        evidence=evidence,
        correction=correction,
        template_segments=template_segments,
        quad_points=quad_points,
    )
    page3 = _make_review_canvas(
        title="Header intersection debug STEP3: template header axes corrected by structural matches",
        facility_code=args.facility_code,
        order_id=args.order_id,
        image=corrected_overlay,
        details=[
            "green=real template header line segments mapped by structural FAX intersections, orange=header ROI",
            f"scope={correction.get('scope')} method={correction.get('correction_method')}",
            f"matched_x_axis={correction.get('matched_x_axis_count')}/{len(header_template_xs)} matched_y_axis={correction.get('matched_y_axis_count')}/{len(header_template_ys)}",
            f"median_abs_x_offset={correction.get('median_abs_x_offset_px')} max_abs_x_offset={correction.get('max_abs_x_offset_px')} median_abs_y_offset={correction.get('median_abs_y_offset_px')} max_abs_y_offset={correction.get('max_abs_y_offset_px')}",
            f"template_segments_h={len(template_segments.get('horizontal') or [])} template_segments_v={len(template_segments.get('vertical') or [])}",
        ],
        target_width=1800,
    )
    pdf_path = args.output_dir / f"{args.facility_code}_{args.order_id}_header_intersections.pdf"
    _write_pdf_from_pages([page1, page2, page3], pdf_path)
    png_path = args.output_dir / f"{args.facility_code}_{args.order_id}_header_intersections.png"
    header_overlay.save(png_path)
    corrected_png_path = args.output_dir / f"{args.facility_code}_{args.order_id}_header_axis_correction.png"
    corrected_overlay.save(corrected_png_path)
    header_crop = _crop_header_debug_image(corrected_overlay, evidence=evidence, correction=correction)
    header_crop_png_path = args.output_dir / f"{args.facility_code}_{args.order_id}_header_axis_correction_header_crop.png"
    header_crop.save(header_crop_png_path)
    print(
        json.dumps(
            {
                "pdf": str(pdf_path),
                "png": str(png_path),
                "corrected_png": str(corrected_png_path),
                "header_crop_png": str(header_crop_png_path),
                "json": str(evidence_path),
                "points": len(points),
                "status": evidence.get("status"),
                "matched": sum(1 for item in matches if _matched_status(item.get("status"))),
                "header_axis_correction": {
                    "matched_x_axis": correction.get("matched_x_axis_count"),
                    "matched_y_axis": correction.get("matched_y_axis_count"),
                    "median_abs_x_offset_px": correction.get("median_abs_x_offset_px"),
                    "median_abs_y_offset_px": correction.get("median_abs_y_offset_px"),
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
