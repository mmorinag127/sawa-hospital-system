"""Actual source definitions for URI transport; definitions only, not live proof."""
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import quote, urlencode, urlparse, urlsplit
from html import escape
import re
import os
import json
import hashlib
from importlib.util import module_from_spec, spec_from_file_location

import pytest

from test_h2_grid_source_contract import ROOT, MAIN, URI_BINDINGS, definitions, owned_pipeline, _uri_codec, _figure_markup

STORAGE = ROOT / "backend/src/services/storage_service.py"
ORDER = ROOT / "backend/src/services/order_service.py"


@pytest.fixture(autouse=True)
def conditional_storage_exceptions():
    spec = spec_from_file_location("uri_conditional_transport", ROOT / "ocr_pipeline/tests/terminal_store_fakes.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.install_transport_exceptions():
        yield module


def test_actual_original_source_then_owned_output_binding_matches_terminal_wrapper():
    ns, stores, source, protocol = owned_pipeline()
    with protocol.install_transport_exceptions():
        original = ns["_original_pipeline_source"]("own-bucket", stores.name, "19")
        assert "output_reference" not in original
        assert ns["_owned_output_name"]("output/", original) == stores.output
        assert ns["_terminal_source"]("own-bucket", stores.name, "19", stores.output) == source
    assert _uri_codec.parse_gcs_uri(source["output_reference"]).object_name == stores.output
    assert {key: value for key, value in source.items() if key != "output_reference"} == original
    assert stores.job["execution_claim"] == {"job_id": source["job_id"], "output_reference": source["output_reference"]}


class Transport:
    """Only external GCS/signing transport is substituted, not URI logic."""
    def __init__(self):
        self.calls = []
        self.bytes = {}
        self.generation = "41"

    def bucket(self, bucket):
        transport = self
        class Bucket:
            def blob(self, name, generation=None):
                transport.calls.append(("blob", bucket, name, generation))
                class Blob:
                    @property
                    def generation(self): return transport.generation
                    def upload_from_string(self, data, content_type=None):
                        transport.calls.append(("upload", bucket, name, data, content_type))
                        transport.bytes[bucket, name, int(transport.generation)] = data
                    def reload(self, **kwargs): transport.calls.append(("reload", bucket, name, kwargs))
                    def download_as_bytes(self, **kwargs):
                        transport.calls.append(("download", bucket, name, generation, kwargs))
                        assert kwargs.get("if_generation_match") == generation
                        selected = int(transport.generation) if generation is None else generation
                        return transport.bytes[bucket, name, selected]
                    def generate_signed_url(self, **kwargs):
                        transport.calls.append(("sign", bucket, name, kwargs))
                        query = dict(kwargs["query_parameters"] or {})
                        if kwargs["generation"] is not None: query["generation"] = kwargs["generation"]
                        query["X-Goog-Signature"] = "transport-fixture"
                        return "https://storage.invalid/" + bucket + "/" + quote(name, safe="/") + "?" + urlencode(query)
                return Blob()
        return Bucket()


def storage_functions(transport):
    return definitions(STORAGE, ["load_bytes_from_uri", "save_bytes_to_gcs", "generate_signed_url", "generate_signed_url_from_uri", "verify_selected_pdf_source", "_verify_selected_pdf_source", "sign_published_figure_references"], {
        "urlparse": urlparse, "re": re, "os": os, "_ENV_REF_PATTERN": re.compile(r"\$\{([A-Z0-9_]+)\}"),
        "timedelta": timedelta, "_get_thread_gcs_client": lambda: transport,
        "image_references": _figure_markup._extract_markdown_images,
        "replace_image_references": _figure_markup._replace_markdown_images,
        "_import_google_cloud_storage": lambda: None,
        "_get_thread_gcs_signing_credentials": lambda: (SimpleNamespace(token="fixture-token"), "fixture@example.invalid")})


@pytest.mark.parametrize("key", ["assets/page 1/figure.png", "assets/100%literal.png", "assets/%20.png",
    "assets/区域/図.png", "assets/q?x=1#part.png", "/assets/double-leading.png", "assets/./literal.png"])
def test_actual_producer_publisher_reader_signer_keep_exact_key_and_historical_generation(key):
    transport = Transport()
    storage = storage_functions(transport)
    pipeline, stores, source, protocol = owned_pipeline()
    key = stores.output.rsplit("/", 1)[0] + "/assets/" + key
    with protocol.install_transport_exceptions():
        uploaded = pipeline["_upload_bytes"]("own-bucket", key, b"original 0/null", "image/png")
        assert pipeline["_upload_bytes"]("own-bucket", key, b"original 0/null", "image/png") == uploaded
    generation = _uri_codec.parse_gcs_uri(uploaded).generation
    assert stores.objects[key] == b"original 0/null"
    assert stores.object_metadata[key] == {"original_source": json.dumps(source, ensure_ascii=False, sort_keys=True),
                                          "sha256": hashlib.sha256(b"original 0/null").hexdigest()}
    assert len([write for write in stores.writes if write[0] == key]) == 1
    transport.bytes["own-bucket", key, int(generation)] = stores.objects[key]
    with protocol.install_transport_exceptions():
        stores.bucket("own-bucket").blob(key).upload_from_string(b"newer external generation")
        assert stores.bucket("own-bucket").blob(key, generation=int(generation)).download_as_bytes(
            if_generation_match=int(generation)) == b"original 0/null"
    published = _uri_codec.decorate_gcs_uri(uploaded, "figures/own.png?generation_hint=42&note=%25%3F#page=2")
    parsed = _uri_codec.parse_gcs_uri(published)
    assert (parsed.object_name, parsed.generation, parsed.fragment) == (key, generation, "page=2")
    assert parsed.query == f"generation={generation}&generation_hint=42&note=%25%3F"
    transport.generation = "99"
    transport.bytes["own-bucket", key, 99] = b"newer must not be selected"
    assert storage["load_bytes_from_uri"](published, verification=True) == b"original 0/null"
    signed = storage["generate_signed_url_from_uri"](published)
    sign = [call for call in transport.calls if call[0] == "sign"][-1]
    assert sign[2] == key and sign[3]["generation"] == generation
    assert sign[3]["query_parameters"] == {"generation_hint": "42", "note": "%?"}
    assert signed.endswith("#page=2")
    assert urlsplit(signed).path == "/own-bucket/" + quote(key, safe="/")
    with pytest.raises(ValueError, match="mismatch"):
        storage["load_bytes_from_uri"](published, generation="99")


@pytest.mark.parametrize("failure", ["missing_owner", "claim_before", "claim_after", "namespace", "bytes", "metadata"])
def test_actual_immutable_uploader_rejects_original_owner_and_storage_conflicts(failure):
    from unittest.mock import patch
    ns, stores, source, protocol = owned_pipeline()
    key = stores.output.rsplit("/", 1)[0] + "/assets/own.png"
    upload = ns["_upload_bytes"]
    with protocol.install_transport_exceptions():
        if failure in {"bytes", "metadata"}:
            upload("own-bucket", key, b"own 0/null", "image/png")
            if failure == "bytes":
                stores.objects[key] = b"foreign"
                stores.generations[key, 2] = b"foreign"
            else:
                stores.object_metadata[key]["original_source"] = json.dumps({"job_id": "foreign"})
        if failure == "missing_owner":
            ns["_OUTPUT_OWNER"].set(None)
        if failure == "claim_before":
            stores.job["execution_claim"]["job_id"] = "foreign"
        if failure == "namespace":
            key = "foreign/assets/own.png"
        original = protocol.Blob.upload_from_string
        def conditional_write(blob, data, **kwargs):
            assert kwargs["if_generation_match"] == 0
            original(blob, data, **kwargs)
            if failure == "claim_after":
                stores.job["execution_claim"]["job_id"] = "foreign"
        before = dict(stores.objects)
        with patch.object(protocol.Blob, "upload_from_string", conditional_write):
            with pytest.raises(ns["TerminalReceiptConflict"]):
                upload("own-bucket", key, b"own 0/null", "image/png")
        if failure == "claim_after":
            # Already sent own bytes remain; claim retirement denies a receipt,
            # not a fictitious cancellation or overwrite of another owner.
            assert stores.objects[key] == b"own 0/null"
            assert len(stores.writes) == 1
        else:
            assert stores.objects == before


@pytest.mark.parametrize("query", ["generation=7", "%67eneration=7", "generation=", "a=1&generation=41", "X-Goog-Signature=foreign"])
def test_actual_publisher_rejects_reserved_local_query_before_upload(query):
    ns, stores, _, _ = owned_pipeline(names=["_upload_figure_artifact", "_publish_page_figures", "_extract_markdown_images", "_replace_markdown_images", "_image_reference_spans"])
    figure = SimpleNamespace(name="own.png", reference="figures/own.png", content=b"own", mime_type="image/png")
    page = SimpleNamespace(page_index=2, figures=[figure], markdown_text=f'<img src="figures/own.png?{query}" width="200px"><br>')
    with pytest.raises(ValueError, match="conflicts"):
        ns["_publish_page_figures"]("own-bucket", stores.output.rsplit("/", 1)[0] + "/assets/", page)
    assert stores.writes == []


def test_actual_markup_publication_keeps_delimiter_external_refs_and_raw_query_fragment():
    transport = Transport()
    ns, stores, _, protocol = owned_pipeline(names=["_upload_figure_artifact", "_publish_page_figures", "_extract_markdown_images", "_replace_markdown_images", "_image_reference_spans"])
    key = "figures/100%区域 ?.png"
    ref = quote(key, safe="/") + "?generation_hint=5&note=%25%3F#section"
    external = '<img src="https://example.invalid/100.png?generation=2">\n![data](data:image/png;base64,AAAA)'
    raw = f'<img src="{escape(ref, quote=True)}" width="200px"><br>\n![own](<{ref}> "raw title")\n' + external
    figure = SimpleNamespace(name="100%区域 ?.png", reference=key, content=b"exact own bytes", mime_type="image/png")
    page = SimpleNamespace(page_index=2, figures=[figure], markdown_text=raw)
    prefix = stores.output.rsplit("/", 1)[0] + "/assets/"
    with protocol.install_transport_exceptions():
        rendered, count, assets = ns["_publish_page_figures"]("own-bucket", prefix, page)
    expected = _uri_codec.format_gcs_uri("own-bucket", prefix + "p2/" + figure.reference, "2") + "&generation_hint=5&note=%25%3F#section"
    assert count == 1 and assets == {ref: expected}
    assert rendered == f'<img src="{escape(expected, quote=True)}" width="200px"><br>\n![own](<{expected}> "raw title")\n' + external
    assert rendered.endswith(external) and "%3Fgeneration%3D" not in rendered
    storage = storage_functions(transport)
    signed = storage["generate_signed_url_from_uri"](expected)
    literal = "\n`" + expected + "`\n[ordinary](<" + expected + ">)"
    preview = storage["sign_published_figure_references"](rendered + literal, {expected: signed})
    assert preview == f'<img src="{escape(signed, quote=True)}" width="200px"><br>\n![own](<{signed}> "raw title")\n' + external + literal
    with pytest.raises(ValueError, match="signed_receipt_missing"):
        storage["sign_published_figure_references"](rendered, {})


@pytest.mark.parametrize("uri", ["gs://bucket/own.png?generation=1&generation=2", "gs://bucket/own.png?generation=",
    "gs://bucket/own.png?generation=0", "gs://bucket/own.png?%67eneration=1&generation=1", "gs://bucket/name%literal.png",
    "gs://bucket/name%FF.png", "gs://bucket/name with space.png", "gs://bucket/", "gs://bucket/path%2Fname.png"])
def test_ambiguous_uri_never_dispatches_reader_or_signer(uri):
    transport = Transport()
    ns = storage_functions(transport)
    for operation in (ns["load_bytes_from_uri"], ns["generate_signed_url_from_uri"]):
        with pytest.raises(ValueError): operation(uri)
    assert transport.calls == []


def test_signing_failure_is_stop_and_non_gcs_originals_are_unchanged():
    def fail(uri): raise RuntimeError("actual signer failure")
    ns = definitions(ORDER, ["_signed_url_from_uri"], {"urlparse": urlparse,
        "generate_signed_url_from_uri": fail, "logger": SimpleNamespace(warning=lambda *a, **kw: None)})
    with pytest.raises(ValueError, match="storage_signed_url_failed"):
        ns["_signed_url_from_uri"]("gs://bucket/own.png?generation=41")
    for uri, expected in ((None, None), ("", None), ("https://external.invalid/raw?x=1#y", "https://external.invalid/raw?x=1#y"), ("/original/local.pdf", "/original/local.pdf")):
        assert ns["_signed_url_from_uri"](uri) == expected


def test_original_unpinned_upload_and_literal_local_file_contract(tmp_path):
    transport = Transport()
    ns = storage_functions(transport)
    key = "manual-uploads/%literal 区域?.pdf"
    uri = ns["save_bytes_to_gcs"]("bucket", key, b"original PDF", client=transport)
    assert _uri_codec.parse_gcs_uri(uri).object_name == key
    assert _uri_codec.parse_gcs_uri(uri).generation is None
    assert ns["load_bytes_from_uri"](uri) == b"original PDF"
    path = tmp_path / "%literal ?#区域.pdf"
    path.write_bytes(b"local source")
    assert ns["load_bytes_from_uri"](str(path)) == b"local source"


def test_actual_h2_selected_pdf_readers_use_exact_key_without_weakening_current_source_fence():
    transport = Transport()
    ns = storage_functions(transport)
    data = b"original PDF bytes"
    key = "manual-uploads/%literal 区域?#.pdf"
    uri = ns["save_bytes_to_gcs"]("bucket", key, data, client=transport, pin_generation=True)
    receipt = {"storage_uri": uri, "generation": "41", "sha256": hashlib.sha256(data).hexdigest()}
    pipeline = definitions(MAIN, ["_verify_selected_pdf_input"], {"gcs": transport, "json": json, "re": re})
    blob = SimpleNamespace(metadata={"selected_pdf_receipt": json.dumps(receipt)})
    ns["verify_selected_pdf_source"](receipt)
    pipeline["_verify_selected_pdf_input"](blob, data)
    assert all(call[2] == key for call in transport.calls if call[0] in {"blob", "reload"})
    with pytest.raises(ValueError, match="bytes_changed"):
        pipeline["_verify_selected_pdf_input"](blob, b"wrong PDF")
    transport.generation = "99"
    for verify in (lambda: ns["verify_selected_pdf_source"](receipt), lambda: pipeline["_verify_selected_pdf_input"](blob, data)):
        with pytest.raises(ValueError, match="generation_changed"): verify()
    # Pinned historical asset reads are deliberately distinct from the H2
    # current-selected-input fence; neither reads a substituted latest blob.
    assert ns["load_bytes_from_uri"](uri) == data


def test_backend_adapter_loads_the_actual_shared_codec_not_a_second_grammar():
    path = ROOT / "backend/src/services/storage_uri.py"
    spec = spec_from_file_location("actual_backend_uri_adapter", path)
    adapter = module_from_spec(spec)
    spec.loader.exec_module(adapter)
    key = "%literal 区域?#.pdf"
    uri = adapter.format_gcs_uri("bucket", key, "41")
    assert uri == _uri_codec.format_gcs_uri("bucket", key, "41")
    assert adapter.parse_gcs_uri(uri).object_name == key


def test_actual_pipeline_input_uri_parser_and_state_identity_do_not_use_encoded_keys():
    service = ROOT / "backend/src/services/ocr_pipeline_service.py"
    parse = definitions(service, ["_parse_gs_uri"], {"urlparse": urlparse})["_parse_gs_uri"]
    key = "input/%literal 区域?#.pdf"
    uri = _uri_codec.format_gcs_uri("bucket", key, "41")
    assert parse(uri) == ("bucket", key)
    assert parse("/explicit/local.pdf") is None
    with pytest.raises(ValueError): parse("gs://bucket/name%literal.pdf")
    state = ROOT / "backend/src/services/ocr_pipeline_state_store.py"
    calls = []
    ns = definitions(state, ["load_pipeline_state", "_save_state"], {"urlparse": urlparse,
        "_state_uri": lambda: _uri_codec.format_gcs_uri("bucket", key),
        "_load_gs": lambda bucket, name: calls.append(("read", bucket, name)) or {"raw": 0},
        "_save_gs": lambda bucket, name, body: calls.append(("write", bucket, name, body)),
        "logger": SimpleNamespace(info=lambda *a, **kw: None), "OCRPipelineStateConfigError": RuntimeError})
    assert ns["load_pipeline_state"]() == {"raw": 0}
    ns["_save_state"]({"raw": None})
    assert calls == [("read", "bucket", key), ("write", "bucket", key, {"raw": None})]


def test_generation_and_signing_query_ambiguity_remain_explicit_stops():
    for value in (None, "", 0, "0", "01", "1x", True, -1):
        with pytest.raises(ValueError): _uri_codec.validate_generation(value)
    transport = Transport()
    sign = storage_functions(transport)["generate_signed_url_from_uri"]
    for query in ("generation=41&a=1&a=2", "generation=41&X-Goog-Date=foreign"):
        with pytest.raises(ValueError): sign("gs://bucket/own.png?" + query)
    assert transport.calls == []
