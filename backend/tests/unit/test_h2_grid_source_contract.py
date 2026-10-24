"""Source behavior definitions; no OCR, DB or network required by these cases."""
import ast
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import tempfile
import re
import json
import os
import time
import logging
import hashlib
import threading
from contextvars import ContextVar
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from types import SimpleNamespace
from typing import Any, Iterable, Optional, Tuple
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
import pytest
from src import staging_data as datasets

ROOT = Path(__file__).resolve().parents[3]
GRID = ROOT / "backend/src/services/grid_detector.py"
HAKODATE = ROOT / "backend/src/services/hakodate_assignment_service.py"
RUNNER = ROOT / "ocr_pipeline/app/yomitoku_runner.py"
MAIN = ROOT / "ocr_pipeline/app/main.py"
URI = ROOT / "ocr_pipeline/app/storage_uri.py"
_uri_spec = spec_from_file_location("source_case_storage_uri", URI)
_uri_codec = module_from_spec(_uri_spec)
_uri_spec.loader.exec_module(_uri_codec)
_markup_spec = spec_from_file_location("source_case_figure_markup", ROOT / "ocr_pipeline/app/figure_markup.py")
_figure_markup = module_from_spec(_markup_spec)
_markup_spec.loader.exec_module(_figure_markup)
URI_BINDINGS = {name: getattr(_uri_codec, name) for name in (
    "format_gcs_uri", "parse_gcs_uri", "decorate_gcs_uri", "validate_generation", "validate_source_query", "signing_query", "staging_output_prefix")}


def definitions(path, names, bindings):
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    namespace = {"__name__": "h2_source_case", "dataclass": dataclass, "BytesIO": BytesIO,
                 "Path": Path, "Any": Any, "Iterable": Iterable, "Optional": Optional,
                 "Tuple": Tuple, "np": np, "Image": Image, "tempfile": tempfile,
                 "figure_markup": _figure_markup, "datasets": datasets, **URI_BINDINGS, **bindings}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def owned_pipeline(stores=None, names=(), bindings=None, claim=True):
    """Integrated producer definitions and real receipt/claim validation.

    No legacy uploader alternative: this fixture requires the parent's retained
    H2 implementation and conditional transport fixture in the integrated tree.
    """
    spec = spec_from_file_location("h2_conditional_transport", ROOT / "ocr_pipeline/tests/terminal_store_fakes.py")
    transport = module_from_spec(spec)
    spec.loader.exec_module(transport)
    stores = stores or transport.Stores()
    validator = definitions(ROOT / "ocr_pipeline/app/rois.py", ["validate_captured_config"],
                            {"hashlib": hashlib, "json": json, "deepcopy": deepcopy})["validate_captured_config"]
    required = ["TerminalPersistenceIncomplete", "TerminalReceiptConflict", "MandatoryOcrStageFailed",
                "_owned_output_context", "_assert_output_owner", "_terminal_digest", "_same_original_source",
                "_original_pipeline_source", "_terminal_source", "_owned_output_name", "_download_generation_input", "_verify_selected_pdf_input",
                "_load_configured_input", "_upload_bytes"]
    namespace = definitions(MAIN, list(dict.fromkeys(required + list(names))), {
        "hashlib": hashlib, "json": json, "re": re, "os": os, "time": time,
        "gcs": stores, "db": stores, "validate_captured_config": validator,
        "_OUTPUT_OWNER": ContextVar("source_case_original_owner", default=None),
        "_ACTIVE_PRODUCER_JOBS": set(), "_ACTIVE_PRODUCER_LOCK": threading.Lock(),
        **(bindings or {})})
    with transport.install_transport_exceptions():
        source = namespace["_original_pipeline_source"]("own-bucket", stores.name, "19")
    assert "output_reference" not in source
    stores.output = namespace["_owned_output_name"]("output/", source)
    source["output_reference"] = _uri_codec.format_gcs_uri("own-bucket", stores.output)
    if claim:
        stores.create({"status": "running", "execution_claim": {
            "job_id": source["job_id"], "output_reference": source["output_reference"]}})
        namespace["_OUTPUT_OWNER"].set((source, stores))
    return namespace, stores, source, transport


