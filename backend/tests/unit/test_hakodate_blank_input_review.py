from copy import deepcopy
import json
import os
from pathlib import Path

from PIL import Image
import pytest

from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime
from src.services import hakodate_ocr_evidence_service as evidence


@pytest.mark.parametrize('text', ['1', '0', '6', '9', '26', '-'])
@pytest.mark.parametrize('white', [True, False])
def test_all_cells_recognized_raw_unchanged_and_white_requires_review(monkeypatch, text, white):
    image = Image.new('RGB', (20, 20), 'white')
    if not white:
        image.putpixel((5, 5), (0, 0, 0))
    polygon = [[0, 0], [20, 0], [20, 20], [0, 20]]
    calls = []
    class Recognizer:
        def __init__(self, *args, **kwargs):
            pass
        def recognize(self, image_bgr, *, points):
            calls.append(deepcopy(points))
            return {'contents': [text], 'scores': [0.0822547972202301],
                    'directions': ['horizontal'], 'candidates': [[{'text': '1', 'score': 0.08}]] if text == '-' else [[]],
                    'points': points}
    monkeypatch.setattr(runtime, 'YomitokuTextRecognizerTopKWrapper', Recognizer)
    rows, metrics = runtime.run_text_recognizer_direct(recognizer=object(), contact_sheet=image,
        regions=[{'sheet_cell': 'E31', 'field': 'qty.regular', 'ink_area': 0}], polygons=[polygon],
        score_threshold=0.45, digit_score_threshold=0.05, candidate_digit_score_threshold=0.05,
        enable_context_repair=False)
    assert calls == [[polygon]] and metrics['raw_prediction_count'] == 1
    row = rows[0]
    assert row['recognizer_raw_text'] == text
    assert row['recognizer_score'] == 0.0822547972202301
    assert row['recognizer_input_evidence']['all_white'] is white
    record = {'raw_text': text, 'engine_metadata': {'recognizer_output': row}}
    if white or text == '-':
        with pytest.raises(ValueError, match='unresolved|conflict'):
            evidence.validate_ocr_quantity_source(record)
    else:
        assert evidence.validate_ocr_quantity_source(record) == text
        assert 'quantity_source_issue' not in row


def test_missing_legacy_metadata_is_not_blank_evidence():
    assert evidence.validate_ocr_quantity_source({'raw_text': '26'}) == '26'
    assert evidence.validate_ocr_quantity_source({'raw_text': '1'}) == '1'


def test_issue_retained_through_evidence_and_unresolved_assignment():
    record = {'text': '1', 'confidence': .08, 'bbox': [0, 0, 10, 10],
              'engine_metadata': {'recognizer_output': {'raw_text': '1',
                  'quantity_source_issue': 'hakodate_blank_input_nonempty_prediction'}}}
    records = evidence.evidence_from_records([record], run_id='unit', engine='unit', source_scope='unit')
    targets = evidence.target_cells_from_regions([{'sheet_cell': 'E11', 'worksheet_row': 11,
        'worksheet_col': 5, 'field': 'qty.regular', 'role': 'quantity', 'bbox': [0, 0, 10, 10]}])
    assigned = evidence.assign_evidence_to_target_cells(evidence_records=records, target_cells=targets)
    assert records[0]['raw_text'] == '1'
    assert assigned['assignments'][0]['assignment_state'] == 'unresolved'
    assert any('hakodate_ocr_quantity_unresolved' in value for value in assigned['blockers'])


def test_operator_label_alone_does_not_clear_issue():
    with pytest.raises(ValueError, match='unresolved'):
        evidence.validate_ocr_quantity_source({'raw_text': '1', 'engine_metadata': {
            'source': 'operator', 'recognizer_output': {'raw_text': '1',
                'quantity_source_issue': 'hakodate_blank_input_nonempty_prediction'}}})
    assert evidence.validate_ocr_quantity_source({'raw_text': '26', 'engine_metadata': {
        'source': 'operator', 'edited_by': 'explicit-operator', 'operator_output': {'operator_value': '26'}}}) == '26'


