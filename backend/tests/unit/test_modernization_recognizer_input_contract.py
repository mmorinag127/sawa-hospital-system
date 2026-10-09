"""Synthetic pixel component proofs, not native OCR or user-FAX evidence."""

from copy import deepcopy

import cv2
import numpy as np
import pytest

from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime


def _target(width=1360, height=63, glyph=None):
    image = np.full((height + 40, width + 40, 3), 255, dtype=np.uint8)
    box = [20, 20, 20 + width, 20 + height]
    if glyph is not None:
        (text_width, text_height), _ = cv2.getTextSize(
            glyph, cv2.FONT_HERSHEY_SIMPLEX, 1.3, 1,
        )
        cv2.putText(
            image, glyph,
            (20 + (width - text_width) // 2, 20 + (height + text_height) // 2),
            cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 1, cv2.LINE_8,
        )
    region = {
        "region_id": "E11", "target_cell_id": "quantity-row-0",
        "sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5,
        "field": "qty.regular_x", "date": "2026-03-01", "daypart": "朝食",
        "bbox": box,
        "polygon": [[20, 20], [20 + width, 20],
                    [20 + width, 20 + height], [20, 20 + height]],
    }
    return image, region


def _contact(image, regions, mode="corner_cc"):
    return runtime.build_recognizer_contact_sheet(
        rectified_fax_bgr=image, regions=regions, line_mask=None, mode=mode,
        min_ink_area=10**9, min_ink_height=10**9,
    )


def _ink(image):
    pixels = np.asarray(image)
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY) if pixels.ndim == 3 else pixels
    mask = (gray < 128).astype(np.uint8)
    points = cv2.findNonZero(mask)
    assert points is not None, "nonblank authored glyph was erased before recognition"
    _, _, width, height = cv2.boundingRect(points)
    return width, height, int(mask.sum())