@pytest.mark.parametrize("name,path", [("detect_table_grid", GRID), ("_render_pdf_page_image", HAKODATE)])
def test_exact_page_and_missing_page_stop(name, path):
    calls = []
    image = Image.new("RGB", (100, 100))
    pages = [SimpleNamespace(width=100, height=100, to_image=lambda **kw: calls.append(1) or SimpleNamespace(original=image)),
             SimpleNamespace(width=100, height=100, to_image=lambda **kw: calls.append(2) or SimpleNamespace(original=image))]
    class Pdf:
        def __init__(self, values): self.pages = values
        def __enter__(self): return self
        def __exit__(self, *args): pass
    namespace = definitions(path, ["GridDetectionResult", name] if path == GRID else [name], {"_cap_detection_dpi": lambda dpi, **kw: dpi,
        "_downscale_image_if_needed": lambda value: value, "_detect_grid_from_image": lambda value, template: value})
    with patch("pdfplumber.open", return_value=Pdf(pages)):
        assert namespace[name](b"pdf transport", {"page": 2}).size == image.size
        assert calls == [2]
        for page in [3, 0, -1, 1.5, True, None, "", "not-a-page"]:
            with pytest.raises(ValueError): namespace[name](b"pdf transport", {"page": page})
        assert calls == [2]
        namespace[name](b"pdf transport", {})
        assert calls == [2, 1]
    with patch("pdfplumber.open", return_value=Pdf([])):
        with pytest.raises(ValueError): namespace[name](b"pdf transport", {"page": 1})


@pytest.mark.parametrize("roi", [[.1, .05, .9, .95], [.05, .05, 1., 1.]])
def test_actual_roi_projection_equals_same_crop_translated_once(roi):
    tree = ast.parse(GRID.read_text())
    names = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    namespace = definitions(GRID, names, {"_GRID_DETECTION_MAX_PIXELS": 24000000, "_GRID_DETECTION_MIN_DPI": 96})
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    for x in [180, 350, 520, 690, 820]: draw.line((x, 150, x, 850), fill="black", width=5)
    for y in [150, 320, 490, 660, 850]: draw.line((180, y, 820, y), fill="black", width=5)
    bounds = namespace["_normalize_region"](roi, 1000, 1000)
    x0, y0, x1, y1 = bounds
    template = {"grid_expected_columns": 4, "grid_expected_rows": 4}
    local = namespace["_auto_detect_grid"](image.crop(bounds), template)
    actual = namespace["_auto_detect_grid"](image, {**template, "grid_table_search_region": roi})
    assert local is not None and actual is not None
    expected_box = [(local[0][0]*(x1-x0)+x0)/1000, (local[0][1]*(y1-y0)+y0)/1000,
                    (local[0][2]*(x1-x0)+x0)/1000, (local[0][3]*(y1-y0)+y0)/1000]
    assert actual[0] == pytest.approx(expected_box)
    assert actual[1] == pytest.approx([(value*(x1-x0)+x0)/1000 for value in local[1]])
    assert actual[2] == pytest.approx([(value*(y1-y0)+y0)/1000 for value in local[2]])
    assert actual[0][1] >= .05


