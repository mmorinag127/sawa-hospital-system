"""Producer and evidence contracts with authored model responses; not native OCR proof."""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
import pytest

from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime
from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as renderer
from src.services import hakodate_ocr_evidence_service as evidence
from test_hakodate_review_resolution_root import recognize, record, region


def run(monkeypatch, raw, score=.08, *, white=False, role='quantity'):
    image = Image.new('RGB', (80, 60), 'white')
    if not white:
        ImageDraw.Draw(image).text((25, 20), raw or 'x', fill='black')
    candidates = [[{'text':'1', 'score':.99}, {'text':'26', 'score':.8}]]
    rows, metrics = recognize(monkeypatch, image, [region(role)],
        [[[0,0],[80,0],[80,60],[0,60]]], [raw], scores=[score], candidates=candidates)
    source = rows[0]
    assert source['recognizer_raw_text'] == raw
    assert source['recognizer_score'] == score
    assert source['recognizer_candidates'] == candidates[0]
    assert source['recognizer_accepted_candidate'] is None
    assert source['recognizer_candidate_accepted'] is False
    assert metrics['candidate_digit_accept_count'] == 0
    assert metrics['raw_prediction_count'] == metrics['candidate_count'] == 1
    assert metrics['digit_score_threshold'] == metrics['candidate_digit_score_threshold'] == .05
    return source


@pytest.mark.parametrize('raw', ['0','1','26'])
def test_raw_digits_not_replaced_by_stronger_diagnostic_candidate(monkeypatch, raw):
    source = run(monkeypatch, raw)
    assert source['resolution_state'] == 'resolved'
    assert source['resolved_value'] == source['ocr_normalized'] == raw
    assert evidence.validate_ocr_quantity_source(record(source)) == raw
    assert source['recognizer_decision_source'] == 'raw_digits'


@pytest.mark.parametrize('raw,score,reason', [
    ('26', .049, 'hakodate_ocr_quantity_low_confidence'),
    ('', .01, 'hakodate_ocr_quantity_low_confidence'),
    ('-', .8, 'hakodate_ocr_quantity_non_numeric'),
    ('I', .8, 'hakodate_ocr_quantity_non_numeric'),
])
def test_quantity_raw_remains_editable_without_topk_substitution(monkeypatch, raw, score, reason):
    source = run(monkeypatch, raw, score)
    assert source['resolution_state'] == 'resolved'
    assert source['resolved_value'] == raw
    assert evidence.recognition_review([record(source)]) == {'count':0, 'cells':[]}
    assert evidence.validate_ocr_quantity_source(record(source)) == raw


def test_exact_existing_threshold_inclusive(monkeypatch):
    source = run(monkeypatch, '26', .05)
    assert source['resolved_value'] == '26'
    assert evidence.validate_ocr_quantity_source(record(source)) == '26'


def test_empty_raw_never_promoted_by_numeric_candidates(monkeypatch):
    source = run(monkeypatch, '', .8, white=True)
    assert source['resolved_value'] == source['ocr_text'] == ''
    assert source['recognizer_accepted'] is False
    assert evidence.validate_ocr_quantity_source(record(source)) == ''


@pytest.mark.parametrize('change', [
    {'recognizer_candidate_accepted':True},
    {'recognizer_decision_source':'topk_digits'},
    {'recognizer_accepted_candidate':{'text':'1','normalized_digits':'1'}},
    {'ocr_normalized':'1'},
])
def test_raw_wins_over_derived_candidate_diagnostics(monkeypatch, change):
    source = run(monkeypatch, '26')
    source.update(deepcopy(change))
    assert evidence.recognition_resolution(source) == {'resolution_state':'resolved', 'resolved_value':'26'}
    assert evidence.validate_ocr_quantity_source(record(source)) == '26'


def test_image_diagnostics_do_not_replace_quantity_raw(monkeypatch):
    white = run(monkeypatch, '1', white=True)
    assert white['resolved_value'] == '1'
    source = run(monkeypatch, '26')
    source['recognizer_preprocess_ink_removed'] = True
    assert evidence.recognition_resolution(source)['resolved_value'] == '26'


