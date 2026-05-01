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
    pdf_render_module.render_pdf_to_page_images = lambda *args, **kwargs: []
    sys.modules.setdefault("app.pdf_render", pdf_render_module)

    postprocess_module = types.ModuleType("app.postprocess")
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
    yomitoku_runner_module.prewarm_analyzer = lambda *args, **kwargs: None
    yomitoku_runner_module.run_yomitoku = lambda *args, **kwargs: ([], None, None)
    sys.modules.setdefault("app.yomitoku_runner", yomitoku_runner_module)


_install_stub_modules()
pipeline_main = importlib.import_module("app.main")


class MainArtifactTests(unittest.TestCase):
    def test_handler_writes_failed_payload_when_run_yomitoku_aborts(self):
        class _FakeJobRef:
            id = "job-doc"

            def __init__(self):
                self.set_payload = None
                self.updates = []

            def get(self):
                return types.SimpleNamespace(exists=False)

            def set(self, payload):
                self.set_payload = dict(payload)

            def update(self, payload):
                self.updates.append(dict(payload))

        class _FakeCollection:
            def __init__(self, job_ref):
                self._job_ref = job_ref

            def document(self, _doc_id):
                return self._job_ref

        class _FakeDB:
            def __init__(self, job_ref):
                self._job_ref = job_ref

            def collection(self, _name):
                return _FakeCollection(self._job_ref)

        class _FakeBlob:
            def __init__(self):
                self.metadata = {}

            def reload(self):
                return None

            def download_as_bytes(self):
                return b"%PDF-1.4\n%EOF\n"

        class _FakeBucket:
            def __init__(self, blob):
                self._blob = blob

            def blob(self, _name):
                return self._blob

        class _FakeGCS:
            def __init__(self, blob):
                self._blob = blob

            def bucket(self, _name):
                return _FakeBucket(self._blob)

        job_ref = _FakeJobRef()
        partial_calls = []

        with mock.patch.object(
            pipeline_main,
            "request",
            types.SimpleNamespace(
                get_json=lambda *args, **kwargs: {
                    "bucket": "bucket",
                    "name": "input/test.pdf",
                    "generation": "1",
                }
            ),
        ), mock.patch.object(
            pipeline_main,
            "db",
            _FakeDB(job_ref),
        ), mock.patch.object(
            pipeline_main,
            "gcs",
            _FakeGCS(_FakeBlob()),
        ), mock.patch.object(
            pipeline_main,
            "_write_output_partial",
            side_effect=lambda **kwargs: partial_calls.append(dict(kwargs)),
        ), mock.patch.object(
            pipeline_main,
            "_run_template_classification",
            return_value=(None, None),
        ), mock.patch.object(
            pipeline_main,
            "correct_pdf_for_yomitoku",
            return_value=(b"%PDF-1.4\n%EOF\n", {"applied": False, "corrected_pdf_generated": False}, []),
        ), mock.patch.object(
            pipeline_main,
            "run_yomitoku",
            side_effect=SystemExit(1),
        ):
            with self.assertRaises(SystemExit):
                pipeline_main.handler()

        self.assertTrue(partial_calls)
        self.assertEqual(partial_calls[-1]["status"], "failed")
        self.assertEqual(partial_calls[-1]["stage"], "error")
        self.assertEqual(partial_calls[-1]["payload"]["error_type"], "SystemExit")
        self.assertTrue(job_ref.updates)
        self.assertEqual(job_ref.updates[-1]["status"], "failed")
        self.assertEqual(job_ref.updates[-1]["error_type"], "SystemExit")

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

    def test_template_roi_extraction_rejects_removed_qty_engine(self):
        sample_image = object()

        def _fake_postprocess_and_retry(*, rois, tpl_cfg, ocr_fn, base_prompt=""):  # noqa: ARG001
            return {"qty": {}, "qty_row_order": [], "qty_col_order": []}

        with mock.patch.object(pipeline_main, "ocr_image_words", return_value=[]), mock.patch.object(
            pipeline_main,
            "crop_rois",
            return_value={"qty_cells": [], "qty_schema": {"rows": 0, "cols": 0}},
        ), mock.patch.object(
            pipeline_main,
            "ocr_image_text",
            return_value="should-not-run",
        ) as ocr_text_mock, mock.patch.object(
            pipeline_main,
            "postprocess_and_retry",
            side_effect=_fake_postprocess_and_retry,
        ):
            with self.assertRaisesRegex(RuntimeError, "removed"):
                pipeline_main._run_template_roi_extraction(
                    template_context={
                        "template_id": "tpl-test",
                        "template": {"postprocess": {"qty_ocr_engine": "tesseract_digits"}},
                        "warped_ocr_bgr": sample_image,
                    }
                )
        ocr_text_mock.assert_not_called()

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
                            "qty_ocr_engine": "yomitoku",
                            "text_ocr_engine": "yomitoku",
                        }
                    },
                    "warped_ocr_bgr": sample_image,
                }
            )

        self.assertEqual(captured["qty"], "menu-text")
        self.assertEqual(captured["menu"], "menu-text")
        self.assertEqual(ocr_text_mock.call_count, 2)
        self.assertEqual(result["template_id"], "tpl-test")

    def test_template_roi_extraction_skips_ocr_words_when_column_edges_are_defined(self):
        sample_image = object()

        with mock.patch.object(
            pipeline_main,
            "ocr_image_words",
            side_effect=AssertionError("ocr_image_words should not run"),
        ), mock.patch.object(
            pipeline_main,
            "crop_rois",
            return_value={"qty_cells": [], "qty_schema": {"rows": 0, "cols": 3}},
        ), mock.patch.object(
            pipeline_main,
            "postprocess_and_retry",
            return_value={"qty": {}, "qty_row_order": [], "qty_col_order": []},
        ):
            result = pipeline_main._run_template_roi_extraction(
                template_context={
                    "template_id": "tpl-test",
                    "template": {
                        "auto_headers": [{"name": "regular_x", "match_groups": [["常食"]]}],
                        "rois": {
                            "qty": {
                                "column_edges": [0.4, 0.5, 0.6, 0.7],
                            }
                        },
                    },
                    "warped_ocr_bgr": sample_image,
                }
            )

        self.assertEqual(result["template_id"], "tpl-test")

    def test_template_roi_extraction_uses_ocr_words_when_auto_headers_need_inference(self):
        sample_image = object()

        with mock.patch.object(
            pipeline_main,
            "ocr_image_words",
            return_value=[{"text": "常食", "x": 0.45, "y": 0.2}],
        ) as ocr_words_mock, mock.patch.object(
            pipeline_main,
            "crop_rois",
            return_value={"qty_cells": [], "qty_schema": {"rows": 0, "cols": 1}},
        ), mock.patch.object(
            pipeline_main,
            "postprocess_and_retry",
            return_value={"qty": {}, "qty_row_order": [], "qty_col_order": []},
        ):
            result = pipeline_main._run_template_roi_extraction(
                template_context={
                    "template_id": "tpl-test",
                    "template": {
                        "auto_headers": [{"name": "regular_x", "match_groups": [["常食"]]}],
                        "rois": {
                            "qty": {},
                        },
                    },
                    "warped_ocr_bgr": sample_image,
                }
            )

        ocr_words_mock.assert_called_once_with(sample_image, device=pipeline_main.YOMITOKU_DEVICE)
        self.assertEqual(result["template_id"], "tpl-test")


if __name__ == "__main__":
    unittest.main()