def test_actual_figure_producer_and_consumer_survive_temp_cleanup():
    created = []
    png = BytesIO()
    Image.new("RGB", (3, 2), (1, 2, 3)).save(png, format="PNG")
    content = png.getvalue()
    class Recognition:
        def to_markdown(self, filename, **kwargs):
            directory = Path(filename).parent / kwargs["figure_dir"]
            directory.mkdir(exist_ok=True)
            (directory / "figure.png").write_bytes(content)
            Path(filename).write_text("recognized 0/null ![figure](figures/figure.png)")
            created.append(directory)
    analyzer = lambda image: (Recognition(), None, None)
    runner = definitions(RUNNER, ["FigureData", "PageResult", "run_yomitoku"], {
        "_get_analyzer": lambda *args: analyzer, "_serialize_results": lambda value: {},
        "_extract_tables": lambda *args, **kwargs: [{"recognized": "0/null"}]})
    pages, ocr, layout = runner["run_yomitoku"](pdf_bytes=None, dpi=300, device="cpu", visualize=False,
        ignore_line_break=False, no_figure=False, figure_width=100, figure_dir="figures",
        page_images=[(2, np.zeros((10, 10, 3), dtype=np.uint8))])
    assert not created[0].exists()
    assert pages[0].markdown_text == "recognized 0/null ![figure](figures/figure.png)"
    assert pages[0].tables == [{"recognized": "0/null"}]
    assert pages[0].figures[0].name == "figure.png"
    assert pages[0].figures[0].content == content
    assert ocr is None and layout is None
    uploaded = []
    consumer = definitions(MAIN, ["_upload_figure_artifact"], {
        "_upload_bytes": lambda *args: uploaded.append(args) or "gs://bucket/figure.png"})
    assert consumer["_upload_figure_artifact"]("bucket", "figure.png", pages[0].figures[0]) == "gs://bucket/figure.png"
    assert uploaded == [("bucket", "figure.png", content, "image/png")]


def page_publisher(uploaded):
    return definitions(MAIN, ["_publish_page_figures", "_upload_figure_artifact",
                               "_extract_markdown_images", "_replace_markdown_images", "_image_reference_spans"], {
        "re": re,
        "_upload_bytes": lambda bucket, name, content, mime: uploaded.append((name, content, mime)) or _uri_codec.format_gcs_uri(bucket, name)
    })["_publish_page_figures"]


@pytest.mark.parametrize("names", [("same.png", "same.png"), ("first.png", "second.png"), ("a/same.png", "b/same.png")])
def test_actual_two_page_capture_upload_match_replace_after_cleanup(names):
    captured_paths = []
    contents = []
    for color in [(1, 2, 3), (4, 5, 6)]:
        stream = BytesIO()
        Image.new("RGB", (3, 2), color).save(stream, format="PNG")
        contents.append(stream.getvalue())
    class Recognition:
        def __init__(self, index): self.index = index
        def to_markdown(self, filename, **kwargs):
            directory = Path(filename).parent / kwargs["figure_dir"]
            directory.mkdir()
            asset = directory / names[self.index]
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(contents[self.index])
            ref = f"figures/{names[self.index]}"
            Path(filename).write_text(f"raw page {self.index} 0/null ![a]({ref}) ![repeat]({ref})")
            captured_paths.append(directory)
    runner = definitions(RUNNER, ["FigureData", "PageResult", "run_yomitoku"], {
        "_get_analyzer": lambda *args: lambda image: (Recognition(int(image[0, 0, 0])), None, None),
        "_serialize_results": lambda result: {"index": result.index},
        "_extract_tables": lambda result, **kwargs: [{"page": result["index"], "raw": "0/null"}]})
    pages, _, _ = runner["run_yomitoku"](pdf_bytes=None, dpi=300, device="cpu", visualize=False,
        ignore_line_break=False, no_figure=False, figure_width=100, figure_dir="figures",
        page_images=[(1, np.zeros((10, 10, 3), dtype=np.uint8)), (2, np.ones((10, 10, 3), dtype=np.uint8))])
    assert all(not directory.exists() for directory in captured_paths)
    uploaded = []
    publish = page_publisher(uploaded)
    for index, page in enumerate(pages):
        markdown, count, assets = publish("bucket", "assets/", page)
        uri = f"gs://bucket/assets/p{index+1}/figures/{names[index]}"
        assert markdown == f"raw page {index} 0/null ![a]({uri}) ![repeat]({uri})"
        assert count == 1
        assert page.tables == [{"page": index, "raw": "0/null"}]
    assert uploaded == [(f"assets/p1/figures/{names[0]}", contents[0], "image/png"),
                        (f"assets/p2/figures/{names[1]}", contents[1], "image/png")]


