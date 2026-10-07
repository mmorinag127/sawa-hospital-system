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
    def test_handler_required_stage_failures_are_owned_terminal_not_done(self):
        import json
        from contextlib import ExitStack
        from terminal_store_fakes import Stores, install_transport_exceptions
        for stage in ("classification", "roi", "correction"):
            with self.subTest(stage=stage), ExitStack() as stack:
                stores = Stores()
                stack.enter_context(install_transport_exceptions())
                patches = {
                    "db": stores, "gcs": stores, "request": types.SimpleNamespace(get_json=lambda **_: {}),
                    "INPUT_PREFIX": "input/", "parse_gcs_event": lambda _: ("own-bucket", "input/own.pdf", "19"),
                    "OCR_ENABLE_TEMPLATE_MATCH": True, "OCR_ENABLE_TEMPLATE_ROI": True, "OCR_ENABLE_PAGE_CORRECTION": True,
                    "run_yomitoku": lambda **_: self.fail("recognizer after mandatory failure"),
                }
                for name, value in patches.items():
                    stack.enter_context(mock.patch.object(pipeline_main, name, value))
                classify = stack.enter_context(mock.patch.object(pipeline_main, "_run_template_classification", return_value=({"matched_template_id": "own-template"}, {"template": stores.configured["facility"]["fax_template"]})))
                roi = stack.enter_context(mock.patch.object(pipeline_main, "_run_template_roi_extraction", return_value={"qty": {"own": 0}}))
                correction = stack.enter_context(mock.patch.object(pipeline_main, "correct_pdf_for_yomitoku"))
                {"classification": classify, "roi": roi, "correction": correction}[stage].side_effect = ValueError("required computation failed")
                _, status = pipeline_main.handler()
                writes = [json.loads(value) for name, value in stores.writes if name == stores.output]
                self.assertEqual(status, 200)
                self.assertEqual(writes[-1]["status"], "failed")
                self.assertFalse(writes[-1]["canonical_adoption_allowed"])
                self.assertEqual(writes[-1]["input_generation"], "19")
                self.assertFalse(any(write["status"] == "done" for write in writes))
                self.assertEqual(stores.job["status"], "failed")
                self.assertEqual(stores.job["terminal_receipt"]["result_digest"], pipeline_main._terminal_digest(writes[-1]))

    def test_captured_roi_template_reaches_actual_loader_without_firestore(self):
        import hashlib
        import json
        import numpy as np
        from app.rois import crop_rois
        configured = {"facility": {"facility_id": "fixture", "fax_template": {
            "template_id": "own-template", "rois": {"table_box": [0, 0, 1, 1]}, "zero": 0, "unknown": None,
        }}, "policy": {"quantity_rules": {"minimum": 0}}}
        digest = hashlib.sha256(json.dumps(configured, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        forbidden_db = types.SimpleNamespace(collection=lambda *_: self.fail("runtime Firestore template substituted"))
        def match(*args, **kwargs):
            self.assertEqual(kwargs["authored_templates"], {"own-template": configured["facility"]["fax_template"]})
            return "own-template", image, image, image, {}
        with mock.patch.object(pipeline_main, "db", forbidden_db), \
             mock.patch.object(pipeline_main, "OCR_ENABLE_TEMPLATE_MATCH", True), \
             mock.patch.object(pipeline_main, "render_pdf_to_png_bytes", return_value=b"png"), \
             mock.patch.object(pipeline_main, "build_images_for_match_and_ocr", return_value=(image, image, image)), \
             mock.patch.object(pipeline_main, "choose_template_and_warp", side_effect=match):
            _, context = pipeline_main._run_template_classification(pdf_bytes=b"own", requested_template_id="own-template",
                                captured_config=configured, configured_digest=digest)
        self.assertEqual(context["template"], configured["facility"]["fax_template"])
        rois = crop_rois(context["warped_ocr_bgr"], context["template"])
        self.assertEqual(rois["table"].shape, image.shape)
        self.assertFalse(rois["table"].any())
        with self.assertRaisesRegex(ValueError, "captured_template_receipt_mismatch"):
            pipeline_main.load_template_config(forbidden_db, "own-template", captured_config=configured, expected_digest="wrong")

    def test_full_configured_sidecar_is_generation_pinned_and_content_verified(self):
        import json
        import hashlib
        configured = {"facility": {"fax_template": {"template_id": "own", "zero": 0, "unknown": None}}, "policy": {}}
        data = json.dumps(configured, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        receipt = {"bucket": "own-bucket", "object_name": "input/own.pdf.configured.json", "generation": "17",
                   "sha256": hashlib.sha256(data).hexdigest()}
        event_blob = types.SimpleNamespace(name="input/own.pdf", bucket=types.SimpleNamespace(name="own-bucket"),
                                          metadata={"configured_input_receipt": json.dumps(receipt)})
        source = mock.Mock()
        source.download_as_bytes.return_value = data
        bucket = mock.Mock()
        bucket.blob.return_value = source
        with mock.patch.object(pipeline_main.gcs, "bucket", return_value=bucket):
            result, digest = pipeline_main._load_configured_input(event_blob)
            self.assertEqual(result, configured)
            self.assertEqual(digest, receipt["sha256"])
            bucket.blob.assert_called_once_with(receipt["object_name"], generation=17)
            source.download_as_bytes.assert_called_once_with(if_generation_match=17, timeout=30, retry=None)
            source.download_as_bytes.return_value = b"different-config"
            with self.assertRaisesRegex(ValueError, "captured_template_receipt_mismatch"):
                pipeline_main._load_configured_input(event_blob)

    def test_noop_producer_receipt_is_consumed_by_reparse_without_correction_fallback(self):
        import hashlib
        import tempfile
        sys.path.insert(0, str(ROOT.parent / "backend"))
        from src.services import order_service
        from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "own.pdf"
            original = b"own-original-zero"
            path.write_bytes(original)
            source = {"storage_uri": str(path), "generation": None, "sha256": hashlib.sha256(original).hexdigest()}
            declaration = pipeline_main._recognition_input_receipt(original, original, source, None)
            with nonworkflow_execution_context({"selected_pdf_receipt": source}):
                result, meta = order_service._resolve_reparse_llm_pdf_bytes(
                    document_uri=str(path), payload={"page_correction_artifacts": {"recognition_input": declaration}},
                )
            self.assertEqual(result, original)
            self.assertEqual(result.selected_pdf_receipt, source)
            self.assertEqual(meta["used"], "raw")
            self.assertIsNone(meta["fallback_reason"])

    def test_recognition_input_declares_noop_original_and_real_corrected(self):
        import hashlib
        source = {"storage_uri": "gs://own/source.pdf", "sha256": hashlib.sha256(b"original0").hexdigest()}
        original = pipeline_main._recognition_input_receipt(b"original0", b"original0", source, None)
        self.assertEqual(original, {"representation": "original_identity", "receipt": source})
        child = {"storage_uri": "gs://own/corrected.pdf?generation=2", "generation": "2",
                 "source_sha256": source["sha256"], "sha256": hashlib.sha256(b"corrected0").hexdigest()}
        self.assertEqual(pipeline_main._recognition_input_receipt(b"original0", b"corrected0", source, child),
                         {"representation": "corrected", "receipt": child})
        with self.assertRaisesRegex(ValueError, "recognition_input_receipt_required"):
            pipeline_main._recognition_input_receipt(b"original0", b"changed", source, None)

    def test_input_generation_is_pinned_and_never_metageneration(self):
        self.assertEqual(pipeline_main.parse_gcs_event({"bucket": "bucket", "name": "input/pdf", "generation": "42"}),
                         ("bucket", "input/pdf", "42"))
        with self.assertRaises(ValueError):
            pipeline_main.parse_gcs_event({"bucket": "bucket", "name": "input/pdf", "metageneration": "42"})
        blob = mock.Mock()
        blob.download_as_bytes.return_value = b"own-pdf"
        blob.metadata = {"selected_pdf_receipt": json.dumps({
            "storage_uri": "local-authoritative-upload.pdf", "generation": None,
            "sha256": pipeline_main.hashlib.sha256(b"own-pdf").hexdigest(),
        })}
        bucket = mock.Mock()
        bucket.blob.return_value = blob
        gcs = mock.Mock()
        gcs.bucket.return_value = bucket
        with mock.patch.object(pipeline_main, "gcs", gcs):
            _, data = pipeline_main._download_generation_input("bucket", "input/pdf", "42")
            self.assertEqual(data, b"own-pdf")
            bucket.blob.assert_called_once_with("input/pdf", generation=42)
            blob.download_as_bytes.assert_called_once_with(if_generation_match=42)
            blob.download_as_bytes.side_effect = ValueError("generation changed")
            with self.assertRaises(ValueError):
                pipeline_main._download_generation_input("bucket", "input/pdf", "42")

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
                self.metadata = {"selected_pdf_receipt": json.dumps({
                    "storage_uri": "local-authoritative-upload.pdf", "generation": None,
                    "sha256": pipeline_main.hashlib.sha256(b"%PDF-1.4\n%EOF\n").hexdigest(),
                })}

            def reload(self):
                return None

            def download_as_bytes(self, *, if_generation_match):
                self_outer.assertEqual(if_generation_match, 1)
                return b"%PDF-1.4\n%EOF\n"

        class _FakeBucket:
            def __init__(self, blob):
                self._blob = blob

            def blob(self, _name, *, generation):
                self_outer.assertEqual(generation, 1)
                return self._blob

        class _FakeGCS:
            def __init__(self, blob):
                self._blob = blob

            def bucket(self, _name):
                return _FakeBucket(self._blob)

        job_ref = _FakeJobRef()
        self_outer = self
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
        blob = mock.Mock(generation=42)
        gcs = mock.Mock()
        gcs.bucket.return_value.blob.return_value = blob
        summary = {"applied": True, "corrected_pdf_generated": True}
        with mock.patch.object(pipeline_main, "gcs", gcs):
            uri = pipeline_main._upload_corrected_pdf_artifact(
                bucket="bucket",
                artifact_prefix="output/doc/",
                base="doc",
                original_pdf_bytes=b"%PDF-raw\n%EOF\n",
                corrected_pdf_bytes=b"%PDF-corrected\n%EOF\n",
                page_correction_summary=summary,
            )

        self.assertEqual(uri, "gs://bucket/output/doc/doc_corrected.pdf?generation=42")
        gcs.bucket.return_value.blob.assert_called_once_with("output/doc/doc_corrected.pdf")
        blob.upload_from_string.assert_called_once_with(
            b"%PDF-corrected\n%EOF\n",
            content_type="application/pdf",
        )
        self.assertEqual(summary["corrected_pdf_receipt"]["generation"], "42")
        self.assertEqual(summary["corrected_pdf_receipt"]["source_sha256"], pipeline_main.hashlib.sha256(b"%PDF-raw\n%EOF\n").hexdigest())

    def test_selected_pdf_input_rejects_missing_changed_and_foreign_generation(self):
        data = b"original"
        receipt = {"storage_uri": "gs://canonical/selected.pdf?generation=7", "generation": "7",
                   "sha256": pipeline_main.hashlib.sha256(data).hexdigest()}
        original = mock.Mock(generation=7)
        gcs = mock.Mock()
        gcs.bucket.return_value.blob.return_value = original
        blob = mock.Mock(metadata={"selected_pdf_receipt": json.dumps(receipt)})
        with mock.patch.object(pipeline_main, "gcs", gcs):
            pipeline_main._verify_selected_pdf_input(blob, data)
            original.generation = 8
            with self.assertRaisesRegex(ValueError, "selected_pdf_generation_changed"):
                pipeline_main._verify_selected_pdf_input(blob, data)
            original.generation = 7
            with self.assertRaisesRegex(ValueError, "selected_pdf_bytes_changed"):
                pipeline_main._verify_selected_pdf_input(blob, b"foreign")
            blob.metadata = {}
            with self.assertRaisesRegex(ValueError, "selected_pdf_start_receipt_required"):
                pipeline_main._verify_selected_pdf_input(blob, data)

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
