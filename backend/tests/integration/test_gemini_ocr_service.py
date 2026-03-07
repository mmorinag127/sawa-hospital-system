import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import gemini_ocr_service  # noqa: E402
from src.services.gemini_ocr_service import (  # noqa: E402
    _build_request_body,
    _build_response_schema,
    _extract_json_payload,
    run_gemini_ocr,
)


def test_extract_json_payload_parses_valid_payload():
    raw = (
        '{"facility_name":"グループホームそよかぜ","date_strings":["2/15"],'
        '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"じゃが芋のコンソメ煮","qty.regular_2f":"20"}]}'
    )
    payload = _extract_json_payload(raw)
    assert payload["facility_name"] == "グループホームそよかぜ"
    assert payload["date_strings"] == ["2/15"]
    assert isinstance(payload["rows"], list)
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["qty.regular_2f"] == "20"


def test_extract_json_payload_recovers_truncated_rows_payload():
    raw = (
        '{"facility_name":"グループホームそよかぜ","date_strings":["2/15","2/16"],'
        '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"A","qty.regular_2f":"20"},'
        '{"date_mmdd":"2/15","daypart":"昼","menu":"B","qty.regular_2f":"11"},'
        '{"date_mmdd'
    )
    payload = _extract_json_payload(raw)
    assert payload["facility_name"] == "グループホームそよかぜ"
    assert payload["date_strings"] == ["2/15", "2/16"]
    assert len(payload["rows"]) == 2
    assert payload["rows"][0]["menu"] == "A"
    assert payload["rows"][1]["menu"] == "B"
    assert payload["rows"][0]["qty.regular_2f"] == "20"


def test_extract_json_payload_raises_on_non_recoverable_json():
    with pytest.raises(Exception):
        _extract_json_payload('{"facility_name":"x","date_strings":[],"rows":[')


def test_run_gemini_ocr_retries_on_max_tokens(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    calls: list[dict] = []
    responses = [
        {
            "candidates": [
                {
                    "finishReason": "MAX_TOKENS",
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"facility_name":"x","date_strings":["2/15"],'
                                    '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"A","qty.regular_2f":"1"}]}'
                                )
                            }
                        ]
                    },
                }
            ]
        },
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"facility_name":"x","date_strings":["2/15"],'
                                    '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"A","qty.regular_2f":"20"}]}'
                                )
                            }
                        ]
                    },
                }
            ]
        },
    ]

    class _FakeResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def read(self) -> bytes:
            import json

            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ARG002
            return False

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        import json

        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        idx = min(len(calls) - 1, len(responses) - 1)
        return _FakeResponse(responses[idx])

    monkeypatch.setattr(gemini_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(gemini_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_gemini_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "gemini_ocr_max_tokens": 1000,
            "gemini_ocr_retry_max_tokens": 2000,
            "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        },
    )

    assert len(calls) == 2
    assert calls[0]["generationConfig"]["maxOutputTokens"] == 1000
    assert calls[1]["generationConfig"]["maxOutputTokens"] == 2000
    assert output["rows"][0]["qty.regular_2f"] == "20"
    assert output["_ocr_debug"]["attempt_count"] == 2
    assert output["_ocr_debug"]["retry_applied"] is True
    assert output["_ocr_debug"]["finish_reason"] == "STOP"


def test_build_request_body_places_image_before_prompt():
    body = _build_request_body(
        system_prompt="system rules",
        user_prompt="read this",
        image_b64="AAAA",
        schema={"type": "object"},
        max_tokens=1000,
    )
    parts = body["contents"][0]["parts"]
    assert "inline_data" in parts[0]
    assert parts[1]["text"] == "read this"


def test_build_response_schema_returns_custom_schema_override():
    custom_schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {"type": "object"},
            }
        },
        "required": ["rows"],
    }

    assert _build_response_schema({"gemini_ocr_response_schema": custom_schema}) == custom_schema


def test_run_gemini_ocr_normalizes_qty_date_daypart(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"facility_name":"  ","date_strings":["2026/2/15"],'
                                '"rows":[{"date_mmdd":"2026-2-15","daypart":"朝食","menu":"A",'
                                '"qty.regular_2f":"２０食","qty.regular_3f":"０"}]}'
                            )
                        }
                    ]
                },
            }
        ]
    }

    class _FakeResponse:
        def read(self) -> bytes:
            import json

            return json.dumps(response).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ARG002
            return False

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse()

    monkeypatch.setattr(gemini_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(gemini_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_gemini_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
                "qty.regular_3f",
            ],
        },
    )

    assert output["facility_name"] is None
    assert output["date_strings"] == ["2/15"]
    assert output["rows"][0]["date_mmdd"] == "2/15"
    assert output["rows"][0]["daypart"] == "朝"
    assert output["rows"][0]["qty.regular_2f"] == ""
    assert output["rows"][0]["qty.regular_3f"] == "0"


def test_run_gemini_ocr_quantity_only_mode_keeps_row_index(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    captured_bodies: list[dict] = []
    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"facility_name":"x","date_strings":["2/15"],"rows":['
                                '{"row_index":0,"qty.regular_2f":"２０"},'
                                '{"row_index":1,"qty.regular_2f":"0"},'
                                '{"row_index":2,"qty.regular_2f":"x"}]}'
                            )
                        }
                    ]
                },
            }
        ]
    }

    class _FakeResponse:
        def read(self) -> bytes:
            import json

            return json.dumps(response).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ARG002
            return False

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        import json

        captured_bodies.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse()

    monkeypatch.setattr(gemini_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(gemini_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_gemini_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "llm_quantity_only_mode": True,
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
            ],
        },
    )

    assert captured_bodies
    system_prompt_text = captured_bodies[0]["system_instruction"]["parts"][0]["text"]
    user_prompt_text = captured_bodies[0]["contents"][0]["parts"][1]["text"]
    assert "row_index" in system_prompt_text
    assert "parenthesis/bracket mark spans multiple quantity cells" in system_prompt_text
    assert "arrows/vertical range lines indicate a number applies to a span" in system_prompt_text
    assert user_prompt_text == "Read the attached fax image and return JSON following the system instruction."
    schema = captured_bodies[0]["generationConfig"]["responseSchema"]
    row_schema = schema["properties"]["rows"]["items"]
    assert "row_index" in row_schema["properties"]
    assert "row_index" in row_schema["required"]
    assert output["rows"] == [
        {"qty.regular_2f": "20", "row_index": "0"},
        {"qty.regular_2f": "0", "row_index": "1"},
        {"qty.regular_2f": "", "row_index": "2"},
    ]
    assert output["_ocr_debug"]["quantity_only_mode"] is True


def test_run_gemini_ocr_uses_template_user_prompt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    captured_bodies: list[dict] = []
    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"facility_name":"x","date_strings":["2/15"],'
                                '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"A","qty.regular_2f":"9"}]}'
                            )
                        }
                    ]
                },
            }
        ]
    }

    class _FakeResponse:
        def read(self) -> bytes:
            import json

            return json.dumps(response).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ARG002
            return False

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        import json

        captured_bodies.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse()

    monkeypatch.setattr(gemini_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(gemini_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_gemini_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "gemini_ocr_user_prompt": "Operator note: infer unreadable digits conservatively.",
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
            ],
        },
    )

    assert captured_bodies
    assert captured_bodies[0]["contents"][0]["parts"][1]["text"] == "Operator note: infer unreadable digits conservatively."
    assert (
        "Extract only the order table and return strict JSON."
        in captured_bodies[0]["system_instruction"]["parts"][0]["text"]
    )
    assert output["rows"][0]["qty.regular_2f"] == "9"