def test_actual_page_publication_rejects_missing_or_ambiguous_identity_before_upload():
    uploaded = []
    publish = page_publisher(uploaded)
    figure = SimpleNamespace(name="same.png", reference="figures/same.png", content=b"own", mime_type="image/png")
    page = SimpleNamespace(page_index=2, figures=[figure], markdown_text="![raw](figures/missing.png)")
    with pytest.raises(ValueError, match="figure missing on page 2"):
        publish("bucket", "assets/", page)
    page.markdown_text = "![raw](figures/same.png)"
    page.figures = [figure, SimpleNamespace(name="same.png", reference="figures/same.png", content=b"other", mime_type="image/png")]
    with pytest.raises(ValueError, match="ambiguous figure identity"):
        publish("bucket", "assets/", page)
    assert uploaded == []
    page.figures = [figure, figure]
    markdown, count, assets = publish("bucket", "assets/", page)
    assert count == 1 and markdown == "![raw](gs://bucket/assets/p2/figures/same.png)"


@pytest.mark.parametrize("collision", [False, True])
def test_actual_external_references_are_unchanged_even_with_local_collision(collision):
    uploaded = []
    publish = page_publisher(uploaded)
    raw = "![http](https://example.test/figures/same.png) ![data](data:image/png;base64,AAAA) ![cdn](//example.test/same.png)"
    figures = [SimpleNamespace(name="same.png", reference="figures/same.png", content=b"own", mime_type="image/png")] if collision else []
    page = SimpleNamespace(page_index=1, figures=figures, markdown_text=raw)
    assert publish("bucket", "assets/", page) == (raw, 0, {})
    assert uploaded == []
    if collision:
        page.markdown_text += " ![local](figures/same.png)"
        markdown, count, assets = publish("bucket", "assets/", page)
        assert markdown == raw + " ![local](gs://bucket/assets/p1/figures/same.png)"
        assert count == 1


def test_actual_exact_paths_distinguish_directories_and_mime_only_ambiguity():
    uploaded = []
    publish = page_publisher(uploaded)
    a = SimpleNamespace(name="same.png", reference="figures/a/same.png", content=b"a", mime_type="image/png")
    b = SimpleNamespace(name="same.png", reference="figures/b/same.png", content=b"b", mime_type="image/png")
    page = SimpleNamespace(page_index=1, figures=[a, b], markdown_text="![a](figures/a/same.png) ![b](figures/b/same.png)")
    markdown, count, assets = publish("bucket", "assets/", page)
    assert (markdown, count) == ("![a](gs://bucket/assets/p1/figures/a/same.png) ![b](gs://bucket/assets/p1/figures/b/same.png)", 2)
    assert uploaded == [("assets/p1/figures/a/same.png", b"a", "image/png"), ("assets/p1/figures/b/same.png", b"b", "image/png")]
    uploaded.clear()
    page.figures = [a, SimpleNamespace(**{**vars(a), "mime_type": "image/jpeg"})]
    with pytest.raises(ValueError, match="ambiguous figure identity"):
        publish("bucket", "assets/", page)
    assert uploaded == []
    page.figures = [a]
    page.markdown_text = "![alias](same.png)"
    with pytest.raises(ValueError, match="figure missing"):
        publish("bucket", "assets/", page)


def test_actual_duplicate_page_preflight_and_empty_figures():
    validate = definitions(MAIN, ["_validate_figure_pages"], {})["_validate_figure_pages"]
    uploaded = []
    publish = page_publisher(uploaded)
    page = SimpleNamespace(page_index=1, figures=[], markdown_text="raw 0/null no image")
    with pytest.raises(ValueError, match="duplicate figure page"):
        validate([page, page])
    assert uploaded == []
    validate([page])
    assert publish("bucket", "assets/", page) == (page.markdown_text, 0, {})


