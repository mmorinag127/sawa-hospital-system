"""Resolved values never masquerade as raw model diagnostics."""
from copy import deepcopy
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime
from src.services import hakodate_recognizer_contact_sheet as contact_sheet
from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as renderer
from src.services import hakodate_ocr_evidence_service as evidence
from test_h2_operator_repair_revision import connected_physical_pipeline, _capture_reparse_job


def region(role='quantity'):
    return {'sheet_cell': 'E11', 'target_cell_id': 'E11', 'worksheet_row': 11,
            'worksheet_col': 5, 'role': role, 'field': 'qty.regular' if role == 'quantity' else 'note',
            'bbox': [0., 0., 80., 60.]}


def recognize(monkeypatch, contact, regions, polygons, raw, scores=None, candidates=None):
    calls = []
    regions = [{**r, 'ocr_contact_crop_box':[p[0][0],p[0][1],p[2][0],p[2][1]]} for r,p in zip(regions,polygons)]
    class Wrapper:
        def __init__(self, *args, **kwargs): pass
        def recognize(self, pixels, *, points):
            calls.append(deepcopy(points))
            return {'contents': raw, 'scores': scores if scores is not None else [.08] * len(raw),
                    'candidates': candidates if candidates is not None else [[{'text': '1', 'score': .08}]] * len(raw),
                    'directions': ['horizontal'] * len(raw), 'points': points}
    monkeypatch.setattr(runtime, 'YomitokuTextRecognizerTopKWrapper', Wrapper)
    rows, metrics = runtime.run_text_recognizer_direct(recognizer=object(), contact_sheet=contact,
        regions=regions, polygons=polygons, score_threshold=.45, digit_score_threshold=.05,
        candidate_digit_score_threshold=.05, enable_context_repair=False)
    assert calls == [polygons] and metrics['raw_prediction_count'] == len(regions)
    return rows, metrics


def record(source):
    source = {**source, 'raw_text': source['recognizer_raw_text'], 'pred_digits': source['ocr_normalized']}
    return {'text': source['recognizer_raw_text'], 'normalized_value': source['resolved_value'],
            'confidence': source['recognizer_score'], 'bbox': source['bbox'],
            'engine_metadata': {'sheet_cell': source['sheet_cell'], 'recognizer_output': source}}


@pytest.mark.parametrize('role', ['quantity', 'note'])
@pytest.mark.parametrize('raw', ['1', '0', '26', '-', '--'])
def test_white_conflict_has_no_resolved_value_and_raw_is_reviewable(monkeypatch, role, raw):
    rows, _ = recognize(monkeypatch, Image.new('RGB', (80,60), 'white'), [region(role)],
                         [[[0,0],[80,0],[80,60],[0,60]]], [raw])
    source = rows[0]
    assert source['recognizer_raw_text'] == raw and source['recognizer_score'] == .08
    assert source['recognizer_candidates'] == [{'text': '1', 'score': .08}]
    assert source['resolution_state'] == 'review_required'
    assert source['resolved_value'] is source['ocr_text'] is source['ocr_normalized'] is None
    assert not source['recognizer_accepted'] and not source['recognizer_candidate_accepted']
    records = evidence.evidence_from_records([record(source)], run_id='own', engine='fixture', source_scope='fixture')
    assert records[0]['raw_text'] == raw and records[0]['normalized_value'] is None
    assert evidence.recognition_review(records)['cells'][0]['raw_text'] == raw
    with pytest.raises(ValueError, match='unresolved'):
        evidence.validate_ocr_source_for_role(records[0], role)
    assigned = evidence.assign_evidence_to_target_cells(evidence_records=records,
        target_cells=evidence.target_cells_from_regions([region(role)]))
    assignment = assigned['assignments'][0]
    assert assignment['assigned_value'] is None and assignment['raw_texts'] == [raw]
    assert assignment['resolution_state'] == 'review_required'
    sheet = evidence.sheet_output_from_assigned_results(assignments=assigned['assignments'], blockers=assigned['blockers'])
    assert sheet['cells']['E11']['value_normalized'] is None
    assert sheet['cells']['E11']['raw_texts'] == [raw]
    assert sheet['rows'][0]['values_by_column']['E'] is None


