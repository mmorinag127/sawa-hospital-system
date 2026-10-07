from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np

from app.template_match import choose_template_and_warp


class TemplateMatchFallbackTest(unittest.TestCase):
    def test_missing_capture_foreign_and_duplicate_selection_stop_before_image_or_db(self):
        db = Mock()
        db.collection.side_effect = AssertionError("alternate DB access")
        own = {"template_id": "component-template"}
        cases = (
            (None, ["component-template"], "captured_template_selection_required"),
            ({"component-template": own}, None, "captured_template_selection_required"),
            ({"component-template": own}, ["foreign"], "captured_template_selection_required"),
            ({"component-template": own}, ["component-template"] * 2, "captured_template_selection_required"),
            ({"component-template": own, "foreign": {"template_id": "foreign"}}, ["component-template"], "captured_template_selection_required"),
            ({"component-template": {"template_id": "foreign"}}, ["component-template"], "captured_template_id_mismatch"),
        )
        with patch("app.template_match.cv2.BFMatcher") as matcher, patch("app.template_match.cv2.cvtColor") as convert:
            for authored, selected, error in cases:
                with self.subTest(authored=authored, selected=selected):
                    with self.assertRaisesRegex(ValueError, error):
                        choose_template_and_warp(db, None, None, collection="foreign",
                                                 template_ids=selected, authored_templates=authored)
            matcher.assert_not_called()
            convert.assert_not_called()
        db.collection.assert_not_called()

    def test_valid_authored_selection_retains_actual_no_descriptor_diagnostics(self):
        # Synthetic raster, not PDF/OCR success evidence.
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        db = Mock()
        db.collection.side_effect = AssertionError("alternate DB access")
        authored = {"component-template": {
            "template_id": "component-template", "template_image_uri": "component-raster",
        }}
        with patch("app.template_match._download_template_png", return_value=image) as download:
            result = choose_template_and_warp(db, image, image, template_ids=["component-template"], authored_templates=authored)
        self.assertEqual(result, (None, None, None, None, {
            "candidates": [{"id": "component-template", "status": "no_descriptors"}],
        }))
        download.assert_called_once_with("component-raster")
        db.collection.assert_not_called()


    def test_valid_canonical_raster_retains_actual_match_and_warp(self):
        # Authored synthetic component raster, not a customer template/FAX.
        image = np.random.default_rng(7).integers(0, 256, (256, 256, 3), dtype=np.uint8)
        db = Mock()
        db.collection.side_effect = AssertionError("alternate DB access")
        authored = {"component-template": {
            "template_id": "component-template", "template_image_uri": "component-raster",
        }}
        with patch("app.template_match._download_template_png", return_value=image):
            selected, warped_match, warped_ocr, warped_alt, diagnostics = choose_template_and_warp(
                db, image, image, img_alt_bgr=image,
                template_ids=["component-template"], authored_templates=authored,
            )
        self.assertEqual(selected, "component-template")
        for warped in (warped_match, warped_ocr, warped_alt):
            self.assertEqual(warped.shape, image.shape)
            np.testing.assert_allclose(warped, image, atol=1)
        self.assertEqual(len(diagnostics["candidates"]), 1)
        self.assertEqual(diagnostics["candidates"][0]["id"], "component-template")
        self.assertEqual(diagnostics["candidates"][0]["status"], "matched")
        db.collection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
