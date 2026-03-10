import importlib
import logging
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))


def _install_stub_modules() -> None:
    cv2_module = types.ModuleType("cv2")
    cv2_module.imencode = lambda *_args, **_kwargs: (True, types.SimpleNamespace(tobytes=lambda: b""))
    sys.modules.setdefault("cv2", cv2_module)

    flask_module = types.ModuleType("flask")

    class _FakeFlask:
        def __init__(self, *_args, **_kwargs):
            self.logger = logging.getLogger("test-flask")

        def route(self, *_args, **_kwargs):
            def _decorator(func):
                return func

            return _decorator

        def post(self, *_args, **_kwargs):
            def _decorator(func):
                return func

            return _decorator

    flask_module.Flask = _FakeFlask
    flask_module.jsonify = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    flask_module.request = types.SimpleNamespace(get_json=lambda *args, **kwargs: {})
    sys.modules.setdefault("flask", flask_module)

    google_module = types.ModuleType("google")
    google_cloud_module = types.ModuleType("google.cloud")
    google_firestore_module = types.ModuleType("google.cloud.firestore")
    google_storage_module = types.ModuleType("google.cloud.storage")

    class _FakeFirestoreClient:
        def __init__(self, *args, **kwargs):
            pass

    class _FakeStorageClient:
        def __init__(self, *args, **kwargs):
            pass

        def bucket(self, *_args, **_kwargs):
            raise AssertionError("bucket() should not be used in helper unit tests")

    google_firestore_module.Client = _FakeFirestoreClient
    google_firestore_module.SERVER_TIMESTAMP = object()
    google_storage_module.Client = _FakeStorageClient
    google_storage_module.Blob = object
    google_module.cloud = google_cloud_module
    google_cloud_module.firestore = google_firestore_module
    google_cloud_module.storage = google_storage_module
    sys.modules.setdefault("google", google_module)
    sys.modules.setdefault("google.cloud", google_cloud_module)
    sys.modules.setdefault("google.cloud.firestore", google_firestore_module)
    sys.modules.setdefault("google.cloud.storage", google_storage_module)

    issue_detection_module = types.ModuleType("app.issue_detection")
    issue_detection_module.detect_table_cell_issues = lambda *args, **kwargs: []
    issue_detection_module.merge_cell_issues = lambda *args, **kwargs: []
    sys.modules.setdefault("app.issue_detection", issue_detection_module)

    page_correction_module = types.ModuleType("app.page_correction")
    page_correction_module.correct_pdf_for_yomitoku = lambda *args, **kwargs: (b"", {}, [])
    sys.modules.setdefault("app.page_correction", page_correction_module)

    pdf_render_module = types.ModuleType("app.pdf_render")
    pdf_render_module.render_pdf_to_png_bytes = lambda *args, **kwargs: []
    sys.modules.setdefault("app.pdf_render", pdf_render_module)

    postprocess_module = types.ModuleType("app.postprocess")
    postprocess_module._tesseract_digits_text = lambda *args, **kwargs: ""
    postprocess_module.postprocess_and_retry = lambda *args, **kwargs: {}
    sys.modules.setdefault("app.postprocess", postprocess_module)

    preprocess_module = types.ModuleType("app.preprocess")
    preprocess_module.build_images_for_match_and_ocr = lambda *args, **kwargs: (None, None)
    sys.modules.setdefault("app.preprocess", preprocess_module)

    rois_module = types.ModuleType("app.rois")
    rois_module.crop_rois = lambda *args, **kwargs: {}
    rois_module.load_template_config = lambda *args, **kwargs: {}
    sys.modules.setdefault("app.rois", rois_module)

    template_match_module = types.ModuleType("app.template_match")
    template_match_module.choose_template_and_warp = lambda *args, **kwargs: {}
    sys.modules.setdefault("app.template_match", template_match_module)

    yomitoku_runner_module = types.ModuleType("app.yomitoku_runner")
    yomitoku_runner_module.ocr_image_text = lambda *args, **kwargs: ""
    yomitoku_runner_module.ocr_image_words = lambda *args, **kwargs: []
    yomitoku_runner_module.run_yomitoku = lambda *args, **kwargs: ([], None, None)
    sys.modules.setdefault("app.yomitoku_runner", yomitoku_runner_module)


_install_stub_modules()
pipeline_main = importlib.import_module("app.main")


