"""Structural observations, synthetic decisions, and explicit private raw replay."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import types

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime
from src.services import hakodate_recognizer_contact_sheet as contact_sheet
from src.services import hakodate_ocr_evidence_service as evidence
from src.services import hakodate_preprocess_observation as observation
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from test_hakodate_ocr_checkpoint_service import _item
from test_hakodate_review_resolution_root import recognize, record
from test_h2_operator_repair_revision import connected_physical_pipeline, _capture_reparse_job


def region():
    return {'sheet_cell':'E11','role':'quantity','field':'qty.regular','bbox':[80.,80.,160.,140.],
        'polygon':[[80.,80.],[160.,80.],[160.,140.],[80.,140.]]}


def image(glyph=None):
    source = Image.new('RGB',(240,240),'white')
    draw = ImageDraw.Draw(source)
    draw.rectangle((80,80,160,140),outline='black')
    if glyph:
        draw.text((110,100),glyph,font=ImageFont.load_default(size=20),fill='black')
    return np.asarray(source).copy()


def prepare(source,regions=None):
    return runtime.build_recognizer_contact_sheet(rectified_fax_bgr=source,regions=regions or [region()],line_mask=None,mode='corner_cc')


@pytest.mark.parametrize('role',['quantity','note'])
def test_fresh_empty_raw_resolves_without_value_replacement_or_auto_selection(monkeypatch,role):
    from src.services import order_workflow_v2_service as workflow
    monkeypatch.setattr(workflow,'select_ocr_result',lambda *a,**kw:pytest.fail('selection requires operator API'))
    authored = region(); authored.update(role=role,field='qty.regular' if role == 'quantity' else 'note')
    contact,regions,polygons,_ = prepare(image(),[authored])
    rows,_ = recognize(monkeypatch,contact,regions,polygons,[''],[.99],[[{'text':'','score':.99}]])
    source = rows[0]
    assert source['recognizer_preprocess_ink_removed'] is True
    assert source['resolution_state'] == 'resolved' and source['resolved_value'] == ''
    assert source['recognizer_raw_text'] == '' and source['recognizer_candidates'] == [{'text':'','score':.99}]
    legacy = deepcopy(source); del legacy['recognizer_preprocess_observation']
    assert evidence.recognition_input_issue(legacy) == 'hakodate_preprocess_ink_loss'


@pytest.mark.parametrize('raw',['1','0','26','-'])
def test_white_quantity_raw_remains_editable_with_observation(monkeypatch,raw):
    contact,regions,polygons,_ = prepare(image())
    rows,_ = recognize(monkeypatch,contact,regions,polygons,[raw],[.99])
    assert rows[0]['recognizer_raw_text'] == raw and rows[0]['resolved_value'] == raw
    assert evidence.recognition_input_issue(rows[0]) == 'hakodate_blank_input_nonempty_prediction'


@pytest.mark.parametrize('glyph',['0','1','26','-'])
def test_nonwhite_numbers_and_nonnumeric_authority_unchanged(monkeypatch,glyph):
    contact,regions,polygons,_ = prepare(image(glyph))
    rows,_ = recognize(monkeypatch,contact,regions,polygons,[glyph],[.99])
    assert rows[0]['recognizer_raw_text'] == glyph
    assert rows[0]['resolved_value'] == glyph
    assert evidence.validate_ocr_quantity_source(record(rows[0])) == glyph


@pytest.mark.parametrize('white',[True,False])
def test_warp_diagnostic_does_not_replace_quantity_raw(monkeypatch,white):
    wrong = np.full((60,80,3),255,np.uint8)
    if not white:
        cv2.putText(wrong,'1',(30,40),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,0,0),2)
    monkeypatch.setattr(contact_sheet,'_warp_polygon_cell_to_axis_aligned_crop',lambda *args:(
        wrong,[0.,0.,80.,60.],(0,0,80,60),(80,80,160,140)))
    contact,regions,polygons,_ = prepare(image('26'))
    rows,_ = recognize(monkeypatch,contact,regions,polygons,['' if white else '1'],[.99])
    assert rows[0]['resolution_state'] == 'resolved'
    assert rows[0]['resolved_value'] == rows[0]['recognizer_raw_text']
    assert evidence.recognition_input_issue(rows[0]) == 'hakodate_preprocess_ink_loss'


def packet(tmp_path,monkeypatch):
    monkeypatch.setenv('OCR_ARTIFACT_DIR',str(tmp_path/'owned-artifacts'))
    monkeypatch.delenv('OCR_OUTPUT_BUCKET',raising=False)
    checkpoint = checkpoints.persist_recognizer_checkpoint(item=_item(tmp_path),raw_rectified_bgr=image(),
        regions=[region()],expected_sheet_cells=['E11'])
    contact,rows,polygons,_ = prepare(image())
    rows,_ = recognize(monkeypatch,contact,rows,polygons,[''],[.99])
    output = BytesIO(); contact.save(output,format='PNG')
    manifest = {'contract':observation.CONTRACT,'checkpoint':checkpoint,
        'observations':[r['recognizer_preprocess_observation'] for r in rows]}
    from src.services.workflow_attempt_guard_service import canonical_snapshot_json
    source = {**checkpoint['source'],'expected_sheet_cells':['E11'],
        'configured_input_digest':sha256(canonical_snapshot_json(checkpoint['resume_item']['configured_input']).encode()).hexdigest()}
    return manifest,rows,output.getvalue(),source


@pytest.mark.parametrize('wrong_box',[[0,0,84,80],[79,70,162,150]])
def test_axis_aligned_wrong_crop_cannot_certify_another_white_region(wrong_box):
    authored = region(); del authored['polygon']
    source = np.full((240,240,3),255,np.uint8)
    x0,y0,x1,y1 = wrong_box
    with pytest.raises(ValueError,match='hakodate_observation_source_crop_changed'):
        observation.observe(source,authored,source[y0:y1,x0:x1],wrong_box,
            observation.pixels(source)['sha256'])


def test_axis_aligned_crop_is_derived_from_original_bbox():
    authored = region(); del authored['polygon']
    source = np.full((240,240,3),255,np.uint8)
    correct = [78,70,162,150]
    result = observation.observe(source,authored,source[70:150,78:162],correct,
        observation.pixels(source)['sha256'])
    assert result['source_crop_box'] == correct
    assert result['actual_warp'] == result['expected_warp']


@pytest.mark.parametrize('change',['none','source','geometry','polygon_removed','actual_warp','expected_warp','contact','contact_proof','flag','contract','checkpoint','configuration','artifact'])
def test_real_source_artifact_boundary_rejects_tampered_observations(tmp_path,monkeypatch,change):
    manifest,rows,contact,source = packet(tmp_path,monkeypatch)
    if change == 'source': rows[0]['recognizer_preprocess_observation']['source_sha256'] = '0'*64
    elif change == 'geometry': rows[0]['bbox'][0] += 1
    elif change == 'polygon_removed': del rows[0]['polygon']
    elif change in ('actual_warp','expected_warp'): rows[0]['recognizer_preprocess_observation'][change]['sha256'] = '0'*64
    elif change == 'contact':
        output = BytesIO(); Image.new('RGB',(10,10)).save(output,format='PNG'); contact = output.getvalue()
    elif change == 'contact_proof':
        rows[0]['recognizer_input_evidence']['rgb_sha256'] = '0'*64
    elif change == 'flag': rows[0]['recognizer_preprocess_ink_removed'] = False
    elif change == 'contract': rows[0]['recognizer_preprocess_observation']['contract'] = 'unknown'
    elif change == 'checkpoint': source['selected_pdf_receipt']['sha256'] = '0'*64
    elif change == 'configuration': source['configured_input_digest'] = '0'*64
    elif change == 'artifact': manifest['observations'] = []
    if change == 'none':
        observation.validate_artifact(json.dumps(manifest).encode(),rows,contact,source)
    else:
        with pytest.raises(ValueError):
            observation.validate_artifact(json.dumps(manifest).encode(),rows,contact,source)


def baseline():
    relative = 'backend/src/hakodate_best_method_runtime/run_text_recognizer_corner_noise_trial.py'
    root = Path(__file__).resolve().parents[3]
    module = types.ModuleType('observation_pixel_baseline'); module.__file__ = str(root/relative)
    exec(compile(subprocess.check_output(['git','show','HEAD:'+relative],cwd=root,text=True),module.__file__,'exec'),module.__dict__)
    return module


@pytest.mark.skipif(not os.environ.get('OBSERVATION_ARTIFACT_DIR'),reason='explicit private preprocessing/raw replay inputs required')
def test_actual_392_pixels_and_saved_raw_replay_not_native_inference(monkeypatch):
    root = Path(os.environ['OBSERVATION_ARTIFACT_DIR'])
    assert sha256((root/'checkpoint.json').read_bytes()).hexdigest() == '6a58dcad8b02ebec31ee45ca76e4e725f34a00bdb4dcc3ab591e43e37134599a'
    assert sha256((root/'rectified.png').read_bytes()).hexdigest() == 'fec00cb06e27cfee5b2496cf18ad4b041e0a0ddbfa25ca341eec8f2e6cc2be88'
    checkpoint = json.loads((root/'checkpoint.json').read_text())
    assert sha256((root/'original.pdf').read_bytes()).hexdigest() == checkpoint['source']['selected_pdf_receipt']['sha256']
    originals = json.loads((root/'recognizer-records.json').read_text())
    expected = json.loads(Path(os.environ['OBSERVATION_MANIFEST_PATH']).read_text())['cases'][0]['blank_resolution']
    source_blanks = set(expected['blank_cells'])
    assert len(source_blanks) == 325
    pixels = cv2.imread(str(root/'rectified.png'))
    before,_,_,_ = baseline().build_recognizer_contact_sheet(rectified_fax_bgr=pixels,regions=checkpoint['regions'],line_mask=None,mode='corner_cc')
    contact,regions,polygons,skipped = prepare(pixels,checkpoint['regions'])
    assert not skipped and len(regions) == len(originals) == 392
    assert before.tobytes() == contact.tobytes() and before.size == contact.size
    rows,_ = recognize(monkeypatch,contact,regions,polygons,[r['recognizer_raw_text'] for r in originals],
        [r['recognizer_score'] for r in originals],[r['recognizer_candidates'] for r in originals])
    report = {'base':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'verification':'preprocessing and saved raw replay; not native model inference',
        'cells':392,'source_blank_cells':len(source_blanks),'white':0,'numeric':0,'raw_unchanged':0,'historical_differences':[],'white_state_differences':[]}
    with Image.open(root/'recognizer-contact.png') as historical:
        for old,row in zip(originals,rows):
            cell = row['sheet_cell']; crop = contact.crop(row['ocr_contact_crop_box'])
            old_crop = historical.crop(old['ocr_contact_crop_box']).convert('RGB')
            white = crop.getextrema() == ((255,255),)*3
            report['white'] += white
            if white:
                assert cell in source_blanks
            assert all(row[k] == old[k] for k in ('recognizer_raw_text','recognizer_score','recognizer_candidates'))
            report['raw_unchanged'] += 1
            if cell in expected['numeric_cells']:
                assert not white and evidence.validate_ocr_quantity_source(record(row)) == expected['numeric_cells'][cell]
                report['numeric'] += 1
            if crop.size != old_crop.size or crop.tobytes() != old_crop.tobytes(): report['historical_differences'].append(cell)
            if white != (old_crop.getextrema() == ((255,255),)*3): report['white_state_differences'].append(cell)
    report['input_hash_differences'] = 0
    Path(os.environ['OBSERVATION_REPORT']).write_text(json.dumps(report,indent=2)+'\n')
    assert report['numeric'] == 57 and report['raw_unchanged'] == 392


@pytest.mark.skipif(not os.environ.get('OBSERVATION_REAL_INPUTS'),reason='explicit verified real zero/one source paths required')
def test_real_zero_one_input_bytes_unchanged():
    previous = baseline()
    for case in json.loads(Path(os.environ['OBSERVATION_REAL_INPUTS']).read_text()):
        pixels = cv2.imread(case['rectified'])
        records = {r['sheet_cell']:r for r in json.loads(Path(case['records']).read_text())}
        regions = [records[cell] for cell in case['cells']]
        before,_,_,_ = previous.build_recognizer_contact_sheet(rectified_fax_bgr=pixels,regions=regions,line_mask=None,mode='corner_cc')
        after,_,_,skipped = prepare(pixels,regions)
        assert not skipped and before.size == after.size and before.tobytes() == after.tobytes()


@pytest.mark.parametrize('operator',[False,True])
def test_renderer_preserves_explicit_polygon_through_canonical_packet(tmp_path,monkeypatch,operator):
    from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as renderer
    from src.services import canonical_ocr_artifact_service as artifacts
    from src.services.workflow_attempt_guard_service import canonical_snapshot_json
    monkeypatch.setenv('OCR_ARTIFACT_DIR',str(tmp_path/'storage'))
    monkeypatch.delenv('OCR_OUTPUT_BUCKET',raising=False)
    authored = {**region(),'worksheet_row':11,'worksheet_col':5,
        'polygon':[[80.,80.],[160.,82.],[159.,140.],[81.,139.]]}
    item = _item(tmp_path)
    item['template_bbox'] = [80,80,160,140]
    monkeypatch.setattr(renderer,'_build_preprocess_for_ocr',lambda **kwargs:{
        'raw_rectified':image(),'expected_sheet_cells':['E11'],'target_regions':[authored],
        'rectified_quad_points':item['quad_px'],'axis_evidence':{}})
    monkeypatch.setattr(renderer,'_snap_regions_x_to_fax_lines_all_targets',lambda pixels,regions:(regions,{}))
    def run(**kwargs):
        contact,regions,polygons,skipped = prepare(kwargs['raw_rectified_bgr'],kwargs['regions'])
        assert not skipped
        rows,metrics = recognize(monkeypatch,contact,regions,polygons,[''],[.99],[[{'text':'','score':.99}]])
        return [{**r,'raw_text':'','pred_digits':r['ocr_normalized'],'score':.99} for r in rows],metrics,contact
    monkeypatch.setattr(renderer,'_run_text_recognizer_records',run)
    output = tmp_path/'workspace'
    summary,_ = renderer.build_best_method_for_manifest_item(item=item,page_index=1,
        draft_sheet={'fields':['qty.regular'],'rows':[['']]},output_dir=output,
        operator_values={'E11':'26'} if operator else None)
    rows = json.loads(Path(summary['outputs']['records']).read_text())
    assert rows[0]['polygon'] == authored['polygon']
    assert observation.validate_metadata(rows[0]) is None
    if operator:
        assert rows[0]['operator_value'] == rows[0]['pred_digits'] == '26'
        assert rows[0]['recognition_source'] == rows[0]['ocr_engine'] == 'operator'
    source = {'selected_pdf_receipt':item['selected_pdf_receipt'],'expected_sheet_cells':['E11'],
        'configured_input_digest':sha256(canonical_snapshot_json(item['configured_input']).encode()).hexdigest()}
    packet = artifacts.persist_outputs(order_id=item['order_id'],outputs=summary['outputs'],source_receipt=source,
        expected_sheet_cells=['E11'],producer_version='synthetic-renderer/v1',workspace=output)
    assert json.loads(artifacts._verify(packet['outputs']['records'])) == rows


@pytest.mark.parametrize('operator',[False,True])
@pytest.mark.parametrize('wrong_warp',[False,True])
def test_real_producer_serializer_packet_persistence_never_selects_or_overrides_source(
        connected_physical_pipeline,monkeypatch,operator,wrong_warp):
    from uuid import uuid4
    from src.db import session_scope
    from src.models.order import Order
    from src.models.document import OrderDocument
    from src.models.order_workflow_state import OrderWorkflowState
    from src.services import order_service as orders, ocr_evidence_service as store
    from src.services import workflow_attempt_guard_service as guard
    from src.services import canonical_ocr_artifact_service as artifacts
    from src.services import order_workflow_v2_service as workflow
    from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as renderer

    order_id,_,_,item,_ = connected_physical_pipeline
    monkeypatch.setattr(workflow,'select_ocr_result',lambda *a,**kw:pytest.fail('operator selection must be explicit'))
    def run(**kwargs):
        contact,regions,polygons,skipped = prepare(kwargs['raw_rectified_bgr'],kwargs['regions'])
        assert not skipped
        rows,metrics = recognize(monkeypatch,contact,regions,polygons,['']*len(regions),[.99]*len(regions),
            [[{'text':'','score':.99}]]*len(regions))
        return [{**r,'raw_text':r['recognizer_raw_text'],'pred_digits':r['ocr_normalized'],
                 'score':r['recognizer_score']} for r in rows],metrics,contact
    monkeypatch.setattr(renderer,'_run_text_recognizer_records',run)
    if operator:
        build = renderer.build_best_method_for_manifest_item
        def corrected(**kwargs):
            kwargs['operator_values'] = {cell:'26' for cell in item['original_expected_sheet_cells']}
            return build(**kwargs)
        monkeypatch.setattr(renderer,'build_best_method_for_manifest_item',corrected)
    if wrong_warp:
        # This fixture has authored axis-aligned cells. Corrupt the actual
        # pre-clean crop, not the independent prescribed-source computation.
        monkeypatch.setattr(contact_sheet,'_mask_crop_to_region_polygon',
            lambda crop,**kwargs:np.zeros_like(crop))
    def state():
        with session_scope() as session:
            row = session.get(OrderWorkflowState,order_id)
            return row.evidence_run_id,row.draft_id,row.confirmed_snapshot_id
    with session_scope() as session:
        order = session.get(Order,order_id)
        orders._record_current_order_version(session,order,session.get(OrderDocument,order.current_document_id))
        revision = session.get(OrderWorkflowState,order_id).revision
        job,attempt = str(uuid4()),str(uuid4())
        start,_,template = _capture_reparse_job(session,order_id,job,attempt)
    item['week_code'] = start['order']['week_code']
    item.update(selected_page=1,quad_px=[[20,20],[270,20],[270,290],[20,290]],
        quad_source='explicit-fixture',header_axis_override=None,row_axis_override=None)
    before = state()
    variables = [(v,v.set(value)) for v,value in ((orders._workflow_ocr_job_id,job),
        (orders._workflow_ocr_order_id,order_id),(orders._workflow_ocr_attempt_id,attempt),
        (orders._workflow_ocr_revision,revision))]
    try:
        with guard.workflow_execution_context(job,order_id,attempt,revision):
            if wrong_warp:
                with pytest.raises(ValueError,match='hakodate_observation_source_or_warp_changed'):
                    orders._hakodate_canonical_payload_from_manifest_item(order_id=order_id,facility_id='fixture',item={})
            else:
                payload = orders._hakodate_canonical_payload_from_manifest_item(order_id=order_id,facility_id='fixture',item={})
                outputs = payload['hakodate_canonical_pipeline']['artifact_packet']['outputs']
                rows = json.loads(artifacts._verify(outputs['records']))
                assert len(rows) == len(item['original_expected_sheet_cells'])
                assert all('polygon' in r and observation.validate_metadata(r) is None for r in rows)
                if operator:
                    assert all(r['ocr_engine']=='operator' and r['operator_value']=='26' for r in rows)
                else:
                    assert all(r['recognizer_raw_text']=='' for r in rows)
                artifacts.validate_payload(payload)
                saved = store.persist_evidence_run(order_id,payload,template_version_id=template,
                    status='diagnostic_only',source='synthetic-recognizer-or-operator',producer_version='fixture/v1',
                    workflow_job_id=job,workflow_attempt_id=attempt,expected_workflow_revision=revision)
                assert saved['payload_json']['hakodate_ocr_evidence_records'] == payload['hakodate_ocr_evidence_records']
                forged = deepcopy(payload)
                forged['hakodate_canonical_pipeline']['artifact_packet']['outputs'].pop('preprocess_observation')
                with pytest.raises(ValueError): artifacts.validate_payload(forged)
    finally:
        for variable,token in reversed(variables): variable.reset(token)
    assert state() == before
