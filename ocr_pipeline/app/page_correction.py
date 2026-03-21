from __future__ import annotations

from io import BytesIO
import os
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image

from app.pdf_render import render_pdf_to_page_images
from app.preprocess import build_images_for_match_and_ocr
from app.template_match import choose_template_and_warp


_RIGHT_ANGLE_ROTATIONS = (0, 90, 180, 270)


def _empty_correction_summary(*, enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "applied": False,
        "document_rotation_deg": 0,
        "applied_page_count": 0,
        "template_warp_page_count": 0,
        "deskew_page_count": 0,
        "position_normalized_page_count": 0,
        "corrected_page_count": 0,
        "corrected_pdf_generated": False,
        "corrected_pdf_changed": False,
        "corrected_pdf_byte_length": 0,
        "corrected_pdf_uploaded": False,
        "corrected_pdf_uri": None,
        "pages": [],
    }


def _read_bool_env(name: str, default: bool) -> bool:
    value = str(os.environ.get(name, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _read_float_env(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _read_int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _ensure_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _document_binary(image: np.ndarray) -> np.ndarray:
    gray = _ensure_gray(image)
    denoised = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
    return cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]


def _encode_png(image_bgr: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image_bgr)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return encoded.tobytes()


def _rotate_right_angle(image_bgr: np.ndarray, degrees: int) -> np.ndarray:
    normalized = int(degrees) % 360
    if normalized == 0:
        return image_bgr.copy()
    if normalized == 90:
        return cv2.rotate(image_bgr, cv2.ROTATE_90_CLOCKWISE)
    if normalized == 180:
        return cv2.rotate(image_bgr, cv2.ROTATE_180)
    if normalized == 270:
        return cv2.rotate(image_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"unsupported right-angle rotation: {degrees}")


def _normalize_bbox(
    bbox: tuple[int, int, int, int] | None,
    *,
    width: int,
    height: int,
) -> list[float] | None:
    if bbox is None or width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = bbox
    return [
        round(max(0.0, min(1.0, float(x0) / float(width))), 6),
        round(max(0.0, min(1.0, float(y0) / float(height))), 6),
        round(max(0.0, min(1.0, float(x1) / float(width))), 6),
        round(max(0.0, min(1.0, float(y1) / float(height))), 6),
    ]


def _content_bbox(image_bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    binary = _document_binary(image_bgr)
    points = cv2.findNonZero(binary)
    if points is None:
        return None
    x, y, width, height = cv2.boundingRect(points)
    if width <= 0 or height <= 0:
        return None
    return int(x), int(y), int(x + width), int(y + height)


def _foreground_bbox(image_bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    bbox = _content_bbox(image_bgr)
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def _candidate_score(diagnostics: dict[str, Any] | None, matched_template_id: str | None) -> float:
    if not matched_template_id:
        return -1.0
    if not isinstance(diagnostics, dict):
        return 1.0
    candidates = diagnostics.get("candidates")
    if not isinstance(candidates, list):
        raw_score = diagnostics.get("score")
        if raw_score is not None:
            try:
                return float(raw_score)
            except Exception:
                pass
        return 1.0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("id") or "").strip() != matched_template_id:
            continue
        if str(candidate.get("status") or "").strip() != "matched":
            continue
        try:
            return float(candidate.get("score") or 0.0)
        except Exception:
            return -1.0
    return -1.0


def _save_pdf(images_bgr: Iterable[np.ndarray]) -> bytes:
    pil_images = [Image.fromarray(image[:, :, ::-1]) for image in images_bgr]
    if not pil_images:
        return b""
    buffer = BytesIO()
    first, rest = pil_images[0], pil_images[1:]
    # Keep corrected PDF page dimensions close to the original document size instead of
    # treating raw pixel dimensions as PDF points. This avoids enormous page geometries
    # that later explode memory when any downstream code re-rasterizes the corrected PDF.
    first.save(buffer, format="PDF", save_all=True, append_images=rest, resolution=300.0)
    return buffer.getvalue()


def _try_template_warp(
    *,
    db: Any,
    page_image_bgr: np.ndarray,
    template_ids: list[str] | None,
    preferred_template_id: str | None = None,
) -> tuple[np.ndarray | None, dict[str, Any] | None]:
    if not template_ids:
        return None, None
    preferred_token = str(preferred_template_id or "").strip() or None
    best_corrected = None
    best_diag = None
    best_score = -1.0
    for rotation_deg in _RIGHT_ANGLE_ROTATIONS:
        rotated = _rotate_right_angle(page_image_bgr, rotation_deg)
        match_bgr, ocr_bgr, _ocr_keep_lines_bgr = build_images_for_match_and_ocr(_encode_png(rotated))
        matched_template_id, warped_match, warped_ocr, warped_alt, diagnostics = choose_template_and_warp(
            db,
            match_bgr,
            ocr_bgr,
            img_alt_bgr=rotated,
            template_ids=template_ids,
        )
        if preferred_token and str(matched_template_id or "").strip() != preferred_token:
            continue
        score = _candidate_score(diagnostics, matched_template_id)
        corrected = warped_ocr if warped_ocr is not None else warped_alt
        if score <= best_score or warped_match is None or corrected is None:
            continue
        best_score = score
        best_corrected = corrected
        best_diag = {
            "mode": "template_warp",
            "template_id": matched_template_id,
            "template_score": round(score, 4),
            "right_angle_rotation_deg": rotation_deg,
            "deskew_angle_deg": 0.0,
            "deskew_applied": False,
            "perspective_applied": True,
            "position_normalized": True,
            "content_bbox_norm": _normalize_bbox(
                _content_bbox(corrected),
                width=corrected.shape[1],
                height=corrected.shape[0],
            ),
            "translation_px": [0, 0],
            "warnings": [],
            "diagnostics": diagnostics if isinstance(diagnostics, dict) else {},
        }
    return best_corrected, best_diag


def _normalize_axis_angle(angle_deg: float) -> float:
    normalized = float(angle_deg)
    while normalized <= -90.0:
        normalized += 180.0
    while normalized > 90.0:
        normalized -= 180.0
    if normalized > 45.0:
        normalized -= 90.0
    elif normalized < -45.0:
        normalized += 90.0
    return normalized


def _estimate_skew_angle_deg(image_bgr: np.ndarray) -> tuple[float, dict[str, Any]]:
    binary = _document_binary(image_bgr)
    height, width = binary.shape[:2]
    min_line_length = max(80, int(min(width, height) * 0.25))
    threshold = max(40, int(min(width, height) * 0.08))
    lines = cv2.HoughLinesP(
        binary,
        1,
        np.pi / 180.0,
        threshold=threshold,
        minLineLength=min_line_length,
        maxLineGap=20,
    )
    normalized_angles: list[float] = []
    if lines is not None:
        for item in lines:
            x0, y0, x1, y1 = item[0]
            angle = float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))
            deviation = _normalize_axis_angle(angle)
            if abs(deviation) <= max(1.0, _read_float_env("OCR_PAGE_CORRECTION_MAX_SKEW_DEG", 8.0)):
                normalized_angles.append(deviation)
    diagnostics = {
        "line_count": len(normalized_angles),
        "min_line_length": min_line_length,
        "hough_threshold": threshold,
    }
    min_lines = max(2, _read_int_env("OCR_PAGE_CORRECTION_MIN_SKEW_LINES", 4))
    if len(normalized_angles) < min_lines:
        return 0.0, diagnostics
    angle = float(np.median(np.array(normalized_angles, dtype=np.float32)))
    min_abs_angle = max(0.1, _read_float_env("OCR_PAGE_CORRECTION_MIN_ABS_SKEW_DEG", 0.3))
    if abs(angle) < min_abs_angle:
        return 0.0, diagnostics
    return angle, diagnostics


def _rotate_small_angle(image_bgr: np.ndarray, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) <= 1e-6:
        return image_bgr.copy()
    height, width = image_bgr.shape[:2]
    center = (float(width) / 2.0, float(height) / 2.0)
    matrix = cv2.getRotationMatrix2D(center, float(angle_deg), 1.0)
    return cv2.warpAffine(
        image_bgr,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def estimate_rotation_angle(
    image_bgr: np.ndarray,
    *,
    max_abs_degrees: float = 8.0,
) -> dict[str, Any]:
    angle_deg, diagnostics = _estimate_skew_angle_deg(image_bgr)
    diagnostics = dict(diagnostics)
    diagnostics["angle_deg"] = float(angle_deg)
    diagnostics["confidence"] = round(
        min(
            1.0,
            float(diagnostics.get("line_count") or 0)
            / float(max(_read_int_env("OCR_PAGE_CORRECTION_MIN_SKEW_LINES", 4), 1)),
        ),
        4,
    )
    if abs(angle_deg) > float(max_abs_degrees):
        diagnostics["skipped_reason"] = "above_max_abs_degrees"
    return diagnostics


def correct_rotation(
    image_bgr: np.ndarray,
    *,
    max_abs_degrees: float = 8.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    diagnostics = estimate_rotation_angle(image_bgr, max_abs_degrees=max_abs_degrees)
    angle_deg = float(diagnostics.get("angle_deg") or 0.0)
    if abs(angle_deg) <= max(0.1, _read_float_env("OCR_PAGE_CORRECTION_MIN_ABS_SKEW_DEG", 0.3)):
        diagnostics["applied"] = False
        diagnostics["skipped_reason"] = "below_min_abs_angle"
        return image_bgr.copy(), diagnostics
    if abs(angle_deg) > float(max_abs_degrees):
        diagnostics["applied"] = False
        diagnostics["skipped_reason"] = "above_max_abs_degrees"
        return image_bgr.copy(), diagnostics
    corrected = _rotate_small_angle(image_bgr, angle_deg)
    diagnostics["applied"] = True
    diagnostics["applied_angle_deg"] = round(float(angle_deg), 4)
    diagnostics["output_size"] = [int(corrected.shape[1]), int(corrected.shape[0])]
    return corrected, diagnostics


def _order_quad_points(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1)
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(sums)]
    ordered[2] = pts[np.argmax(sums)]
    ordered[1] = pts[np.argmin(diffs)]
    ordered[3] = pts[np.argmax(diffs)]
    return ordered


def detect_document_quad(
    image_bgr: np.ndarray,
    *,
    min_area_ratio: float = 0.35,
    max_area_ratio: float = 0.98,
    corner_tolerance_ratio: float = 0.18,
) -> dict[str, Any]:
    gray = _ensure_gray(image_bgr)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 60, 180)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_h, image_w = gray.shape[:2]
    image_area = float(max(image_h * image_w, 1))
    tolerance = float(min(image_h, image_w)) * float(corner_tolerance_ratio)
    corners = np.array(
        [[0.0, 0.0], [image_w - 1.0, 0.0], [image_w - 1.0, image_h - 1.0], [0.0, image_h - 1.0]],
        dtype=np.float32,
    )
    best_candidate = None
    best_score = -1.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = float(cv2.contourArea(contour))
        area_ratio = area / image_area
        if area_ratio < float(min_area_ratio) or area_ratio > float(max_area_ratio):
            continue
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        ordered = _order_quad_points(approx.reshape(4, 2))
        top_width = float(np.linalg.norm(ordered[1] - ordered[0]))
        bottom_width = float(np.linalg.norm(ordered[2] - ordered[3]))
        left_height = float(np.linalg.norm(ordered[3] - ordered[0]))
        right_height = float(np.linalg.norm(ordered[2] - ordered[1]))
        quad_width = max(top_width, bottom_width)
        quad_height = max(left_height, right_height)
        if quad_width <= 0 or quad_height <= 0:
            continue
        aspect_ratio = quad_width / quad_height
        if aspect_ratio < 0.45 or aspect_ratio > 1.4:
            continue
        bounding_x, bounding_y, bounding_w, bounding_h = cv2.boundingRect(approx)
        fill_ratio = area / float(max(bounding_w * bounding_h, 1))
        if fill_ratio < 0.65:
            continue
        distances = [
            float(np.linalg.norm(point - corner))
            for point, corner in zip(ordered, corners)
        ]
        score = area_ratio + min(0.25, fill_ratio / 4.0)
        if all(distance <= tolerance for distance in distances):
            score += 0.2
        if score <= best_score:
            continue
        best_score = score
        best_candidate = {
            "found": True,
            "points": ordered,
            "area_ratio": round(area_ratio, 4),
            "fill_ratio": round(fill_ratio, 4),
            "aspect_ratio": round(aspect_ratio, 4),
            "bounding_rect": [int(bounding_x), int(bounding_y), int(bounding_w), int(bounding_h)],
            "corner_distances": [round(distance, 2) for distance in distances],
            "corner_aligned": all(distance <= tolerance for distance in distances),
        }
    if best_candidate is not None:
        return best_candidate
    return {"found": False, "skipped_reason": "quad_not_found"}


def _perspective_distortion_metrics(points: np.ndarray) -> dict[str, float]:
    ordered = _order_quad_points(points)
    top_width = float(np.linalg.norm(ordered[1] - ordered[0]))
    bottom_width = float(np.linalg.norm(ordered[2] - ordered[3]))
    left_height = float(np.linalg.norm(ordered[3] - ordered[0]))
    right_height = float(np.linalg.norm(ordered[2] - ordered[1]))
    width_ratio_delta = abs(top_width - bottom_width) / max(top_width, bottom_width, 1.0)
    height_ratio_delta = abs(left_height - right_height) / max(left_height, right_height, 1.0)
    return {
        "width_ratio_delta": round(width_ratio_delta, 4),
        "height_ratio_delta": round(height_ratio_delta, 4),
    }


def correct_perspective(image_bgr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    detection = detect_document_quad(image_bgr)
    if not bool(detection.get("found")):
        detection["applied"] = False
        return image_bgr.copy(), detection
    ordered = np.asarray(detection["points"], dtype=np.float32)
    distortion = _perspective_distortion_metrics(ordered)
    min_ratio_delta = max(0.01, _read_float_env("OCR_PAGE_CORRECTION_MIN_PERSPECTIVE_RATIO_DELTA", 0.03))
    if (
        float(distortion["width_ratio_delta"]) < min_ratio_delta
        and float(distortion["height_ratio_delta"]) < min_ratio_delta
    ):
        return image_bgr.copy(), {
            "applied": False,
            "skipped_reason": "already_rectilinear",
            "area_ratio": detection.get("area_ratio"),
            "corner_distances": detection.get("corner_distances") or [],
            **distortion,
        }
    top_width = np.linalg.norm(ordered[1] - ordered[0])
    bottom_width = np.linalg.norm(ordered[2] - ordered[3])
    left_height = np.linalg.norm(ordered[3] - ordered[0])
    right_height = np.linalg.norm(ordered[2] - ordered[1])
    width = max(int(round(max(top_width, bottom_width))), 1)
    height = max(int(round(max(left_height, right_height))), 1)
    dst = np.array(
        [
            [0.0, 0.0],
            [width - 1.0, 0.0],
            [width - 1.0, height - 1.0],
            [0.0, height - 1.0],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(ordered, dst)
    corrected = cv2.warpPerspective(
        image_bgr,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return corrected, {
        "applied": True,
        "area_ratio": detection.get("area_ratio"),
        "corner_distances": detection.get("corner_distances") or [],
        **distortion,
        "output_size": [int(corrected.shape[1]), int(corrected.shape[0])],
        "points": [[float(x), float(y)] for x, y in ordered.tolist()],
    }


def _center_content(image_bgr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    height, width = image_bgr.shape[:2]
    bbox = _content_bbox(image_bgr)
    if bbox is None:
        return image_bgr.copy(), {
            "position_normalized": False,
            "content_bbox_norm": None,
            "translation_px": [0, 0],
        }
    x0, y0, x1, y1 = bbox
    box_width = x1 - x0
    box_height = y1 - y0
    if box_width <= 0 or box_height <= 0:
        return image_bgr.copy(), {
            "position_normalized": False,
            "content_bbox_norm": _normalize_bbox(bbox, width=width, height=height),
            "translation_px": [0, 0],
        }
    box_center_x = (x0 + x1) // 2
    box_center_y = (y0 + y1) // 2
    target_center_x = width // 2
    target_center_y = height // 2
    dx = int(target_center_x - box_center_x)
    dy = int(target_center_y - box_center_y)
    min_shift = max(4, int(min(width, height) * 0.03))
    if abs(dx) < min_shift and abs(dy) < min_shift:
        return image_bgr.copy(), {
            "position_normalized": False,
            "content_bbox_norm": _normalize_bbox(bbox, width=width, height=height),
            "translation_px": [0, 0],
        }
    canvas = np.full_like(image_bgr, 255)
    src = image_bgr[y0:y1, x0:x1]
    dst_x0 = max(0, min(width - box_width, x0 + dx))
    dst_y0 = max(0, min(height - box_height, y0 + dy))
    dst_x1 = dst_x0 + box_width
    dst_y1 = dst_y0 + box_height
    canvas[dst_y0:dst_y1, dst_x0:dst_x1] = src
    return canvas, {
        "position_normalized": True,
        "content_bbox_norm": _normalize_bbox((dst_x0, dst_y0, dst_x1, dst_y1), width=width, height=height),
        "translation_px": [int(dst_x0 - x0), int(dst_y0 - y0)],
    }


def normalize_page_position(image_bgr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    corrected, diagnostics = _center_content(image_bgr)
    diagnostics = dict(diagnostics)
    diagnostics["applied"] = bool(diagnostics.get("position_normalized"))
    return corrected, diagnostics


def correct_page_image(
    image: np.ndarray,
    *,
    enable_perspective: bool = True,
    enable_rotation: bool = True,
    enable_position_normalization: bool = True,
) -> tuple[np.ndarray, dict[str, Any]]:
    current = image.copy()
    perspective_diag = {"applied": False, "skipped_reason": "disabled"}
    if enable_perspective:
        current, perspective_diag = correct_perspective(current)
    skew_angle, skew_diag = _estimate_skew_angle_deg(current)
    deskew_applied = abs(skew_angle) > 1e-6 and enable_rotation
    if deskew_applied:
        current = _rotate_small_angle(current, skew_angle)
    position_diag = {
        "position_normalized": False,
        "content_bbox_norm": _normalize_bbox(_content_bbox(current), width=current.shape[1], height=current.shape[0]),
        "translation_px": [0, 0],
    }
    if enable_position_normalization:
        current, position_diag = _center_content(current)
    diagnostics = {
        "mode": "generic"
        if perspective_diag.get("applied") or deskew_applied or position_diag["position_normalized"]
        else "identity",
        "right_angle_rotation_deg": 0,
        "deskew_angle_deg": round(float(skew_angle), 4),
        "deskew_applied": deskew_applied,
        "perspective_applied": bool(perspective_diag.get("applied")),
        "position_normalized": bool(position_diag["position_normalized"]),
        "content_bbox_norm": position_diag["content_bbox_norm"],
        "translation_px": position_diag["translation_px"],
        "warnings": [],
        "diagnostics": {"skew": skew_diag, "perspective": perspective_diag},
    }
    return current, diagnostics


def correct_page_images(
    *,
    page_images: Iterable[tuple[int, np.ndarray]],
    db: Any,
    preferred_template_id: str | None = None,
    preferred_template_ids: list[str] | None = None,
) -> tuple[list[tuple[int, np.ndarray]], dict[str, Any]]:
    pages = [(int(page_index), image.copy()) for page_index, image in page_images if image is not None]
    if not pages:
        return [], _empty_correction_summary(enabled=True)
    template_ids: list[str] = []
    if preferred_template_id and str(preferred_template_id).strip():
        template_ids.append(str(preferred_template_id).strip())
    for template_id in preferred_template_ids or []:
        token = str(template_id or "").strip()
        if token and token not in template_ids:
            template_ids.append(token)
    corrected_pages: list[tuple[int, np.ndarray]] = []
    page_diags: list[dict[str, Any]] = []
    document_rotation_deg = 0
    first_page_index, first_page_image = pages[0]
    first_corrected = None
    first_diag = None
    if template_ids and _read_bool_env("OCR_ENABLE_PAGE_CORRECTION_TEMPLATE_WARP", True):
        first_corrected, first_diag = _try_template_warp(
            db=db,
            page_image_bgr=first_page_image,
            template_ids=template_ids,
            preferred_template_id=preferred_template_id,
        )
        if isinstance(first_diag, dict):
            document_rotation_deg = int(first_diag.get("right_angle_rotation_deg") or 0) % 360
    for page_index, page_image in pages:
        if page_index == first_page_index and first_corrected is not None and isinstance(first_diag, dict):
            corrected = first_corrected
            diag = dict(first_diag)
        else:
            rotated = _rotate_right_angle(page_image, document_rotation_deg)
            corrected, diag = correct_page_image(
                rotated,
                enable_perspective=_read_bool_env("OCR_ENABLE_PAGE_CORRECTION_PERSPECTIVE", True),
                enable_rotation=_read_bool_env("OCR_ENABLE_PAGE_CORRECTION_DESKEW", True),
                enable_position_normalization=_read_bool_env("OCR_ENABLE_PAGE_CORRECTION_POSITION_NORMALIZE", True),
            )
            diag["right_angle_rotation_deg"] = document_rotation_deg
        diag["page_index"] = int(page_index)
        corrected_pages.append((page_index, corrected))
        page_diags.append(diag)
    template_warp_page_count = sum(1 for item in page_diags if item.get("mode") == "template_warp")
    deskew_page_count = sum(1 for item in page_diags if item.get("deskew_applied"))
    position_normalized_page_count = sum(1 for item in page_diags if item.get("position_normalized"))
    applied_page_count = sum(
        1
        for item in page_diags
        if int(item.get("right_angle_rotation_deg") or 0) % 360 != 0
        or bool(item.get("perspective_applied"))
        or bool(item.get("deskew_applied"))
        or bool(item.get("position_normalized"))
    )
    summary = {
        "enabled": True,
        "applied": applied_page_count > 0,
        "document_rotation_deg": document_rotation_deg,
        "applied_page_count": applied_page_count,
        "template_warp_page_count": template_warp_page_count,
        "deskew_page_count": deskew_page_count,
        "position_normalized_page_count": position_normalized_page_count,
        "corrected_page_count": len(corrected_pages),
        "corrected_pdf_generated": False,
        "corrected_pdf_changed": False,
        "corrected_pdf_byte_length": 0,
        "corrected_pdf_uploaded": False,
        "corrected_pdf_uri": None,
        "pages": page_diags,
    }
    return corrected_pages, summary


def correct_pdf_for_yomitoku(
    *,
    pdf_bytes: bytes,
    dpi: int,
    db: Any,
    preferred_template_id: str | None = None,
    preferred_template_ids: list[str] | None = None,
) -> tuple[bytes, dict[str, Any], list[tuple[int, np.ndarray]] | None]:
    if not _read_bool_env("OCR_ENABLE_PAGE_CORRECTION", True):
        return pdf_bytes, _empty_correction_summary(enabled=False), None
    page_images = render_pdf_to_page_images(pdf_bytes, dpi)
    corrected_pages, summary = correct_page_images(
        page_images=page_images,
        db=db,
        preferred_template_id=preferred_template_id,
        preferred_template_ids=preferred_template_ids,
    )
    if not summary.get("applied"):
        return pdf_bytes, summary, None
    corrected_pdf_bytes = _save_pdf([image for _, image in corrected_pages])
    summary["corrected_pdf_generated"] = bool(corrected_pdf_bytes)
    summary["corrected_pdf_changed"] = bool(corrected_pdf_bytes and corrected_pdf_bytes != pdf_bytes)
    summary["corrected_pdf_byte_length"] = int(len(corrected_pdf_bytes or b""))
    return corrected_pdf_bytes or pdf_bytes, summary, corrected_pages
