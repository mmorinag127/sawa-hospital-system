from __future__ import annotations

import unittest
from pathlib import Path

import cv2

from app.template_match import choose_template_and_warp


class _MissingDoc:
    exists = False
    id = ""

    def to_dict(self):
        return {}


class _EmptyCollection:
    def document(self, _template_id: str):
        return self

    def get(self):
        return _MissingDoc()

    def stream(self):
        return []


class _EmptyDB:
    def collection(self, _name: str):
        return _EmptyCollection()


class TemplateMatchFallbackTest(unittest.TestCase):
    def test_choose_template_uses_local_registry_when_firestore_is_empty(self):
        root = Path(__file__).resolve().parents[1]
        image_path = root / "src" / "data" / "templates" / "fax_layout_floor_2f3f_v1.png"
        image = cv2.imread(str(image_path))
        self.assertIsNotNone(image)

        matched_template_id, warped_match, warped_ocr, warped_alt, diagnostics = choose_template_and_warp(
            _EmptyDB(),
            image,
            image,
            template_ids=["fax_layout_floor_2f3f_v1"],
        )

        self.assertEqual(matched_template_id, "fax_layout_floor_2f3f_v1")
        self.assertIsNotNone(warped_match)
        self.assertIsNotNone(warped_ocr)
        self.assertIsNone(warped_alt)
        candidates = diagnostics.get("candidates") or []
        self.assertTrue(any(candidate.get("status") == "matched" for candidate in candidates))


if __name__ == "__main__":
    unittest.main()
