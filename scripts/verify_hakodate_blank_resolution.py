"""Verify every cell against explicit expectations from the inspected FAX."""

import hashlib


def verify_blank_resolution(payload, expected, contact=None, recognizer_records=None):
    blanks = expected["blank_cells"]
    numbers = expected["numeric_cells"]
    reviews = expected["review_cells"]
    notes = expected["note_cells"]
    assert isinstance(blanks, list) and blanks and len(set(blanks)) == len(blanks)
    assert all(isinstance(group, dict) for group in (numbers, reviews, notes))
    assert numbers and all(isinstance(value, str) and value.isascii() and value.isdigit() for value in numbers.values())
    groups = [set(blanks), set(numbers), set(reviews), set(notes)]
    assert sum(map(len, groups)) == len(set().union(*groups))
    records = payload["hakodate_ocr_evidence_records"]
    by_cell = {record["engine_metadata"]["sheet_cell"]: record for record in records}
    assert len(by_cell) == len(records) == sum(map(len, groups))
    assert set(by_cell) == set().union(*groups)
    if contact is not None:
        assert isinstance(recognizer_records, list)
        originals = {source['sheet_cell']: source for source in recognizer_records}
        assert len(originals) == len(recognizer_records) == len(records)
        assert set(originals) == set(by_cell)
    for cell, record in by_cell.items():
        source = record["engine_metadata"]["recognizer_output"]
        if contact is not None:
            assert source == originals[cell]
        if cell in groups[0]:
            assert record["raw_text"] == record["normalized_value"] == ""
            assert source["blank_decision"]["state"] == "blank"
            assert source["blank_decision"]["resolved_value"] == ""
            assert source["recognizer_raw_text"] == expected["blank_raw_text"]
            pixels = source["recognizer_input_evidence"]
            assert pixels["all_white"] is True
            assert pixels['bbox'] == source['ocr_contact_crop_box']
            assert source['blank_decision']['input_id'] == cell
            assert source['blank_decision']['contact'] == pixels
            if contact is not None:
                crop = contact.crop(tuple(pixels["bbox"])).convert("RGB")
                assert crop.size == (pixels["width"], pixels["height"])
                assert crop.getextrema() == ((255, 255),) * 3
                assert hashlib.sha256(crop.tobytes()).hexdigest() == pixels["rgb_sha256"]
        else:
            values = numbers if cell in numbers else reviews if cell in reviews else notes
            assert record["raw_text"] == record["normalized_value"] == values[cell]
            assert source["recognizer_raw_text"] == values[cell]
            assert "blank_decision" not in source
    expected_review = [{"sheet_cell": cell, "raw_text": record["raw_text"],
                        "reason": "hakodate_ocr_quantity_non_numeric"}
                       for cell, record in by_cell.items() if cell in reviews]
    assert payload["hakodate_recognition_review"] == {"cells": expected_review, "count": len(expected_review)}
    return expected_review