@pytest.mark.parametrize("reference", ["../same.png", "/same.png", "figures/../../same.png"])
def test_actual_local_escape_stops_before_upload(reference):
    uploaded = []
    page = SimpleNamespace(page_index=1, figures=[], markdown_text=f"![escape]({reference})")
    with pytest.raises(ValueError, match="invalid local figure reference"):
        page_publisher(uploaded)("bucket", "assets/", page)
    assert uploaded == []


@pytest.mark.parametrize("directory", ["../figures", "/figures"])
def test_actual_producer_export_directory_escape_stops_before_analyzer(directory):
    runner = definitions(RUNNER, ["FigureData", "PageResult", "run_yomitoku"], {})
    with pytest.raises(ValueError, match="figure_dir must remain"):
        runner["run_yomitoku"](pdf_bytes=None, dpi=300, device="cpu", visualize=False,
            ignore_line_break=False, no_figure=False, figure_width=100, figure_dir=directory, page_images=[])


def test_actual_producer_duplicate_page_stops_before_second_export():
    exports = []
    class Recognition:
        def to_markdown(self, filename, **kwargs):
            exports.append(filename)
            Path(filename).write_text("raw 0/null")
    runner = definitions(RUNNER, ["FigureData", "PageResult", "run_yomitoku"], {
        "_get_analyzer": lambda *args: lambda image: (Recognition(), None, None),
        "_serialize_results": lambda result: {}, "_extract_tables": lambda *args, **kwargs: []})
    with pytest.raises(ValueError, match="duplicate figure page identity"):
        runner["run_yomitoku"](pdf_bytes=None, dpi=300, device="cpu", visualize=False,
            ignore_line_break=False, no_figure=True, figure_width=100, figure_dir="figures",
            page_images=[(1, np.zeros((10, 10, 3))), (1, np.zeros((10, 10, 3)))])
    assert len(exports) == 1


def test_actual_html_and_markdown_mixed_supported_destinations():
    uploaded = []
    figure = SimpleNamespace(name="page_1_figure_0.png", reference="figures/page_1_figure_0.png", content=b"own", mime_type="image/png")
    other = SimpleNamespace(name="name (raw).png", reference="figures/name (raw).png", content=b"other", mime_type="image/png")
    raw = ('<img src="figures/page_1_figure_0.png" width="200px"><br>\n'
           '![same](figures/page_1_figure_0.png "title")\n'
           '![encoded](<figures/name%20(raw).png?q=1#part> "raw title")\n'
           '<img src="https://example.test/figures/page_1_figure_0.png" width="20px"><br>\n'
           '<img src="data:image/png;base64,AAAA" width="30px" alt="![raw](figures/missing.png)">')
    page = SimpleNamespace(page_index=1, figures=[figure, other], markdown_text=raw)
    markdown, count, assets = page_publisher(uploaded)("bucket", "assets/", page)
    assert count == 2
    assert '<img src="gs://bucket/assets/p1/figures/page_1_figure_0.png" width="200px"><br>' in markdown
    assert '![same](gs://bucket/assets/p1/figures/page_1_figure_0.png "title")' in markdown
    assert '![encoded](<gs://bucket/assets/p1/figures/name%20%28raw%29.png?q=1#part> "raw title")' in markdown
    assert raw.splitlines()[-2:] == markdown.splitlines()[-2:]
    assert assets["figures/name%20(raw).png?q=1#part"].endswith("name%20%28raw%29.png?q=1#part")


