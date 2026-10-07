from __future__ import annotations

import ast
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.rois import load_template_config


class TemplateCollectionConfigTest(unittest.TestCase):
    def setUp(self):
        # Synthetic component config; not a facility/FAX flow fixture.
        self.config = {"facility": {"fax_template": {
            "template_id": "component-template", "zero": 0, "unknown": None,
        }}}
        self.digest = hashlib.sha256(json.dumps(
            self.config, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        self.db = Mock()
        self.db.collection.side_effect = AssertionError("alternate DB access")

    def test_missing_capture_stops_even_with_collection_env(self):
        with patch.dict("os.environ", {"OCR_TEMPLATE_COLLECTION": "foreign", "TEMPLATE_COLLECTION": "foreign"}):
            with self.assertRaisesRegex(ValueError, "captured_template_receipt_mismatch"):
                load_template_config(self.db, "component-template", collection="foreign")
        self.db.collection.assert_not_called()

    def test_valid_capture_preserves_template_and_copy_isolation(self):
        result = load_template_config(self.db, "component-template", captured_config=self.config, expected_digest=self.digest)
        self.assertEqual(result, self.config["facility"]["fax_template"])
        self.assertEqual(result["zero"], 0)
        self.assertIsNone(result["unknown"])
        result["zero"] = 9
        self.assertEqual(self.config["facility"]["fax_template"]["zero"], 0)
        self.db.collection.assert_not_called()

    def test_wrong_digest_and_wrong_or_missing_id_stop_without_db(self):
        for template_id, digest, error in (
            ("component-template", "wrong", "captured_template_receipt_mismatch"),
            ("foreign", self.digest, "captured_template_id_mismatch"),
            (None, self.digest, "captured_template_id_mismatch"),
        ):
            with self.subTest(template_id=template_id, digest=digest):
                with self.assertRaisesRegex(ValueError, error):
                    load_template_config(self.db, template_id, captured_config=self.config, expected_digest=digest)
        self.db.collection.assert_not_called()

    def test_disabled_classification_validates_before_optional_return(self):
        source = Path(__file__).resolve().parents[1] / "app/main.py"
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == "_run_template_classification")
        render = Mock(side_effect=AssertionError("image work before validation"))
        namespace = {"db": self.db, "load_template_config": load_template_config,
                     "OCR_ENABLE_TEMPLATE_MATCH": False, "render_pdf_to_png_bytes": render,
                     "Any": object}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        classify = namespace[function.name]
        with self.assertRaisesRegex(ValueError, "captured_template_receipt_mismatch"):
            classify(pdf_bytes=b"not-a-PDF", requested_template_id="component-template")
        for candidates in (["foreign"], ["component-template", "foreign"], ["component-template"] * 2):
            with self.subTest(candidates=candidates):
                with self.assertRaisesRegex(ValueError, "captured_template_selection_required"):
                    classify(pdf_bytes=b"not-a-PDF", requested_template_id="component-template",
                             requested_template_ids=candidates, captured_config=self.config, configured_digest=self.digest)
        self.assertEqual(classify(pdf_bytes=b"not-a-PDF", requested_template_id="component-template",
                                 captured_config=self.config, configured_digest=self.digest), (None, None))
        render.assert_not_called()
        self.db.collection.assert_not_called()


    def test_enabled_classification_rejects_bad_receipt_or_id_before_render(self):
        source = Path(__file__).resolve().parents[1] / "app/main.py"
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == "_run_template_classification")
        render = Mock(side_effect=AssertionError("image work before validation"))
        namespace = {"db": self.db, "load_template_config": load_template_config,
                     "OCR_ENABLE_TEMPLATE_MATCH": True, "render_pdf_to_png_bytes": render,
                     "Any": object}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        for template_id, digest, error in (
            ("component-template", "wrong", "captured_template_receipt_mismatch"),
            ("foreign", self.digest, "captured_template_id_mismatch"),
            (None, self.digest, "captured_template_id_mismatch"),
        ):
            with self.subTest(template_id=template_id):
                with self.assertRaisesRegex(ValueError, error):
                    namespace[function.name](pdf_bytes=b"not-a-PDF", requested_template_id=template_id,
                                             captured_config=self.config, configured_digest=digest)
        render.assert_not_called()
        self.db.collection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