@pytest.mark.parametrize('raw', ['1', '0', '26'])
def test_real_font_numbers_remain_numbers(monkeypatch, raw):
    image = Image.new('RGB', (80,60), 'white')
    ImageDraw.Draw(image).text((25,10), raw, font=ImageFont.load_default(size=30), fill='black')
    contact, regions, polygons, skipped = runtime.build_recognizer_contact_sheet(
        rectified_fax_bgr=cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), regions=[region()], line_mask=None, mode='corner_cc')
    assert not skipped
    rows, _ = recognize(monkeypatch, contact, regions, polygons, [raw], candidates=[[{'text':raw,'score':.08}]])
    assert rows[0]['resolution_state'] == 'resolved' and rows[0]['resolved_value'] == raw
    assert evidence.validate_ocr_quantity_source(record(rows[0])) == raw


@pytest.mark.parametrize('role,raw', [('quantity','-'), ('quantity','--'), ('note','-'), ('note','--')])
def test_nonnumeric_topk_is_review_for_quantity_but_literal_note(monkeypatch, role, raw):
    image = Image.new('RGB', (80,60), 'white'); ImageDraw.Draw(image).line((25,30,50,30), fill='black', width=3)
    rows, _ = recognize(monkeypatch, image, [region(role)], [[[0,0],[80,0],[80,60],[0,60]]], [raw])
    assert rows[0]['recognizer_raw_text'] == raw
    if role == 'quantity':
        assert rows[0]['resolved_value'] is None and rows[0]['ocr_normalized'] is None
        assert rows[0]['quantity_source_issue'] == 'hakodate_ocr_quantity_non_numeric'
    else:
        assert rows[0]['resolved_value'] == raw and rows[0]['ocr_normalized'] == raw
        assert evidence.validate_ocr_source_for_role(record(rows[0]), 'note') == raw


@pytest.mark.parametrize('x', range(5))
@pytest.mark.parametrize('size', [8,10,14])
def test_thin_digit_erased_at_frame_cannot_become_blank(monkeypatch, x, size):
    image = Image.new('RGB', (80,60), 'white')
    ImageDraw.Draw(image).text((x,15), '1', font=ImageFont.load_default(size=size), anchor='lt', fill='black')
    assert image.getextrema() != ((255,255),(255,255),(255,255))
    contact, regions, polygons, _ = runtime.build_recognizer_contact_sheet(
        rectified_fax_bgr=cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), regions=[region()], line_mask=None, mode='corner_cc')
    rows, _ = recognize(monkeypatch, contact, regions, polygons, ['1'])
    if x == 0:
        assert rows[0]['recognizer_input_evidence']['all_white']
    if rows[0]['recognizer_input_evidence']['all_white']:
        assert rows[0]['resolution_state'] == 'review_required' and rows[0]['resolved_value'] is None
        assert rows[0]['recognizer_preprocess_ink_removed']


@pytest.mark.parametrize('raw', ['', '1'])
def test_warp_digit_loss_stops_even_when_model_returns_empty(monkeypatch, raw):
    image = Image.new('RGB', (80,60), 'white')
    ImageDraw.Draw(image).text((0,10), '1', font=ImageFont.load_default(size=10), anchor='lt', fill='black')
    authored = {**region(), 'polygon': [[0.,0.],[80.,0.],[80.,60.],[0.,60.]]}
    monkeypatch.setattr(contact_sheet, '_warp_polygon_cell_to_axis_aligned_crop', lambda *args: (
        np.full((60,80,3),255,dtype=np.uint8), [0.,0.,80.,60.], (0,0,80,60), (0,0,80,60)))
    contact, regions, polygons, _ = runtime.build_recognizer_contact_sheet(
        rectified_fax_bgr=cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), regions=[authored], line_mask=None, mode='corner_cc')
    rows, _ = recognize(monkeypatch, contact, regions, polygons, [raw])
    assert rows[0]['recognizer_preprocess_ink_removed']
    assert rows[0]['resolution_state'] == 'review_required' and rows[0]['resolved_value'] is None