def test_actual_full_handler_html_page_loop_metadata_tables_and_normal_return():
    contents = []
    directories = []
    for color in [(2, 3, 4), (5, 6, 7)]:
        png = BytesIO(); Image.new("RGB", (2, 2), color).save(png, format="PNG"); contents.append(png.getvalue())
    class Recognition:
        def __init__(self, index): self.index = index
        def to_markdown(self, filename, **kwargs):
            directory = Path(filename).parent / kwargs["figure_dir"]
            directory.mkdir(); directories.append(directory)
            (directory / "page_1_figure_0.png").write_bytes(contents[self.index])
            # Exact HTML format of pinned yomitoku/export/export_markdown.py:97.
            Path(filename).write_text('<img src="figures/page_1_figure_0.png" width="200px"><br>\nraw 0/null')
    runner = definitions(RUNNER, ["FigureData", "PageResult", "run_yomitoku"], {
        "_get_analyzer": lambda *args: lambda image: (Recognition(int(image[0, 0, 0])), None, None),
        "_serialize_results": lambda value: {"index": value.index},
        "_extract_tables": lambda value, **kw: [{"page": kw["page_index"], "cells": [0, None], "raw": "unchanged"}]})
    def provider(**kwargs):
        return runner["run_yomitoku"](**{**kwargs, "pdf_bytes": None,
            "page_images": [(1, np.zeros((10, 10, 3), dtype=np.uint8)), (2, np.ones((10, 10, 3), dtype=np.uint8))]})
    names = ["handler", "parse_gcs_event", "_normalize_prefix", "_safe_job_id", "_update_job_stage",
             "_upload_bytes", "_upload_text", "_upload_figure_artifact", "_publish_page_figures", "_validate_figure_pages",
             "_write_output_partial", "_extract_metadata", "_upload_corrected_pdf_artifact", "_count_non_empty_roi_cells",
             "_extract_markdown_images", "_replace_markdown_images", "_image_reference_spans",
             "_recognition_input_receipt", "_producer_dataset", "_complete_terminal", "_persist_terminal_intent", "_update_owned_terminal_job",
             "_replay_terminal", "_duplicate_outcome", "_terminal_ack"]
    namespace, stores, source, protocol = owned_pipeline(names=names, claim=False, bindings={
        "storage": SimpleNamespace(Blob=Any), "firestore": SimpleNamespace(SERVER_TIMESTAMP="timestamp"),
        "app": SimpleNamespace(logger=logging.getLogger("source_case"), post=lambda *args, **kw: lambda fn: fn),
        "request": SimpleNamespace(get_json=lambda **kw: {"bucket": "own-bucket", "name": "input/own.pdf", "generation": "19"}),
        "jsonify": lambda value: value,
        "INPUT_PREFIX": "input/", "OUTPUT_PREFIX": "output/", "JOB_COLLECTION": "jobs",
        "OCR_ENABLE_PAGE_CORRECTION": False, "OCR_ENABLE_TEMPLATE_ROI": False, "OCR_ENABLE_TEMPLATE_MATCH": False,
        "OCR_ENABLE_QUANTITY_SUBGRID_SECOND_PASS": False,
        "OCR_PIPELINE_MODE": "structured_v2", "YOMITOKU_DPI": 300, "YOMITOKU_DEVICE": "cpu", "YOMITOKU_VIS": False,
        "YOMITOKU_VIS_PDF": False, "YOMITOKU_IGNORE_LINE_BREAK": False, "YOMITOKU_NO_FIGURE": False,
        "YOMITOKU_FIGURE_WIDTH": 200, "YOMITOKU_FIGURE_DIR": "figures", "run_yomitoku": provider,
        "_run_template_classification": lambda **kw: (None, None), "detect_table_cell_issues": lambda **kw: [],
        "merge_cell_issues": lambda a, b: a+b, "build_template_resolution": lambda **kw: kw,
        "ensure_evidence_manifest": lambda value: value})
    uploads = {}
    conditional_upload = protocol.Blob.upload_from_string
    def observed_upload(blob, value, **kwargs):
        result = conditional_upload(blob, value, **kwargs)
        uploads[blob.name] = (value, kwargs["content_type"])
        if "/assets/" in blob.name:
            assert kwargs["if_generation_match"] == 0
        return result
    with protocol.install_transport_exceptions(), patch.object(protocol.Blob, "upload_from_string", observed_upload):
        response, status = namespace["handler"]()
    assert status == 200 and response["status"] == "done"
    assert all(not directory.exists() for directory in directories)
    payload = json.loads(stores.objects[stores.output])
    assert payload["status"] == "done" and payload["metrics"]["figure_count"] == 2
    assert payload["tables"] == [{"page": 1, "cells": [0, None], "raw": "unchanged"}, {"page": 2, "cells": [0, None], "raw": "unchanged"}]
    for index, page in enumerate(payload["pages"], 1):
        prefix = stores.output.rsplit("/", 1)[0] + "/assets/"
        asset = f"{prefix}figures/p{index}/figures/page_1_figure_0.png"
        assert stores.objects[asset] == contents[index-1]
        assert uploads[asset] == (contents[index-1], "image/png")
        assert stores.object_metadata[asset]["sha256"] == hashlib.sha256(contents[index-1]).hexdigest()
        assert json.loads(stores.object_metadata[asset]["original_source"]) == source
        assert page["figure_uris"] == [f"gs://own-bucket/{asset}?generation=2"]
        assert page["tables"] == [payload["tables"][index-1]]
        assert stores.objects[f"{prefix}own_p{index}.md"] == f'<img src="gs://own-bucket/{asset}?generation=2" width="200px"><br>\nraw 0/null'.encode("utf-8")
    assert stores.job["status"] == "done"
    assert stores.job["execution_claim"] == {"job_id": source["job_id"], "output_reference": source["output_reference"]}
    assert stores.job["terminal_receipt"]["result_digest"] == namespace["_terminal_digest"](payload)
    assert namespace["_OUTPUT_OWNER"].get() is None