def test_note_remains_literal_not_numeric_candidate(monkeypatch):
    source = run(monkeypatch, '--', .01, role='note')
    assert source['resolved_value'] == source['ocr_normalized'] == '--'
    assert evidence.validate_ocr_source_for_role(record(source), 'note') == '--'


def test_renderer_region_serialization_uses_real_engine_and_keeps_operator():
    # Execute the actual serialization block without native preprocessing or file writes.
    tree = ast.parse(Path(renderer.__file__).read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
        and node.name == 'build_best_method_for_manifest_item')
    start = next(index for index,node in enumerate(function.body) if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name) and node.target.id == 'ocr_regions')
    block = ast.Module(body=function.body[start:start+2], type_ignores=[])
    records = [{'raw_text':'26','pred_digits':'26'}, {'raw_text':'','pred_digits':'0'}]
    context = {'records_json':records, 'records':[{}, {'recognition_source':'operator','operator_value':'0'}],
        'TEXT_RECOGNIZER_ENGINE':renderer.TEXT_RECOGNIZER_ENGINE, 'Any':Any}
    exec(compile(block, renderer.__file__, 'exec'), context)
    assert context['ocr_regions'][0]['ocr_engine'] == renderer.TEXT_RECOGNIZER_ENGINE
    assert context['ocr_regions'][1]['ocr_engine'] == 'operator'
    assert context['ocr_regions'][1]['operator_value'] == '0'


@pytest.mark.parametrize('candidate', [None, {}, {'text':'1','normalized_digits':'1'}])
def test_candidate_flags_do_not_replace_quantity_raw(monkeypatch, candidate):
    source = run(monkeypatch, '26')
    source.update(recognizer_candidate_accepted=True, recognizer_accepted_candidate=candidate)
    assert evidence.recognition_resolution(source)['resolved_value'] == '26'
    assert evidence.validate_ocr_quantity_source(record(source)) == '26'


@pytest.mark.skipif(not os.environ.get('HAKODATE_BLANK_ROOT_ARTIFACT_DIR'),
                   reason='explicit saved raw artifacts required')
def test_saved_actual_numeric_records_retain_same_candidate_diagnostic_flag():
    root = Path(os.environ['HAKODATE_BLANK_ROOT_ARTIFACT_DIR'])
    originals = json.loads((root/'recognizer-records.json').read_text())
    expected = json.loads(Path(os.environ['HAKODATE_BLANK_ROOT_MANIFEST_PATH']).read_text())['cases'][0]['blank_resolution']
    assert len(originals) == 392
    assert len(expected['numeric_cells']) == 57
    assert expected['numeric_cells']['E31'] == expected['numeric_cells']['E63'] == '26'
    by_cell = {row['sheet_cell']:row for row in originals}
    unchanged = deepcopy(originals)
    with Image.open(root/'recognizer-contact.png') as contact:
        for cell, raw in expected['numeric_cells'].items():
            source = deepcopy(by_cell[cell])
            assert source['recognizer_raw_text'] == source['pred_digits'] == raw
            assert source['recognizer_decision_source'] == 'raw_digits'
            assert source['recognizer_candidate_accepted'] is True
            assert source['recognizer_accepted_candidate']['normalized_digits'] == raw
            x0,y0,x1,y1 = source['ocr_contact_crop_box']
            source['recognizer_input_evidence'] = runtime.contact_crop_evidence(
                contact.convert('RGB'), [[x0,y0],[x1,y0],[x1,y1],[x0,y1]])
            resolution = evidence.recognition_resolution(source)
            assert resolution == {'resolution_state':'resolved', 'resolved_value':raw}
            source.update(resolution)
            rec = {'raw_text':source['recognizer_raw_text'], 'normalized_value':resolution['resolved_value'],
                   'engine_metadata':{'sheet_cell':cell, 'recognizer_output':source}}
            assert evidence.validate_ocr_quantity_source(rec) == raw
    assert originals == unchanged