def test_overlay_uses_review_marker_not_raw_one(monkeypatch):
    labels = []
    monkeypatch.setattr(renderer, '_draw_text', lambda draw, point, text, **kwargs: labels.append(text))
    monkeypatch.setattr(renderer, '_draw_quad_points', lambda image, points, **kwargs: image)
    monkeypatch.setattr(renderer, '_make_review_canvas', lambda **kwargs: kwargs['image'])
    renderer._draw_overlay(raw_rectified_bgr=np.full((60,80,3),255,dtype=np.uint8), regions=[region()],
        records=[{**region(), 'resolution_state':'review_required', 'pred_digits':None, 'recognizer_raw_text':'1'}],
        quad_points=[], header_intersection_points=[], facility_code='fixture', order_id='fixture', details=[])
    assert len(labels) == 1 and labels[0].startswith('?') and '1' not in labels[0]


def test_real_producer_persists_unresolved_values_and_keeps_adoption_blocked(connected_physical_pipeline, monkeypatch):
    from uuid import uuid4
    from src.db import session_scope
    from src.models.order import Order
    from src.models.document import OrderDocument
    from src.models.order_workflow_state import OrderWorkflowState
    from src.services import order_service as orders, ocr_evidence_service as store
    from src.services import workflow_attempt_guard_service as guard
    order_id, _, _, item, _ = connected_physical_pipeline
    def run(**kwargs):
        targets = kwargs['regions']
        contact = Image.new('RGB', (len(targets)*10,10), 'white')
        polygons = [[[i*10,0],[(i+1)*10,0],[(i+1)*10,10],[i*10,10]] for i in range(len(targets))]
        rows, metrics = recognize(monkeypatch, contact, targets, polygons, ['1']*len(targets))
        return [{**r, 'raw_text':r['recognizer_raw_text'], 'pred_digits':r['ocr_normalized'], 'score':r['recognizer_score']} for r in rows], metrics, contact
    monkeypatch.setattr(renderer, '_run_text_recognizer_records', run)
    with session_scope() as session:
        order = session.get(Order, order_id)
        orders._record_current_order_version(session, order, session.get(OrderDocument, order.current_document_id))
        revision = session.get(OrderWorkflowState, order_id).revision
        job, attempt = str(uuid4()), str(uuid4())
        start, pdf, template = _capture_reparse_job(session, order_id, job, attempt)
    item['week_code'] = start['order']['week_code']
    item.update(selected_page=1, quad_px=[[20,20],[270,20],[270,290],[20,290]], quad_source='explicit-fixture', header_axis_override=None, row_axis_override=None)
    variables = [(v,v.set(value)) for v,value in ((orders._workflow_ocr_job_id,job), (orders._workflow_ocr_order_id,order_id),
        (orders._workflow_ocr_attempt_id,attempt), (orders._workflow_ocr_revision,revision))]
    try:
        with guard.workflow_execution_context(job, order_id, attempt, revision):
            payload = orders._hakodate_canonical_payload_from_manifest_item(order_id=order_id, facility_id='fixture', item={})
            assert payload['hakodate_recognition_review']['count'] > 0
            assert all(r['raw_text']=='1' and r['normalized_value'] is None for r in payload['hakodate_ocr_evidence_records'])
            saved = store.persist_evidence_run(order_id, payload, template_version_id=template,
                status='diagnostic_only', source='own-recognizer', producer_version='fixture/v1',
                workflow_job_id=job, workflow_attempt_id=attempt, expected_workflow_revision=revision)
            assert saved['status']=='diagnostic_only'
            assert saved['payload_json']['hakodate_ocr_evidence_records'] == payload['hakodate_ocr_evidence_records']
            assert not orders._classify_owned_evidence_payload(payload)['persistable']
            from src.services.canonical_ocr_artifact_service import _verify
            outputs = payload['hakodate_canonical_pipeline']['artifact_packet']['outputs']
            verified = {'records':_verify(outputs['records']), 'contact_sheet':_verify(outputs['contact_sheet'])}
            for key,value in (('normalized_value','1'), ('resolution_state','resolved')):
                changed = deepcopy(payload['hakodate_ocr_evidence_records'])
                changed[0][key] = value
                with pytest.raises(ValueError): orders._validate_hakodate_recognition_review(payload,changed,verified)
    finally:
        for variable,token in reversed(variables): variable.reset(token)