@pytest.mark.parametrize("glyph", ["7", "0", "6", "9"])
@pytest.mark.parametrize("size", [(1360, 63), (90, 300)])
def test_source_resolution_warp_and_contact_preserve_glyph_pixels(glyph, size):
    image, region = _target(*size, glyph=glyph)
    original_image = image.copy()
    original_region = deepcopy(region)
    width, height = size
    warped, cell_box, pixel_box, source_box = runtime._warp_polygon_cell_to_axis_aligned_crop(
        image, region["polygon"],
    )
    assert warped.shape == (height, width, 3)
    assert cell_box == [0.0, 0.0, float(width), float(height)]
    assert pixel_box == (0, 0, width, height)
    assert source_box == tuple(region["bbox"])
    source = image[20:20 + height, 20:20 + width]
    sw, sh, source_coverage = _ink(source)
    ww, wh, warp_coverage = _ink(warped)
    assert abs(ww - sw) <= 2
    assert abs(wh - sh) <= 2
    assert 0.7 <= warp_coverage / source_coverage <= 1.3

    sheet, delivered, polygons, skipped = _contact(image, [region])
    assert skipped == []
    assert len(delivered) == len(polygons) == 1
    output = delivered[0]
    for key in ("region_id", "target_cell_id", "sheet_cell", "worksheet_row",
                "worksheet_col", "field", "date", "daypart", "bbox", "polygon"):
        assert output[key] == region[key]
    assert output["recognizer_candidate"] is True
    assert output["ocr_cell_crop_bbox_px"] == region["bbox"]
    x0, y0, x1, y1 = output["ocr_contact_crop_box"]
    assert polygons[0] == [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    iw, ih, coverage = _ink(sheet.crop((x0, y0, x1, y1)))
    assert coverage > 0
    assert abs(iw / ih - sw / sh) <= 0.2
    assert abs(coverage / (iw * ih) - source_coverage / (sw * sh)) <= 0.2
    assert output["recognizer_ink_stats"]["ink_area"] > 0
    assert region == original_region
    assert np.array_equal(image, original_image)


@pytest.mark.parametrize("mode", ["raw", "clean", "dynamic", "corner_cc"])
def test_blank_thin_and_small_ink_all_keep_recognizer_slots(mode):
    image, region = _target(width=100, height=63)
    blank = deepcopy(region)
    blank["region_id"] = blank["sheet_cell"] = "E12"
    blank["target_cell_id"] = "quantity-row-1"
    blank["worksheet_row"] = 12
    blank_sheet, blank_regions, blank_polygons, blank_skipped = _contact(image, [blank], mode)
    assert blank_skipped == []
    assert len(blank_regions) == len(blank_polygons) == 1
    assert blank_regions[0]["recognizer_candidate"] is True
    assert np.asarray(blank_sheet).min() == 255
    if mode == "corner_cc":
        assert blank_regions[0]["recognizer_ink_stats"]["digit_like_component_count"] == 0

    for points in ([[65, 36], [78, 36], [68, 66]], [[70, 50], [71, 50]]):
        ink_image = image.copy()
        cv2.polylines(ink_image, [np.array(points, dtype=np.int32)], False, (0, 0, 0), 1)
        _, targets, polygons, skipped = _contact(ink_image, [region, blank], mode)
        assert skipped == []
        assert len(targets) == len(polygons) == 2
        assert [item["target_cell_id"] for item in targets] == ["quantity-row-0", "quantity-row-1"]
        assert [item["ocr_contact_slot_index"] for item in targets] == [0, 1]
        assert [item["ocr_contact_compact_slot_index"] for item in targets] == [0, 1]
        assert all(item["recognizer_candidate"] is True for item in targets)
        assert all("recognizer_raw_text" not in item for item in targets)
        assert all("recognizer_score" not in item for item in targets)


@pytest.mark.parametrize("geometry", ["bbox", "none", "display-only", "polygon"])
def test_presentation_polygon_does_not_replace_canonical_crop(geometry):
    image, region = _target(glyph="7")
    if geometry != "polygon":
        region.pop("polygon")
    if geometry == "none":
        region["polygon"] = None
    if geometry == "display-only":
        region["display_polygon"] = "presentation-only-not-an-OCR-polygon"
    _, targets, polygons, skipped = _contact(image, [region])
    assert len(targets) == len(polygons) == 1
    assert skipped == []
    assert targets[0]["sheet_cell"] == "E11"
    assert targets[0]["recognizer_candidate"] is True
    assert targets[0]["recognizer_ink_stats"]["ink_area"] > 0
    if geometry == "display-only":
        assert targets[0]["display_polygon"] == region["display_polygon"]


@pytest.mark.parametrize("box", [
    [-1, 20, 100, 83], [20, 20, 1401, 83], [20, 20, 100, 104],
    [20, 20, 20, 83], [100, 20, 20, 83], [20, 83, 100, 20],
    [20, float("nan"), 100, 83], [20, 20, float("inf"), 83],
])
def test_invalid_bbox_stops_instead_of_omitting_target(box):
    image, valid = _target()
    invalid = deepcopy(valid)
    invalid["bbox"] = box
    with pytest.raises(ValueError, match="^recognizer_crop_bbox_invalid: target_index=1$"):
        _contact(image, [valid, invalid])


@pytest.mark.parametrize("polygon", [
    [], "invalid", [[20, 20]] * 3,
    [[20, 20], [100, 20], [100, 20], [20, 20]],
    [[20, 20], [100, 83], [100, 20], [20, 83]],
    [[-1, 20], [100, 20], [100, 83], [-1, 83]],
    [[20, 20], [1401, 20], [1401, 83], [20, 83]],
    [[20, 20], [100, 20], [100, float("nan")], [20, 83]],
    [[20, 20], [100, 20], [100, float("inf")], [20, 83]],
    [[20, 83], [100, 83], [100, 20], [20, 20]],
])
def test_explicit_invalid_or_reversed_polygon_never_uses_valid_bbox(polygon):
    image, region = _target()
    region["polygon"] = polygon
    with pytest.raises(ValueError, match="recognizer_crop_polygon_invalid"):
        _contact(image, [region])