@pytest.mark.skipif(not os.environ.get('HAKODATE_CONTACT_ARTIFACT_DIR'), reason='optional real contact artifacts')
def test_real_contact_exact_white_count():
    root = Path(os.environ['HAKODATE_CONTACT_ARTIFACT_DIR'])
    regions = json.loads((root / 'recognizer-regions.json').read_text())
    with Image.open(root / 'recognizer-contact.png') as image:
        whites = []
        retained = {}
        for region in regions:
            x0, y0, x1, y1 = region['ocr_contact_crop_box']
            observed = runtime.contact_crop_evidence(image, [[x0,y0],[x1,y0],[x1,y1],[x0,y1]])
            if observed['all_white']:
                whites.append(region)
            source = {**region, 'recognizer_input_evidence': observed}
            if not evidence.recognition_input_issue(source):
                retained[region['sheet_cell']] = region.get('recognizer_raw_text')
        assert len(regions) == 392
        assert len(whites) == 325
        assert all(region['recognizer_raw_text'] == '1' for region in whites)
        assert sum(region.get('role') == 'note' for region in whites) == 56
        assert sum(value in {'2', '24', '25', '26'} for value in retained.values()) == 57
        assert retained['E31'] == retained['E63'] == '26'


@pytest.mark.parametrize('role,field', [('quantity', 'qty.regular'), ('note', 'note')])
def test_removed_issue_still_blocks_both_roles_and_lists_raw(role, field):
    image = Image.new('RGB', (10, 10), 'white')
    observed = runtime.contact_crop_evidence(image, [[0, 0], [10, 0], [10, 10], [0, 10]])
    source = {'raw_text': '1', 'role': role, 'field': field, 'recognizer_input_evidence': observed}
    record = {'raw_text': '1', 'engine_metadata': {'sheet_cell': 'E11', 'recognizer_output': source}}
    with pytest.raises(ValueError, match='unresolved'):
        evidence.validate_ocr_source_for_role(record, role)
    assert evidence.recognition_review([record]) == {'cells': [{'sheet_cell': 'E11', 'raw_text': '1',
        'reason': 'hakodate_blank_input_nonempty_prediction'}], 'count': 1}


@pytest.mark.parametrize('key,value', [('contract', 'unknown'), ('all_white', 'true'),
    ('rgb_sha256', 'bad'), ('width', 0), ('height', True), ('bbox', [0, 0, 9, 10])])
@pytest.mark.parametrize('role', ['quantity', 'note'])
def test_invalid_pixel_evidence_never_success(key, value, role):
    observed = runtime.contact_crop_evidence(Image.new('RGB', (10, 10), 'white'),
        [[0, 0], [10, 0], [10, 10], [0, 10]])
    observed[key] = value
    with pytest.raises(ValueError, match='evidence_invalid'):
        evidence.validate_ocr_source_for_role({'raw_text': '1', 'recognizer_input_evidence': observed}, role)


def test_explicit_operator_values_use_existing_operator_runtime_without_recognition(monkeypatch):
    from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as renderer
    values = {'E31': '26', 'E63': '26', 'F11': '1', 'G11': '0', 'H11': '6', 'I11': '9'}
    regions = [{'sheet_cell': cell, 'ocr_contact_slot_index': index} for index, cell in enumerate(values)]
    monkeypatch.setattr(renderer, 'build_recognizer_contact_sheet',
        lambda **kwargs: (Image.new('RGB', (10, 10), 'white'), regions, [], []))
    records, _, _ = renderer._operator_records(raw_rectified_bgr=None, regions=regions,
        truth={}, operator_values=values)
    assert {row['sheet_cell']: row['operator_value'] for row in records} == values
    for row in records:
        assert row['recognition_source'] == 'operator'
        assert row['pred_digits'] == values[row['sheet_cell']]
        assert 'recognizer_input_evidence' not in row
    assert evidence.recognition_review([{'raw_text': row['operator_value'], 'engine_metadata': {
        'source': 'operator', 'edited_by': 'explicit-operator', 'sheet_cell': row['sheet_cell'],
        'operator_output': row}} for row in records]) == {'cells': [], 'count': 0}


def test_checkpoint_stage_patch_preserves_receipt_without_changing_ownership():
    from src.services.ocr_job_service import merge_own_job_metrics_patch
    checkpoint = {'uri': 'local-checkpoint.json', 'sha256': 'a' * 64}
    current = {'workflow_attempt_id': 'owned', 'evidence_run_id': 'owned-result'}
    result = merge_own_job_metrics_patch(current, {'recognition_checkpoint': checkpoint,
        'workflow_attempt_id': 'foreign', 'evidence_run_id': 'foreign-result'})
    assert result == {**current, 'recognition_checkpoint': checkpoint}
    assert merge_own_job_metrics_patch(result, {'processing_stage': 'recognition'})['recognition_checkpoint'] == checkpoint
