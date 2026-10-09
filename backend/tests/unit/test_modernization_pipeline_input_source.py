"""Input-source component definitions; no native OCR or ownership proof.

Only environment wiring is varied. The actual resolver and config function run
unchanged. This does not exercise run_ocr_pipeline's authored-config/PDF gates,
queue delivery, recognition, images, state storage or a live service.
"""

import pytest

from src.services import ocr_pipeline_service as pipeline


@pytest.fixture(autouse=True)
def isolated_input_environment(monkeypatch):
    for name in (
        "OCR_PIPELINE_BUCKET", "OCR_PIPELINE_INPUT_BUCKET", "OCR_OUTPUT_BUCKET",
        "OCR_ARTIFACT_BUCKET", "RAW_BUCKET", "OCR_PIPELINE_URL",
        "OCR_PIPELINE_INPUT_PREFIX", "OCR_PIPELINE_OUTPUT_PREFIX", "PYTEST_CURRENT_TEST",
    ):
        monkeypatch.delenv(name, raising=False)


def assert_config(*, bucket, error, url):
    assert pipeline.get_pipeline_config() == {
        "configured": bucket is not None,
        "error": error,
        "url_set": bool(url),
        "bucket_set": bucket is not None,
        "bucket": bucket,
        "input_prefix": "input/",
        "output_prefix": "output/",
        "trigger_mode": "gcs_http" if bucket and url else "http_only" if url else "gcs_only",
        "http_trigger_enabled": bool(url),
        "gcs_trigger_enabled": bucket is not None,
        "wait_strategy": "poll_output_gcs",
        "sync_wait_supported": bucket is not None,
        "sync_wait_note": (
            "HTTP trigger disabled; worker relies on storage-triggered OCR execution and polls for output."
            if bucket and not url else None
        ),
    }


@pytest.mark.parametrize("canonical,legacy", [
    ("owned-input", None),
    (" \towned-input\n", None),
    ("owned-input", "owned-input"),
    (" owned-input ", "\towned-input "),
])
@pytest.mark.parametrize("url", [None, " https://pipeline.invalid/trigger "])
def test_explicit_canonical_input_and_equal_alias(monkeypatch, canonical, legacy, url):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", canonical)
    if legacy is not None:
        monkeypatch.setenv("OCR_PIPELINE_INPUT_BUCKET", legacy)
    if url is not None:
        monkeypatch.setenv("OCR_PIPELINE_URL", url)
    for name in ("OCR_OUTPUT_BUCKET", "OCR_ARTIFACT_BUCKET", "RAW_BUCKET"):
        monkeypatch.setenv(name, "unrelated-output-not-an-input-source")
    assert pipeline._get_pipeline_bucket() == "owned-input"
    assert_config(bucket="owned-input", error=None, url=url)


@pytest.mark.parametrize("environment,expected_error", [
    ({}, "OCR_PIPELINE_BUCKET is not configured"),
    ({"OCR_PIPELINE_BUCKET": ""}, "OCR_PIPELINE_BUCKET is not configured"),
    ({"OCR_PIPELINE_BUCKET": " \t\n"}, "OCR_PIPELINE_BUCKET is not configured"),
    ({"OCR_PIPELINE_INPUT_BUCKET": "legacy-input"}, "OCR_PIPELINE_BUCKET is not configured"),
    ({"OCR_OUTPUT_BUCKET": "output-only"}, "OCR_PIPELINE_BUCKET is not configured"),
    ({"OCR_ARTIFACT_BUCKET": "artifact-only"}, "OCR_PIPELINE_BUCKET is not configured"),
    ({"RAW_BUCKET": "raw-only"}, "OCR_PIPELINE_BUCKET is not configured"),
    ({"OCR_PIPELINE_BUCKET": " \t", "OCR_PIPELINE_INPUT_BUCKET": "legacy-input",
      "OCR_OUTPUT_BUCKET": "output", "OCR_ARTIFACT_BUCKET": "artifact", "RAW_BUCKET": "raw"},
     "OCR_PIPELINE_BUCKET is not configured"),
    ({"OCR_PIPELINE_BUCKET": " owned-input ", "OCR_PIPELINE_INPUT_BUCKET": " other-input "},
     "OCR_PIPELINE_INPUT_BUCKET conflicts with OCR_PIPELINE_BUCKET"),
])
@pytest.mark.parametrize("pytest_marker", [False, True])
@pytest.mark.parametrize("url", [None, "https://pipeline.invalid/trigger"])
def test_missing_or_conflicting_input_stops_in_both_environments(
    monkeypatch, environment, expected_error, pytest_marker, url,
):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if pytest_marker:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic-component (call)")
    if url is not None:
        monkeypatch.setenv("OCR_PIPELINE_URL", url)
    with pytest.raises(RuntimeError) as raised:
        pipeline._get_pipeline_bucket()
    assert str(raised.value) == expected_error
    assert_config(bucket=None, error=expected_error, url=url)


def test_existing_explicit_prefix_fields_are_preserved(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "owned-input")
    monkeypatch.setenv("OCR_PIPELINE_INPUT_PREFIX", "owned/input")
    monkeypatch.setenv("OCR_PIPELINE_OUTPUT_PREFIX", "owned/output/")
    config = pipeline.get_pipeline_config()
    assert config["configured"] is True
    assert config["bucket"] == pipeline._get_pipeline_bucket() == "owned-input"
    assert config["error"] is None
    assert config["input_prefix"] == "owned/input/"
    assert config["output_prefix"] == "owned/output/"
