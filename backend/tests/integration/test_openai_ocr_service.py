import sys
import pathlib
import socket

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import openai_ocr_service  # noqa: E402
from src.services.openai_ocr_service import _extract_json_payload, run_openai_ocr  # noqa: E402


def test_extract_json_payload_recovers_truncated_rows_payload():
    raw = (
        '{"facility_name":"テスト施設","date_strings":["2/15","2/16"],'
        '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"A","qty.regular_2f":"20"},'
        '{"date_mmdd":"2/15","daypart":"昼","menu":"B","qty.regular_2f":"11"},'
        '{"date_mmdd'
    )
    payload = _extract_json_payload(raw)
    assert payload["facility_name"] == "テスト施設"
    assert payload["date_strings"] == ["2/15", "2/16"]
    assert len(payload["rows"]) == 2
    assert payload["rows"][0]["qty.regular_2f"] == "20"
    assert payload["rows"][1]["menu"] == "B"


def test_run_openai_ocr_retries_on_length_and_normalizes(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    calls: list[dict] = []
    responses = [
        {
            "id": "resp_1",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": (
                            '{"facility_name":"x","date_strings":["2/15"],'
                            '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"A","qty.regular_2f":"1"}]}'
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        },
        {
            "id": "resp_2",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            '{"facility_name":"  ","date_strings":["2026/2/15"],'
                            '"rows":[{"date_mmdd":"2026-2-15","daypart":"朝食","menu":"A",'
                            '"qty.regular_2f":"２０","qty.regular_3f":"１２a"}]}'
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 140, "total_tokens": 260},
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

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_openai_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "openai_ocr_max_tokens": 1000,
            "openai_ocr_retry_max_tokens": 2000,
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
                "qty.regular_3f",
            ],
        },
    )

    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 1000
    assert calls[1]["max_tokens"] == 2000
    assert calls[0]["messages"][1]["content"][1]["image_url"]["detail"] == "high"

    assert output["facility_name"] is None
    assert output["date_strings"] == ["2/15"]
    assert output["rows"][0]["date_mmdd"] == "2/15"
    assert output["rows"][0]["daypart"] == "朝"
    assert output["rows"][0]["qty.regular_2f"] == "20"
    assert output["rows"][0]["qty.regular_3f"] == ""
    assert output["_ocr_debug"]["attempt_count"] == 2
    assert output["_ocr_debug"]["retry_applied"] is True
    assert output["_ocr_debug"]["finish_reason"] == "stop"


def test_run_openai_ocr_reports_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        raise socket.timeout("timed out")

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="OpenAI OCR timeout after 90s"):
        run_openai_ocr(
            pdf_bytes=b"%PDF-1.4\n%EOF\n",
            template={"main_ocr_row_fields": ["date_mmdd", "daypart", "menu"]},
        )


def test_run_openai_ocr_full_table_mode_uses_extended_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    observed: dict[str, float] = {}

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        observed["timeout"] = timeout
        raise socket.timeout("timed out")

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="OpenAI OCR timeout after 240s"):
        run_openai_ocr(
            pdf_bytes=b"%PDF-1.4\n%EOF\n",
            template={
                "main_ocr_row_fields": ["date_mmdd", "daypart", "menu"],
                "llm_full_table_mode": True,
            },
        )

    assert observed["timeout"] == 240.0


def test_run_openai_ocr_quantity_only_mode_keeps_row_index(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    captured_bodies: list[dict] = []
    response = {
        "id": "resp_q_only",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": (
                        '{"facility_name":"x","date_strings":["2/15"],"rows":['
                        '{"row_index":0,"qty.regular_2f":"２０"},'
                        '{"row_index":1,"qty.regular_2f":"0"},'
                        '{"row_index":2,"qty.regular_2f":"x"}]}'
                    )
                },
            }
        ],
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

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_openai_ocr(
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
    system_prompt_text = captured_bodies[0]["messages"][0]["content"]
    user_prompt_text = captured_bodies[0]["messages"][1]["content"][0]["text"]
    assert "row_index" in system_prompt_text
    assert "parenthesis/bracket mark spans multiple quantity cells" in system_prompt_text
    assert "arrows/vertical range lines indicate a number applies to a span" in system_prompt_text
    assert "Do not output date/daypart/menu fields in rows." in system_prompt_text
    assert user_prompt_text == "Read the attached fax image and return JSON following the system instruction."
    assert output["rows"] == [
        {"qty.regular_2f": "20", "row_index": "0"},
        {"qty.regular_2f": "0", "row_index": "1"},
        {"qty.regular_2f": "", "row_index": "2"},
    ]
    assert output["_ocr_debug"]["quantity_only_mode"] is True


def test_run_openai_ocr_full_table_mode_preserves_sparse_patch_rows(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    response = {
        "id": "resp_full_table",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": (
                        '{"facility_name":"x","date_strings":["04/26"],"rows":['
                        '{"row_index":0,"date_mmdd":"04/26","daypart":"朝","menu":"Menu A","qty.regular_2f":"２０"},'
                        '{"row_index":1,"date_mmdd":"","daypart":"","menu":"","qty.regular_2f":""},'
                        '{"date_mmdd":"04/27","daypart":"昼","menu":"SHOULD_DROP","qty.regular_2f":"99"}]}'
                    )
                },
            }
        ],
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

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_openai_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "llm_full_table_mode": True,
            "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        },
    )

    assert output["rows"] == [
        {"row_index": "0", "qty.regular_2f": "20"},
        {"row_index": "1", "qty.regular_2f": ""},
    ]
    assert output["_ocr_debug"]["quantity_only_mode"] is False
    assert output["_ocr_debug"]["full_table_mode"] is True
    assert output["_ocr_debug"]["returned_row_indexes"] == [0, 1]


