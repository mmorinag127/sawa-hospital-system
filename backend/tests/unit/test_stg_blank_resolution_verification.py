from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

from PIL import Image
import pytest

from test_stg_recognition_review_verification import case, mod


spec = importlib.util.spec_from_file_location('blank_verify', Path(__file__).resolve().parents[3] / 'scripts/verify_hakodate_blank_resolution.py')
blank_verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blank_verify)


def fixture():
    expected = {'blank_cells': ['F11'], 'numeric_cells': {'E31': '26'},
                'review_cells': {'G29': '-'}, 'note_cells': {}, 'blank_raw_text': '1'}
    pixels = Image.new('RGB', (10, 10), 'white')
    rows = []
    for cell, raw in [('F11', ''), ('E31', '26'), ('G29', '-')]:
        source = {'sheet_cell': cell, 'recognizer_raw_text': '1' if cell == 'F11' else raw}
        if cell == 'F11':
            source.update(blank_decision={'state': 'blank', 'resolved_value': ''},
                          recognizer_input_evidence={'all_white': True, 'bbox': [0, 0, 10, 10],
                              'width': 10, 'height': 10, 'rgb_sha256': hashlib.sha256(pixels.tobytes()).hexdigest()})
            source['ocr_contact_crop_box'] = [0, 0, 10, 10]
            source['blank_decision'].update(input_id=cell, contact=deepcopy(source['recognizer_input_evidence']))
        rows.append({'raw_text': raw, 'normalized_value': raw,
                     'engine_metadata': {'sheet_cell': cell, 'recognizer_output': source}})
    review = {'cells': [{'sheet_cell': 'G29', 'raw_text': '-', 'reason': 'hakodate_ocr_quantity_non_numeric'}], 'count': 1}
    return {'hakodate_ocr_evidence_records': rows, 'hakodate_recognition_review': review}, expected, pixels


def test_all_cells_verified_with_raw_blank_prediction_preserved_and_real_26_retained():
    payload, expected, pixels = fixture()
    originals = [deepcopy(row['engine_metadata']['recognizer_output']) for row in payload['hakodate_ocr_evidence_records']]
    assert blank_verify.verify_blank_resolution(payload, expected, pixels, originals) == payload['hakodate_recognition_review']['cells']


@pytest.mark.parametrize('mutation', ['blank_one', 'lost_26', 'missing_cell', 'duplicate_cell', 'hidden_review', 'raw_changed', 'painted_white', 'other_cell_bbox', 'artifact_changed'])
def test_corruption_cannot_pass_blank_fix_verification(mutation):
    payload, expected, pixels = fixture()
    rows = payload['hakodate_ocr_evidence_records']
    originals = [deepcopy(row['engine_metadata']['recognizer_output']) for row in rows]
    if mutation == 'blank_one':
        rows[0]['normalized_value'] = '1'
    elif mutation == 'lost_26':
        rows[1]['raw_text'] = ''
    elif mutation == 'missing_cell':
        rows.pop()
    elif mutation == 'duplicate_cell':
        rows.append(deepcopy(rows[0]))
    elif mutation == 'hidden_review':
        payload['hakodate_recognition_review'] = {'cells': [], 'count': 0}
    elif mutation == 'raw_changed':
        rows[0]['engine_metadata']['recognizer_output']['recognizer_raw_text'] = ''
    elif mutation == 'painted_white':
        pixels.putpixel((5, 5), (0, 0, 0))
    elif mutation == 'other_cell_bbox':
        rows[0]['engine_metadata']['recognizer_output']['recognizer_input_evidence']['bbox'] = [0, 0, 5, 5]
    else:
        originals[0]['recognizer_raw_text'] = 'another-result'
    with pytest.raises(AssertionError):
        blank_verify.verify_blank_resolution(payload, expected, pixels, originals)


def test_explicit_fix_case_keeps_existing_job_attempt_document_and_nonselection_guards():
    evidence, workflow, started, order, document, previous = case()
    payload, expected, _pixels = fixture()
    evidence['payload_json'].update(payload)
    assert mod.review_required_evidence(evidence, workflow, started, order, document, previous, expected)
    evidence['payload_json']['_ocr_execution']['workflow_attempt_id'] = 'another-attempt'
    assert not mod.review_required_evidence(evidence, workflow, started, order, document, previous, expected)