class MainArtifactTests(unittest.TestCase):
    def test_upload_corrected_pdf_artifact_returns_uri_when_correction_applied(self):
        with mock.patch.object(
            pipeline_main,
            "_upload_bytes",
            return_value="gs://bucket/output/doc_corrected.pdf",
        ) as upload_mock:
            uri = pipeline_main._upload_corrected_pdf_artifact(
                bucket="bucket",
                artifact_prefix="output/doc/",
                base="doc",
                original_pdf_bytes=b"%PDF-raw\n%EOF\n",
                corrected_pdf_bytes=b"%PDF-corrected\n%EOF\n",
                page_correction_summary={"applied": True, "corrected_pdf_generated": True},
            )

        self.assertEqual(uri, "gs://bucket/output/doc_corrected.pdf")
        upload_mock.assert_called_once_with(
            "bucket",
            "output/doc/doc_corrected.pdf",
            b"%PDF-corrected\n%EOF\n",
            "application/pdf",
        )

    def test_upload_corrected_pdf_artifact_skips_when_not_applied(self):
        with mock.patch.object(pipeline_main, "_upload_bytes") as upload_mock:
            uri = pipeline_main._upload_corrected_pdf_artifact(
                bucket="bucket",
                artifact_prefix="output/doc/",
                base="doc",
                original_pdf_bytes=b"%PDF-raw\n%EOF\n",
                corrected_pdf_bytes=b"%PDF-corrected\n%EOF\n",
                page_correction_summary={"applied": False, "corrected_pdf_generated": True},
            )

        self.assertIsNone(uri)
        upload_mock.assert_not_called()

    def test_upload_corrected_pdf_artifact_skips_when_bytes_unchanged(self):
        with mock.patch.object(pipeline_main, "_upload_bytes") as upload_mock:
            uri = pipeline_main._upload_corrected_pdf_artifact(
                bucket="bucket",
                artifact_prefix="output/doc/",
                base="doc",
                original_pdf_bytes=b"%PDF-same\n%EOF\n",
                corrected_pdf_bytes=b"%PDF-same\n%EOF\n",
                page_correction_summary={"applied": True, "corrected_pdf_generated": True},
            )

        self.assertIsNone(uri)
        upload_mock.assert_not_called()

    def test_template_roi_extraction_skips_large_text_ocr_when_qty_uses_tesseract(self):
        sample_image = object()
        captured: dict[str, object] = {}

        def _fake_postprocess_and_retry(*, rois, tpl_cfg, ocr_fn, base_prompt=""):  # noqa: ARG001
            captured["qty"] = ocr_fn(sample_image, "", 32)
            captured["menu"] = ocr_fn(sample_image, "", 512)
            return {"qty": {}, "qty_row_order": [], "qty_col_order": []}

        with mock.patch.object(pipeline_main, "ocr_image_words", return_value=[]), mock.patch.object(
            pipeline_main,
            "crop_rois",
            return_value={"qty_cells": [], "qty_schema": {"rows": 0, "cols": 0}},
        ), mock.patch.object(
            pipeline_main,
            "_tesseract_digits_text",
            return_value="42",
        ), mock.patch.object(
            pipeline_main,
            "ocr_image_text",
            return_value="should-not-run",
        ) as ocr_text_mock, mock.patch.object(
            pipeline_main,
            "postprocess_and_retry",
            side_effect=_fake_postprocess_and_retry,
        ):
            result = pipeline_main._run_template_roi_extraction(
                template_context={
                    "template_id": "tpl-test",
                    "template": {"postprocess": {"qty_ocr_engine": "tesseract_digits"}},
                    "warped_ocr_bgr": sample_image,
                }
            )

        self.assertEqual(captured["qty"], "42")
        self.assertEqual(captured["menu"], "")
        ocr_text_mock.assert_not_called()
        self.assertEqual(result["template_id"], "tpl-test")

    def test_template_roi_extraction_can_force_large_text_ocr_engine(self):
        sample_image = object()
        captured: dict[str, object] = {}

        def _fake_postprocess_and_retry(*, rois, tpl_cfg, ocr_fn, base_prompt=""):  # noqa: ARG001
            captured["qty"] = ocr_fn(sample_image, "", 32)
            captured["menu"] = ocr_fn(sample_image, "", 512)
            return {"qty": {}, "qty_row_order": [], "qty_col_order": []}

        with mock.patch.object(pipeline_main, "ocr_image_words", return_value=[]), mock.patch.object(
            pipeline_main,
            "crop_rois",
            return_value={"qty_cells": [], "qty_schema": {"rows": 0, "cols": 0}},
        ), mock.patch.object(
            pipeline_main,
            "_tesseract_digits_text",
            return_value="42",
        ), mock.patch.object(
            pipeline_main,
            "ocr_image_text",
            return_value="menu-text",
        ) as ocr_text_mock, mock.patch.object(
            pipeline_main,
            "postprocess_and_retry",
            side_effect=_fake_postprocess_and_retry,
        ):
            result = pipeline_main._run_template_roi_extraction(
                template_context={
                    "template_id": "tpl-test",
                    "template": {
                        "postprocess": {
                            "qty_ocr_engine": "tesseract_digits",
                            "text_ocr_engine": "yomitoku",
                        }
                    },
                    "warped_ocr_bgr": sample_image,
                }
            )

        self.assertEqual(captured["qty"], "42")
        self.assertEqual(captured["menu"], "menu-text")
        ocr_text_mock.assert_called_once_with(sample_image, device=pipeline_main.YOMITOKU_DEVICE)
        self.assertEqual(result["template_id"], "tpl-test")


if __name__ == "__main__":
    unittest.main()
