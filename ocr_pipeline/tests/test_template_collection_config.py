from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.rois import default_template_collection, load_template_config
from app.template_match import choose_template_and_warp


class _Doc:
    exists = True

    def __init__(self, payload: dict):
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class _Collection:
    def __init__(self, name: str, recorder: list[str]):
        self.name = name
        self.recorder = recorder

    def document(self, _template_id: str):
        self.recorder.append(self.name)
        return self

    def get(self):
        return _Doc({"label": "ok"})


class _DB:
    def __init__(self):
        self.collections: list[str] = []

    def collection(self, name: str):
        return _Collection(name, self.collections)


class TemplateCollectionConfigTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("OCR_TEMPLATE_COLLECTION", None)
        os.environ.pop("TEMPLATE_COLLECTION", None)

    def test_default_template_collection_prefers_ocr_template_collection(self):
        os.environ["OCR_TEMPLATE_COLLECTION"] = "templates-stg"
        os.environ["TEMPLATE_COLLECTION"] = "templates-fallback"

        self.assertEqual(default_template_collection(), "templates-stg")

    def test_load_template_config_uses_env_selected_collection(self):
        os.environ["OCR_TEMPLATE_COLLECTION"] = "templates-stg"
        db = _DB()

        with patch("app.rois._load_template_config_from_registry", return_value=None):
            cfg = load_template_config(db, "tpl-1")

        self.assertEqual(cfg["id"], "tpl-1")
        self.assertEqual(db.collections, ["templates-stg"])

    def test_choose_template_defaults_to_env_collection(self):
        os.environ["OCR_TEMPLATE_COLLECTION"] = "templates-preview"

        with patch("app.template_match._template_sources", return_value=[]) as mock_sources:
            with self.assertRaisesRegex(RuntimeError, "No templates registered"):
                choose_template_and_warp(object(), None, None)

        self.assertEqual(mock_sources.call_args[0][1], "templates-preview")


if __name__ == "__main__":
    unittest.main()