def test_synthetic_explicit_verifier_contract_and_tamper_rejection(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('verify_upload_synthetic', Path(__file__).resolve().parents[3]/'scripts/verify_stg_ocr_upload.py')
    verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
    cells = [f'{col}{row}' for row in range(11,67) for col in 'EFGHIJK']
    numeric = {f'E{row}':'26' for row in range(11,67)}; numeric['H40']='2'
    nonnumeric = {cell:'-' for cell in ('H17','G20','G21','G29','J38','G45','I49','I50','I60','J60')}
    white = [cell for cell in cells if cell not in numeric and cell not in nonnumeric]
    expected = {'blank_cells':white,'numeric_cells':numeric,'review_cells':nonnumeric,'note_cells':{},'blank_raw_text':'1'}
    contact = Image.new('RGB',(392*8,8),'white')
    regions=[]; polygons=[]; raw=[]
    for i,cell in enumerate(cells):
        regions.append({**region('note' if cell.startswith('K') else 'quantity'), 'sheet_cell':cell})
        polygons.append([[i*8,0],[(i+1)*8,0],[(i+1)*8,8],[i*8,8]])
        raw.append(numeric.get(cell,nonnumeric.get(cell,'1')))
        if cell not in white: contact.putpixel((i*8+2,2),(0,0,0))
    rows,metrics=recognize(monkeypatch,contact,regions,polygons,raw)
    sources=[record(r)['engine_metadata']['recognizer_output'] for r in rows]
    records=evidence.evidence_from_records([record(r) for r in rows],run_id='own',engine='fixture',source_scope='fixture')
    payload={'hakodate_ocr_evidence_records':records,'hakodate_ocr_evidence':{'records':deepcopy(records)},
        'hakodate_canonical_pipeline':{'metrics':metrics},'hakodate_recognition_review':evidence.recognition_review(records)}
    path=tmp_path/'contact.png';contact.save(path)
    assert verifier.verify_review_resolution(expected,sources,payload,path)['review_required']==335
    for mutation in ('state','raw','value','proof','api','count','blank'):
        bad=deepcopy(sources); api=deepcopy(payload); idx=cells.index('F11')
        if mutation=='state':bad[idx]['resolution_state']='resolved'
        elif mutation=='raw':bad[idx]['raw_text']='0'
        elif mutation=='value':bad[idx]['resolved_value']='1'
        elif mutation=='proof':bad[idx]['recognizer_input_evidence']['all_white']=False
        elif mutation=='api':api['hakodate_ocr_evidence_records'][idx]['normalized_value']='1'
        elif mutation=='count':api['hakodate_canonical_pipeline']['metrics']['raw_prediction_count']=391
        else:bad[idx]['blank_decision']={}
        with pytest.raises(ValueError):verifier.verify_review_resolution(expected,bad,api,path)


def test_opted_in_review_polling_preserves_ownership_and_legacy_checks():
    from test_stg_recognition_review_verification import case, mod
    args = case()
    assert mod.review_required_evidence(*args)
    payload = args[0]['payload_json']
    source = payload['hakodate_ocr_evidence_records'][0]['engine_metadata']['recognizer_output']
    source.update(resolution_state='review_required', resolved_value=None,
                  quantity_source_issue='hakodate_blank_input_nonempty_prediction')
    payload['hakodate_ocr_evidence_records'].append({'raw_text':'-', 'normalized_value':None,
        'engine_metadata':{'sheet_cell':'G29','recognizer_output':{
            'recognizer_raw_text':'-', 'resolution_state':'review_required', 'resolved_value':None,
            'quantity_source_issue':'hakodate_ocr_quantity_non_numeric'}}})
    payload['hakodate_recognition_review']['cells'].append({'sheet_cell':'G29','raw_text':'-', 'reason':'hakodate_ocr_quantity_non_numeric'})
    payload['hakodate_recognition_review']['count']=2
    assert mod.review_required_evidence(*args,review_resolution=True)
    assert not mod.review_required_evidence(*args)
    for key,value in (('status','done'), ('selectable',True)):
        altered=deepcopy(args); altered[0][key]=value
        assert not mod.review_required_evidence(*altered,review_resolution=True)
    for key in ('job_id','workflow_attempt_id'):
        altered=deepcopy(args); altered[0]['payload_json']['_ocr_execution'][key]='foreign'
        assert not mod.review_required_evidence(*altered,review_resolution=True)


@pytest.mark.skipif(not os.environ.get('HAKODATE_BLANK_ROOT_ARTIFACT_DIR'), reason='explicit real artifacts required')
def test_actual_392_raw_replay_and_explicit_verifier(monkeypatch):
    """Replay saved raw model responses, not a recognizer re-execution."""
    root = Path(os.environ['HAKODATE_BLANK_ROOT_ARTIFACT_DIR'])
    originals = json.loads((root/'recognizer-records.json').read_text())
    regions = json.loads((root/'checkpoint.json').read_text())['regions']
    assert len(regions) == len(originals) == 392
    assert [r['sheet_cell'] for r in regions] == [r['sheet_cell'] for r in originals]
    regions = [{**r, **{k:old[k] for k in ('ocr_contact_crop_box','ocr_contact_slot_index','ocr_cell_crop_bbox_px')}}
               for r,old in zip(regions,originals)]
    polygons = [[[x0,y0],[x1,y0],[x1,y1],[x0,y1]] for x0,y0,x1,y1 in [r['ocr_contact_crop_box'] for r in originals]]
    with Image.open(root/'recognizer-contact.png') as contact:
        rows, metrics = recognize(monkeypatch, contact.convert('RGB'), regions, polygons,
            [r['recognizer_raw_text'] for r in originals], [r['recognizer_score'] for r in originals],
            [r['recognizer_candidates'] for r in originals])
    sources = [record(r)['engine_metadata']['recognizer_output'] for r in rows]
    records = evidence.evidence_from_records([record(r) for r in rows], run_id='own', engine='fixture', source_scope='fixture')
    payload = {'hakodate_ocr_evidence_records': records, 'hakodate_ocr_evidence': {'records':deepcopy(records)},
               'hakodate_canonical_pipeline': {'metrics':metrics}, 'hakodate_recognition_review':evidence.recognition_review(records)}
    spec = importlib.util.spec_from_file_location('verify_upload', Path(__file__).resolve().parents[3]/'scripts/verify_stg_ocr_upload.py')
    verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
    expected = json.loads(Path(os.environ['HAKODATE_BLANK_ROOT_MANIFEST_PATH']).read_text())['cases'][0]['blank_resolution']
    assert verifier.verify_review_resolution(expected, sources, payload, root/'recognizer-contact.png') == {
        'white_pixels':325, 'review_required':335, 'numeric':57, 'automatic_blank':0, 'adopted_one':0}
    for old, row in zip(originals, rows):
        assert all(row[k] == old[k] for k in ('recognizer_raw_text','recognizer_score','recognizer_candidates'))
    print('raw_replay=392 white=325 review=335 numeric=57 automatic_blank=0 adopted_one=0')
    for change in ('raw', 'value', 'state', 'proof', 'api', 'blank'):
        bad = deepcopy(sources); altered = deepcopy(payload)
        index = next(i for i,r in enumerate(bad) if r['sheet_cell'] == 'F11')
        if change == 'raw': bad[index]['recognizer_raw_text'] = '0'
        elif change == 'value': bad[index]['pred_digits'] = '1'
        elif change == 'state': bad[index]['resolution_state'] = 'resolved'
        elif change == 'proof': bad[index]['recognizer_input_evidence']['rgb_sha256'] = '0'*64
        elif change == 'blank': bad[index]['blank_decision'] = {}
        else: altered['hakodate_ocr_evidence_records'][index]['normalized_value'] = '1'
        with pytest.raises(ValueError): verifier.verify_review_resolution(expected, bad, altered, root/'recognizer-contact.png')
