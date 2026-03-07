from __future__ import annotations

import pathlib
import sys
import unittest

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app import page_correction, yomitoku_runner  # noqa: E402


def _make_form_image(
    *,
    width: int = 900,
    height: int = 1200,
    shift_x: int = 0,
    shift_y: int = 0,
) -> np.ndarray:
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    left = 120 + shift_x
    top = 140 + shift_y
    right = width - 120 + shift_x
    bottom = height - 140 + shift_y
    cv2.rectangle(image, (left, top), (right, bottom), (0, 0, 0), 4)
    for offset in range(90, bottom - top, 120):
        y = top + offset
        cv2.line(image, (left + 30, y), (right - 30, y), (0, 0, 0), 3)
    for idx in range(6):
        cv2.putText(
            image,
            f"FAX {idx + 1}",
            (left + 40, top + 60 + (idx * 120)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.4,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
    return image


def _rotate_canvas(image: np.ndarray, angle_deg: float) -> np.ndarray:
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle_deg, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def _perspective_canvas(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    src = np.array(
        [[40.0, 50.0], [w - 60.0, 20.0], [w - 30.0, h - 70.0], [20.0, h - 30.0]],
        dtype=np.float32,
    )
    dst = np.array(
        [[0.0, 0.0], [w - 1.0, 0.0], [w - 1.0, h - 1.0], [0.0, h - 1.0]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(dst, src)
    return cv2.warpPerspective(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def _bbox_center(image: np.ndarray) -> tuple[float, float]:
    bbox = page_correction._foreground_bbox(image)
    if bbox is None:
        return 0.0, 0.0
    x, y, w, h = bbox
    return x + (w / 2.0), y + (h / 2.0)


class PageCorrectionTest(unittest.TestCase):
    def test_correct_rotation_aligns_small_skewed_form(self):
        base = _make_form_image()
        rotated = _rotate_canvas(base, 5.0)

        corrected, diagnostics = page_correction.correct_rotation(rotated)

        self.assertTrue(diagnostics["applied"])
        residual = page_correction.estimate_rotation_angle(corrected)
        self.assertLess(abs(float(residual.get("angle_deg") or 0.0)), 1.0)

    def test_correct_perspective_rectifies_page_quadrilateral(self):
        base = _make_form_image()
        warped = _perspective_canvas(base)

        corrected, diagnostics = page_correction.correct_perspective(warped)

        self.assertTrue(diagnostics["applied"])
        residual = page_correction.estimate_rotation_angle(corrected)
        self.assertLess(abs(float(residual.get("angle_deg") or 0.0)), 1.5)
        self.assertGreater(corrected.shape[0], 700)
        self.assertGreater(corrected.shape[1], 500)

    def test_normalize_page_position_recenters_shifted_foreground(self):
        shifted = _make_form_image(shift_x=-80, shift_y=-100)
        before_center = _bbox_center(shifted)

        corrected, diagnostics = page_correction.normalize_page_position(shifted)

        self.assertTrue(diagnostics["applied"])
        after_center = _bbox_center(corrected)
        target_center = (shifted.shape[1] / 2.0, shifted.shape[0] / 2.0)
        before_error = abs(before_center[0] - target_center[0]) + abs(before_center[1] - target_center[1])
        after_error = abs(after_center[0] - target_center[0]) + abs(after_center[1] - target_center[1])
        self.assertLess(after_error, before_error)

    def test_correct_page_image_skips_perspective_for_clean_rectilinear_page(self):
        clean = _make_form_image()

        corrected, diagnostics = page_correction.correct_page_image(clean)

        self.assertFalse(diagnostics["perspective_applied"])
        self.assertFalse(diagnostics["deskew_applied"])
        self.assertFalse(diagnostics["position_normalized"])
        np.testing.assert_array_equal(corrected, clean)

    def test_correct_pdf_for_yomitoku_applies_template_warp_to_first_page(self):
        first_page = _make_form_image()
        second_page = _make_form_image(shift_x=20)
        warped_first_page = np.full((1400, 1000, 3), 245, dtype=np.uint8)

        original_render = page_correction.render_pdf_to_page_images
        original_build = page_correction.build_images_for_match_and_ocr
        original_choose = page_correction.choose_template_and_warp
        try:
            page_correction.render_pdf_to_page_images = lambda _pdf_bytes, _dpi: [(1, first_page), (2, second_page)]
            page_correction.build_images_for_match_and_ocr = lambda _png_bytes: (first_page, first_page, first_page)
            page_correction.choose_template_and_warp = (
                lambda _db, _match_bgr, _ocr_bgr, img_alt_bgr=None, template_ids=None: (
                    "fax_layout_floor_2f3f_v1",
                    _match_bgr,
                    warped_first_page,
                    img_alt_bgr,
                    {"matched": True, "template_ids": template_ids or []},
                )
            )

            corrected_pdf, summary, corrected_pages = page_correction.correct_pdf_for_yomitoku(
                pdf_bytes=b"%PDF-1.4\n%EOF\n",
                dpi=200,
                db=None,
                preferred_template_id="fax_layout_floor_2f3f_v1",
                preferred_template_ids=None,
            )
        finally:
            page_correction.render_pdf_to_page_images = original_render
            page_correction.build_images_for_match_and_ocr = original_build
            page_correction.choose_template_and_warp = original_choose

        self.assertTrue(summary["applied"])
        self.assertEqual(summary["template_warp_page_count"], 1)
        self.assertEqual(corrected_pages[0][1].shape, warped_first_page.shape)
        self.assertTrue(len(corrected_pdf) > 0)

    def test_run_yomitoku_uses_supplied_page_images_without_pdf_render(self):
        corrected_page = _make_form_image()
        captured: dict[str, np.ndarray] = {}

        class _FakeResults:
            def __init__(self, image: np.ndarray):
                self._image = image

            def to_markdown(
                self,
                path: str,
                *,
                ignore_line_break: bool,
                img: np.ndarray,
                export_figure: bool,
                figure_width: int,
                figure_dir: str,
            ) -> None:
                pathlib.Path(path).write_text("# test\n", encoding="utf-8")

        original_render = yomitoku_runner.render_pdf_to_page_images
        original_get_analyzer = yomitoku_runner._get_analyzer
        original_serialize = yomitoku_runner._serialize_results
        original_extract_tables = yomitoku_runner._extract_tables
        try:
            def _fail_render(_pdf_bytes, _dpi):
                raise AssertionError("render_pdf_to_page_images should not be called")

            def _fake_get_analyzer(_device: str, _visualize: bool):
                def _analyzer(image: np.ndarray):
                    captured["image"] = image.copy()
                    return _FakeResults(image), None, None

                return _analyzer

            yomitoku_runner.render_pdf_to_page_images = _fail_render
            yomitoku_runner._get_analyzer = _fake_get_analyzer
            yomitoku_runner._serialize_results = lambda _results: {"pages": []}
            yomitoku_runner._extract_tables = lambda _analysis, page_index, width, height: []

            page_results, ocr_pdf, layout_pdf = yomitoku_runner.run_yomitoku(
                pdf_bytes=None,
                dpi=200,
                device="cpu",
                visualize=False,
                ignore_line_break=False,
                no_figure=True,
                figure_width=200,
                figure_dir="figures",
                page_images=[(1, corrected_page)],
            )
        finally:
            yomitoku_runner.render_pdf_to_page_images = original_render
            yomitoku_runner._get_analyzer = original_get_analyzer
            yomitoku_runner._serialize_results = original_serialize
            yomitoku_runner._extract_tables = original_extract_tables

        self.assertEqual(len(page_results), 1)
        self.assertIsNone(ocr_pdf)
        self.assertIsNone(layout_pdf)
        np.testing.assert_array_equal(captured["image"], corrected_page)


if __name__ == "__main__":
    unittest.main()
