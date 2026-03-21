import pathlib
import sys
import types

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import pdf_render  # noqa: E402


def test_cap_render_dpi_reduces_requested_resolution_for_huge_pages():
    capped = pdf_render._cap_render_dpi(
        220,
        page_width_points=2542.0,
        page_height_points=3506.0,
        max_pixels=18_000_000,
    )

    assert capped < 220
    assert capped >= 96


def test_render_pdf_to_png_bytes_caps_resolution_for_huge_pages(monkeypatch):
    calls: list[int] = []

    class FakePageImage:
        def __init__(self):
            self.original = Image.new("RGB", (640, 480), color="white")

    class FakePage:
        width = 2542.0
        height = 3506.0

        def to_image(self, resolution: int):
            calls.append(resolution)
            return FakePageImage()

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_pdfplumber = types.SimpleNamespace(open=lambda _buffer: FakePdf())
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)

    png = pdf_render.render_pdf_to_png_bytes(
        b"%PDF-1.4 test",
        dpi=220,
        page=1,
        max_pixels=18_000_000,
    )

    assert png
    assert calls
    assert calls[0] < 220
    assert calls[0] >= 96