@pytest.mark.parametrize("raw", [
    r"\![literal](figures/missing.png)",
    "`![literal](figures/missing.png)`",
    "``![literal](figures/missing.png) ` raw``",
    "```markdown\n![literal](figures/missing.png)\n```",
    '```html\n<img src="figures/missing.png" width="200px"><br>\n```',
    '    <img src="figures/missing.png" width="200px"><br>\n',
    '`<img src="figures/missing.png" width="200px"><br>`',
    '> ```\n> ![literal](figures/missing.png)\n> ```',
    '- item\n\n      ![literal](figures/missing.png)',
])
def test_actual_standard_parser_preserves_literal_assets_without_lookup(raw):
    uploaded = []
    page = SimpleNamespace(page_index=1, figures=[], markdown_text=raw)
    assert page_publisher(uploaded)("bucket", "assets/", page) == (raw, 0, {})
    assert uploaded == []


@pytest.mark.parametrize("real", [
    "![own](figures/own.png)",
    "> ![own](figures/own.png)",
    "- ![own](figures/own.png)",
    "> - ![own](figures/own.png)",
    '![own](\n  figures/own.png\n  "title"\n)',
    '<img\n src="figures/own.png"\n width="200px"><br>',
])
def test_actual_parser_real_asset_adjacent_to_identical_code_only_rewrites_own_span(real):
    uploaded = []
    literal = '`![own](figures/own.png)`\n\n```html\n<img src="figures/own.png" width="200px"><br>\n```'
    raw = literal + "\n\n" + real
    figure = SimpleNamespace(name="own.png", reference="figures/own.png", content=b"own", mime_type="image/png")
    page = SimpleNamespace(page_index=1, figures=[figure], markdown_text=raw)
    markdown, count, assets = page_publisher(uploaded)("bucket", "assets/", page)
    assert markdown == literal + "\n\n" + real.replace("figures/own.png", "gs://bucket/assets/p1/figures/own.png")
    assert count == 1 and len(uploaded) == 1
    assert assets == {"figures/own.png": "gs://bucket/assets/p1/figures/own.png"}


