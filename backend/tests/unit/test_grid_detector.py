import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import grid_detector  # noqa: E402


def test_cap_detection_dpi_reduces_requested_resolution_for_huge_pages():
    capped = grid_detector._cap_detection_dpi(
        300,
        page_width_points=2542.0,
        page_height_points=3506.0,
        max_pixels=24_000_000,
    )

    assert capped < 300
    assert capped >= 96


def test_downscale_image_if_needed_limits_pixel_count():
    image = Image.new("RGB", (10592, 14609), color="white")

    resized = grid_detector._downscale_image_if_needed(image, max_pixels=24_000_000)

    assert resized.size[0] * resized.size[1] <= 24_000_000
    assert resized.size[0] < image.size[0]
    assert resized.size[1] < image.size[1]