def test_run_openai_ocr_full_table_mode_requires_row_index_in_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    captured_bodies: list[dict] = []
    response = {
        "id": "resp_full_table_schema",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": '{"facility_name":"x","date_strings":[],"rows":[]}'},
            }
        ],
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

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    run_openai_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "llm_full_table_mode": True,
            "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        },
    )

    system_prompt_text = captured_bodies[0]["messages"][0]["content"]
    assert "Every returned row object must include row_index." in system_prompt_text
    assert "Return only rows that need a patch; omit unchanged rows." in system_prompt_text
    assert "Do not output structural anchor fields such as date_mmdd, daypart, or menu" in system_prompt_text


def test_run_openai_ocr_full_table_mode_uses_columns_authoritative_aux_fields(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    captured_bodies: list[dict] = []
    response = {
        "id": "resp_full_table_aux",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": (
                        '{"facility_name":"x","date_strings":["04/26"],"rows":['
                        '{"row_index":0,"date_mmdd":"04/26","daypart":"朝","aux.col_2":"主","menu":"大豆のトマト煮","aux.col_4":"70","qty.regular_x":"20"},'
                        '{"row_index":1,"date_mmdd":"04/26","daypart":"朝","aux.col_2":"副","menu":"胡瓜のフレンチサラダ","aux.col_4":"","qty.regular_x":"50"}]}'
                    )
                },
            }
        ],
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

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_openai_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "llm_full_table_mode": True,
            "columns_authoritative": True,
            "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
            "columns": [
                {"index": 0, "role": "date", "header": "日付"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "aux", "header": "副区分"},
                {"index": 3, "role": "menu_name", "header": "メニュー"},
                {"index": 4, "role": "aux", "header": "合計"},
                {"index": 5, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
            ],
        },
    )

    assert output["rows"] == [
        {
            "row_index": "0",
            "aux.col_2": "主",
            "aux.col_4": "70",
            "qty.regular_x": "20",
        },
        {
            "row_index": "1",
            "aux.col_2": "副",
            "aux.col_4": "",
            "qty.regular_x": "50",
        },
    ]
    system_prompt_text = captured_bodies[0]["messages"][0]["content"]
    assert "aux.col_2" in system_prompt_text
    assert "aux.col_4" in system_prompt_text
    assert "display-only helper/total column" in system_prompt_text


def test_run_openai_ocr_uses_template_user_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    def _fake_render_pdf_to_png_bytes(*, pdf_bytes, dpi, page):  # noqa: ARG001
        return b"\x89PNG\r\n\x1a\n"

    captured_bodies: list[dict] = []
    response = {
        "id": "resp_user_prompt",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": (
                        '{"facility_name":"x","date_strings":["2/15"],'
                        '"rows":[{"date_mmdd":"2/15","daypart":"朝","menu":"A","qty.regular_2f":"3"}]}'
                    )
                },
            }
        ],
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

    monkeypatch.setattr(openai_ocr_service, "render_pdf_to_png_bytes", _fake_render_pdf_to_png_bytes)
    monkeypatch.setattr(openai_ocr_service.urllib.request, "urlopen", _fake_urlopen)

    output = run_openai_ocr(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        template={
            "openai_ocr_user_prompt": "Operator note: keep zeros as explicit 0 when visible.",
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
            ],
        },
    )

    assert captured_bodies
    assert (
        captured_bodies[0]["messages"][1]["content"][0]["text"]
        == "Operator note: keep zeros as explicit 0 when visible."
    )
    assert "Extract only the order table and return strict JSON." in captured_bodies[0]["messages"][0]["content"]
    assert output["rows"][0]["qty.regular_2f"] == "3"