def test_actual_parser_reference_image_uses_standard_definition_position():
    uploaded = []
    raw = '![own][source]\n\n[source]: <figures/own.png> "title"\n\n`![literal][source]`'
    figure = SimpleNamespace(name="own.png", reference="figures/own.png", content=b"own", mime_type="image/png")
    page = SimpleNamespace(page_index=1, figures=[figure], markdown_text=raw)
    markdown, count, assets = page_publisher(uploaded)("bucket", "assets/", page)
    assert markdown == raw.replace("![own][source]", '![own](<gs://bucket/assets/p1/figures/own.png> "title")')
    assert count == 1 and len(uploaded) == 1


@pytest.mark.parametrize("raw", [
    "[source]: figures/own.png\nordinary paragraph\n",
    '[source]: figures/own.png "title"\nordinary paragraph\n',
    '[source]: figures/own.png\n  "title"\nordinary paragraph\n',
    "> [source]: figures/own.png\n> ordinary paragraph\n",
    "[source]: figures/missing.png\n\n`![literal][source]`\n",
])
def test_actual_unused_reference_lookahead_does_not_require_asset_or_source_mapping(raw):
    uploaded = []
    page = SimpleNamespace(page_index=1, figures=[], markdown_text=raw)
    assert page_publisher(uploaded)("bucket", "assets/", page) == (raw, 0, {})
    assert uploaded == []


@pytest.mark.parametrize("prefix", ["", "> ", "- "])
@pytest.mark.parametrize("definition", [
    "[source]: figures/own.png\nordinary paragraph",
    '[source]: figures/own.png "title"\nordinary paragraph',
    '[source]: figures/own.png\n  "title"\nordinary paragraph',
])
def test_actual_shared_reference_only_image_use_changes_with_title_lookahead_and_containers(prefix, definition):
    uploaded = []
    image = prefix + "![*raw alt*][source]"
    rest = '\n\n[download][source]\n\n' + definition + '\n\n`![*raw alt*][source]`\n\n```\n![*raw alt*][source]\n```'
    raw = image + rest
    figure = SimpleNamespace(name="own.png", reference="figures/own.png", content=b"own", mime_type="image/png")
    markdown, count, assets = page_publisher(uploaded)("bucket", "assets/", SimpleNamespace(page_index=1, figures=[figure], markdown_text=raw))
    title = ' "title"' if '"title"' in definition else ""
    expected = prefix + "![*raw alt*](<gs://bucket/assets/p1/figures/own.png>" + title + ")" + rest
    assert markdown == expected
    assert "[download][source]" in markdown and definition in markdown
    assert count == 1 and uploaded == [("assets/p1/figures/own.png", b"own", "image/png")]


@pytest.mark.parametrize("image_use", [
    "> ![first\n> second][source]",
    "> ![first\r\n> second][source]",
    "- ![first\n  second][source]",
    "- ![first\r\n  second][source]",
    "-\t![first\r\n\tsecond][source]",
    "> ![first\n> second][]",
    "> ![first\r\n> second]",
])
def test_actual_reference_multiline_raw_alt_container_crlf_and_tab_preservation(image_use):
    uploaded = []
    collapsed = image_use.endswith("[]")
    shortcut = not image_use.endswith("[source]") and not collapsed
    definition_label = "first second" if collapsed or shortcut else "source"
    rest = f'\r\n\r\n[{definition_label}]: figures/own.png "title"\r\nordinary raw paragraph\r\n\r\n[download][{definition_label}]\r\n\r\n`![first][{definition_label}]`'
    figure = SimpleNamespace(name="own.png", reference="figures/own.png", content=b"own", mime_type="image/png")
    page = SimpleNamespace(page_index=1, figures=[figure], markdown_text=image_use + rest)
    markdown, count, assets = page_publisher(uploaded)("bucket", "assets/", page)
    alt_end = image_use.rfind("]") if shortcut else image_use.rfind("][")
    preserved_label = image_use[:alt_end + 1]
    assert markdown == preserved_label + '(<gs://bucket/assets/p1/figures/own.png> "title")' + rest
    assert count == 1
    assert uploaded == [("assets/p1/figures/own.png", b"own", "image/png")]
