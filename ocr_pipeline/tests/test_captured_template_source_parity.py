from __future__ import annotations

import ast
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = "4677ef58dd94c5c99eef228b03691bf6cace8a75"


class CapturedTemplateSourceParityTest(unittest.TestCase):
    def original(self, path):
        return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT, text=True)

    def test_roi_geometry_recognizer_and_preprocessing_source_unchanged(self):
        path = "ocr_pipeline/app/rois.py"
        current = (ROOT / path).read_text()
        original = self.original(path)
        self.assertEqual(current[current.index("def _coerce_box"):], original[original.index("def _coerce_box"):])
        path = "ocr_pipeline/app/main.py"
        current = (ROOT / path).read_text()
        original = self.original(path)
        for name in ("_run_template_roi_extraction",):
            def segment(source):
                node = next(node for node in ast.parse(source).body
                            if isinstance(node, ast.FunctionDef) and node.name == name)
                return ast.get_source_segment(source, node)
            self.assertEqual(segment(current), segment(original))
        retired_constant = (
            'OCR_TEMPLATE_MATCH_ALLOW_DISCOVERY = (\n'
            '    os.environ.get("OCR_TEMPLATE_MATCH_ALLOW_DISCOVERY", "false").lower() == "true"\n'
            ')\n'
        )
        self.assertEqual(original.count(retired_constant), 1)
        self.assertNotIn("OCR_TEMPLATE_MATCH_ALLOW_DISCOVERY", current)
        self.assertEqual(current[:current.index("def _run_template_classification")],
                         original[:original.index("def _run_template_classification")].replace(retired_constant, "", 1))
        self.assertEqual(current[current.index("def _run_template_roi_extraction"):],
                         original[original.index("def _run_template_roi_extraction"):])

    def test_classification_retains_exact_preferred_call_and_result_tail(self):
        path = "ocr_pipeline/app/main.py"
        current = (ROOT / path).read_text()
        original = self.original(path)
        def classification(source):
            return next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "_run_template_classification")
        old_function = classification(original)
        new_function = classification(current)
        def calls(function):
            return sorted((node for node in ast.walk(function)
                           if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                           and node.func.id == "choose_template_and_warp"), key=lambda node: node.lineno)
        self.assertEqual(len(calls(new_function)), 1)
        self.assertEqual(ast.dump(calls(new_function)[0]), ast.dump(calls(old_function)[0]))
        old_source = ast.get_source_segment(original, old_function)
        new_source = ast.get_source_segment(current, new_function)
        tail = "    confidence = _classification_score"
        self.assertEqual(new_source[new_source.index(tail):], old_source[old_source.index(tail):])
        self.assertIn('preferred_id = str(requested_template_id).strip()', new_source)
        self.assertIn('classification_mode = "preferred_primary"', new_source)
        self.assertIn('raise RuntimeError(f"requested template did not match: {requested_template_id}")', new_source)
        self.assertNotIn("prefer_requested", new_source)
        self.assertNotIn('"discovery"', new_source)

    def test_image_download_and_entire_orb_score_homography_warp_tail_unchanged(self):
        path = "ocr_pipeline/app/template_match.py"
        current = (ROOT / path).read_text()
        original = self.original(path)
        def download(source):
            node = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "_download_template_png")
            return ast.get_source_segment(source, node)
        self.assertEqual(download(current), download(original))
        self.assertEqual(current[current.index("    best_id = None"):],
                         original[original.index("    best_id = None"):])


if __name__ == "__main__":
    unittest.main()
