"""Synthetic acceptance mechanics, not native OCR or stg proof."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest
import yaml

from src.services import hakodate_preprocess_observation as observation
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime
from src.services.workflow_attempt_guard_service import canonical_snapshot_json
from test_hakodate_ocr_checkpoint_service import _item
from test_stg_ocr_resume_upload import mod
from test_stg_recognition_review_verification import case


def test_ci_includes_acceptance_without_replacing_existing_review_targets():
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load((root/'.github/workflows/ci.yml').read_text())
    steps = [step for job in workflow['jobs'].values() for step in job.get('steps',[])]
    step = next(step for step in steps if step.get('name') == 'Verify OCR review-resolution root regression')
    command = step['run']
    assert command.count('tests/unit/test_stg_ocr_native_empty_resolution.py') == 1
    for target in ('test_hakodate_review_resolution_root.py','test_hakodate_blank_input_review.py',
            'test_hakodate_blank_review_persistence.py','test_hakodate_raw_authority.py',
            'test_hakodate_empty_sequence_model.py','test_hakodate_text_recognizer_preprocess.py',
            'test_yomitoku_text_recognizer_topk.py','test_stg_recognition_review_verification.py'):
        assert 'tests/unit/'+target in command
    assert '--confcutdir' not in command


@pytest.fixture
def content(tmp_path,request):
    provenance = json.loads((Path(__file__).resolve().parents[2] /
        'src/data/ocr_models/hakodate_empty_sequence_v1/provenance.json').read_text())
    expected = {'blank_cells':['B1'], 'numeric_cells':{'A1':'0','C1':'1','D1':'26'},
        'review_cells':{'E1':'-'}, 'expected_model':{k:provenance[k] for k in
        ('contract','base_sha256','adapter_sha256','learned_source_sha256')}}
    image = Image.new('RGB',(500,200),'white')
    draw = ImageDraw.Draw(image)
    regions = []
    for index,cell in enumerate(['A1','B1','C1','D1','E1']):
        x = 20+index*90
        box = [x,60,x+80,140]
        draw.rectangle(box,outline='black')
        raw = '' if cell == 'B1' else expected['numeric_cells'].get(cell,'-')
        draw.text((x+30,85),raw,font=ImageFont.load_default(size=24),fill='black')
        if cell == 'B1' and getattr(request,'param',False):
            draw.ellipse((x+35,93,x+40,98),fill=(128,128,128))
        regions.append({'sheet_cell':cell,'role':'quantity','field':'qty.regular','bbox':box,
            'polygon':[[x,60],[x+80,60],[x+80,140],[x,140]]})
    image.save(tmp_path/'source.png')
    contact_image,regions,_,skipped = runtime.build_recognizer_contact_sheet(
        rectified_fax_bgr=np.asarray(image).copy(),regions=regions,line_mask=None,mode='corner_cc')
    assert not skipped
    records, api = [], []
    for region in regions:
        cell = region['sheet_cell']
        raw = '' if cell == 'B1' else expected['numeric_cells'].get(cell,'-')
        review = cell == 'E1'
        source = {**region,
            'raw_text':raw, 'recognizer_raw_text':raw, 'recognizer_score':.95,
            'recognizer_candidates':[{'text':raw,'score':.95,'token_probs':[.95]}],
            'recognizer_input_evidence':deepcopy(region['recognizer_preprocess_observation']['contact']),
            'recognizer_candidate_accepted':False,
            'recognizer_accepted_candidate':None,'recognizer_decision_source':'review_required' if review else 'raw_digits' if raw else '',
            'resolution_state':'review_required' if review else 'resolved',
            'resolved_value':None if review else raw,'pred_digits':None if review else raw,
            'ocr_text':None if review else raw,'ocr_normalized':None if review else raw,
            'quantity_source_issue':'hakodate_ocr_quantity_non_numeric' if review else None}
        records.append(source)
        api.append({'raw_text':raw,'normalized_value':None if review else raw,'confidence':.95,
            'resolution_state':source['resolution_state'],
            'engine_metadata':{'sheet_cell':cell,'recognizer_output':deepcopy(source)}})
    contact = tmp_path/'contact.png'; contact_image.save(contact)
    payload = {'hakodate_ocr_evidence_records':api,'hakodate_ocr_evidence':{'records':deepcopy(api)},
        'hakodate_recognition_review':{'count':1,'cells':[{'sheet_cell':'E1','raw_text':'-',
            'reason':'hakodate_ocr_quantity_non_numeric'}]},
        'hakodate_canonical_pipeline':{'expected_sheet_cells':[r['sheet_cell'] for r in records],
            'metrics':{'raw_prediction_count':5,'recognition_model_metadata':provenance}}}
    return expected, records, payload, contact


def test_generic_content_preserves_zero_one_twenty_six_and_native_empty(content):
    assert mod._native_empty_records(*content) == {'white_pixels':1,'source_blank_count':1,'numeric':3,'review_required':1}
    assert content[1][1]['recognizer_preprocess_ink_removed'] is True


@pytest.fixture
def serialized_content(content):
    expected, records, payload, contact = deepcopy(content)
    for index, source in enumerate(records):
        for key in ('ocr_text', 'ocr_normalized'):
            source.pop(key)
        payload['hakodate_ocr_evidence_records'][index]['engine_metadata']['recognizer_output'] = deepcopy(source)
    payload['hakodate_ocr_evidence']['records'] = deepcopy(payload['hakodate_ocr_evidence_records'])
    return expected, records, payload, contact


@pytest.mark.parametrize('cell', ['B1', 'A1', 'C1', 'D1'])
@pytest.mark.parametrize('field', ['ocr_text', 'ocr_normalized'])
@pytest.mark.parametrize('state', ['absent', 'matching', 'null', 'mismatched'])
def test_serialized_internal_fields_are_optional_but_never_coerced(serialized_content, cell, field, state):
    expected, records, payload, contact = serialized_content
    index = next(index for index, row in enumerate(records) if row['sheet_cell'] == cell)
    source = records[index]
    raw = '' if cell in expected['blank_cells'] else expected['numeric_cells'][cell]
    if state != 'absent':
        source[field] = raw if state == 'matching' else None if state == 'null' else '9'
    payload['hakodate_ocr_evidence_records'][index]['engine_metadata']['recognizer_output'] = deepcopy(source)
    payload['hakodate_ocr_evidence']['records'] = deepcopy(payload['hakodate_ocr_evidence_records'])
    if state in ('null', 'mismatched'):
        with pytest.raises(ValueError, match='Native empty resolved value changed'):
            mod._native_empty_records(expected, records, payload, contact)
    else:
        assert mod._native_empty_records(expected, records, payload, contact) == {
            'white_pixels': 1, 'source_blank_count': 1, 'numeric': 3, 'review_required': 1}


@pytest.mark.parametrize('change',['raw1','number','extra_review','model','expected_model','pixel','bbox',
    'duplicate','score','candidate_nan','candidate_adoption','blank_decision','proof','api_raw','review_count',
    'low_confidence','api_score','source_geometry','warp','overlap','missing_cell'])
def test_content_rejects_corruption(content,change):
    expected,records,payload,contact = content
    if change == 'pixel':
        with Image.open(contact) as image:
            box = records[1]['ocr_contact_crop_box']
            image.putpixel((box[0]+1,box[1]+1),(0,0,0)); image.save(contact)
    elif change == 'model': payload['hakodate_canonical_pipeline']['metrics']['recognition_model_metadata']['adapter_sha256'] = '0'*64
    elif change == 'expected_model': expected['expected_model']['base_sha256'] = '0'*64
    elif change == 'duplicate': records[1]['sheet_cell'] = 'A1'
    elif change == 'review_count': payload['hakodate_recognition_review']['count'] = 2
    elif change == 'api_raw': payload['hakodate_ocr_evidence_records'][0]['raw_text'] = '9'
    elif change == 'api_score': payload['hakodate_ocr_evidence_records'][0]['confidence'] = .1
    elif change == 'overlap': expected['blank_cells'].append('C1')
    elif change == 'missing_cell': del expected['numeric_cells']['C1']
    else:
        source = records[1] if change in ('raw1','extra_review','blank_decision','proof') else records[0]
        if change == 'raw1': source['recognizer_raw_text'] = source['raw_text'] = '1'
        elif change == 'number': source['resolved_value'] = source['pred_digits'] = '9'
        elif change == 'extra_review': source.update(resolution_state='review_required',resolved_value=None,pred_digits=None)
        elif change == 'score': source['recognizer_score'] = float('nan')
        elif change == 'candidate_nan': source['recognizer_candidates'][0]['token_probs'] = [float('inf')]
        elif change == 'candidate_adoption': source.update(recognizer_candidate_accepted=True,recognizer_accepted_candidate={'text':'9'})
        elif change == 'blank_decision': source['blank_decision'] = {'value':''}
        elif change == 'proof': del source['recognizer_preprocess_observation']
        elif change == 'bbox': source['ocr_contact_crop_box'][2] = 100000
        elif change == 'source_geometry': source['polygon'][0][0] += 1
        elif change == 'warp': source['recognizer_preprocess_observation']['actual_warp']['sha256'] = '0'*64
        elif change == 'low_confidence':
            source.update(recognizer_score=.1,recognizer_digit_score_threshold=.55)
            payload['hakodate_ocr_evidence_records'][0]['confidence'] = .1
        index = records.index(source)
        payload['hakodate_ocr_evidence_records'][index]['engine_metadata']['recognizer_output'] = deepcopy(source)
    payload['hakodate_ocr_evidence']['records'] = deepcopy(payload['hakodate_ocr_evidence_records'])
    with pytest.raises(ValueError):
        mod._native_empty_records(expected,records,payload,contact)


class StagingObjects:
    """Fake provider I/O only; real URI, hash, checkpoint and source validators run."""
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.downloads = []

    def bucket(self,name):
        assert name == 'sawahospitalsystem-stg-raw'
        return self

    def blob(self,name,generation=None):
        owner = self
        uri = mod.BUCKET+name+'?generation=1'
        assert generation in (None,1)
        class Blob:
            generation = '1'
            def reload(self,**kwargs):
                assert uri in owner.objects
                owner.reads.append(uri)
            def upload_from_string(self,data,**kwargs): owner.objects[uri] = data
            def download_as_bytes(self,**kwargs):
                owner.reads.append(uri)
                return owner.objects[uri]
        return Blob()

    def cli(self,command,*,check):
        assert command[:3] == ['gcloud','storage','cp'] and check is True
        self.downloads.append(command[3])
        uri = next(uri for uri in self.objects if mod.staging_cli_uri(uri) == command[3])
        Path(command[4]).write_bytes(self.objects[uri])


@pytest.fixture
def staging(monkeypatch):
    from src.services import storage_service
    store = StagingObjects()
    monkeypatch.setattr(storage_service._GCS_CLIENT_LOCAL,'client',store,raising=False)
    monkeypatch.setattr(mod.subprocess,'run',store.cli)
    monkeypatch.delenv('OCR_ARTIFACT_DIR',raising=False)
    monkeypatch.setenv('OCR_OUTPUT_BUCKET','sawahospitalsystem-stg-raw')
    return store


def canonical(content,tmp_path,staging):
    from src.services import canonical_ocr_artifact_service as artifacts
    expected,records,payload,contact = content
    workspace = tmp_path/'workspace'; workspace.mkdir()
    item = _item(tmp_path)
    item['order_id'] = 'order'
    item['selected_pdf_receipt'].update(order_id='order',document_id='pdf')
    pdf = Path(item['selected_pdf_receipt']['storage_uri']).read_bytes()
    pdf_uri = mod.BUCKET+'fixture/source.pdf?generation=1'
    staging.objects[pdf_uri] = pdf
    item['selected_pdf_receipt'].update(storage_uri=pdf_uri,generation='1')
    cells = [r['sheet_cell'] for r in records]
    item['original_expected_sheet_cells'] = cells
    item['original_target_semantics'] = [observation.geometry(r) for r in records]
    checkpoint = checkpoints.persist_recognizer_checkpoint(item=item,
        raw_rectified_bgr=cv2.imread(str(tmp_path/'source.png')),
        regions=[observation.geometry(r) for r in records],expected_sheet_cells=cells)
    manifest = {'contract':observation.CONTRACT,'checkpoint':checkpoint,
        'observations':[r['recognizer_preprocess_observation'] for r in records]}
    outputs = {}
    for key in artifacts.REQUIRED_OUTPUTS + ('preprocess_observation',):
        path = workspace/key
        data = (contact.read_bytes() if key in ('overlay','overlay_png','sheet_review_base','contact_sheet') else
            b'%PDF-synthetic' if key == 'pdf' else json.dumps(records).encode() if key in ('records','ocr_regions') else
            json.dumps(manifest).encode() if key == 'preprocess_observation' else b'{}')
        path.write_bytes(data); outputs[key] = str(path)
    source = {'selected_pdf_receipt':deepcopy(item['selected_pdf_receipt']),
        'configured_input_digest':hashlib.sha256(canonical_snapshot_json(item['configured_input']).encode()).hexdigest(),
        'expected_sheet_cells':cells}
    packet = artifacts.persist_outputs(order_id='order',outputs=outputs,source_receipt=source,
        expected_sheet_cells=[r['sheet_cell'] for r in records],producer_version='fixture',workspace=workspace)
    saved = {key:receipt['uri'] for key,receipt in packet['outputs'].items()}
    payload['hakodate_repair_source_receipt'] = source
    payload['_ocr_execution'] = {'selected_pdf_receipt':deepcopy(source['selected_pdf_receipt'])}
    payload['hakodate_canonical_pipeline'].update(artifact_packet=packet,outputs=saved,version='fixture',requested_order_id='order')
    payload['hakodate_overlay'] = {'uri':saved['overlay'],'sheet_review_base_uri':saved['sheet_review_base']}
    for row in payload['hakodate_ocr_evidence_records']:
        row['raw_payload_ref'] = saved['records']
    payload['hakodate_ocr_evidence']['records'] = deepcopy(payload['hakodate_ocr_evidence_records'])
    return content


@pytest.mark.parametrize('changed',['none','source','observation_bytes','records_bytes','metadata_bytes','source_bytes','source_geometry','contact_bytes','missing_proof'])
def test_full_boundary_never_accepts_partial_deployment_or_tampered_artifacts(content,tmp_path,staging,changed):
    expected,records,payload,contact = canonical(content,tmp_path,staging)
    packet = payload['hakodate_canonical_pipeline']['artifact_packet']
    if changed == 'none':
        assert mod.verify_native_empty_resolution(expected,records,payload,contact) == {
            'white_pixels':1,'source_blank_count':1,'numeric':3,'review_required':1}
        assert staging.downloads
        return
    if changed == 'source': payload['hakodate_repair_source_receipt']['selected_pdf_receipt']['sha256'] = '0'*64
    elif changed == 'source_bytes':
        manifest = json.loads(staging.objects[packet['outputs']['preprocess_observation']['uri']])
        staging.objects[manifest['checkpoint']['image']['uri']] = b'changed'
    elif changed == 'source_geometry':
        records[0]['polygon'][0][0] += 1
        payload['hakodate_ocr_evidence_records'][0]['engine_metadata']['recognizer_output'] = deepcopy(records[0])
        payload['hakodate_ocr_evidence']['records'] = deepcopy(payload['hakodate_ocr_evidence_records'])
    elif changed == 'missing_proof':
        payload['hakodate_canonical_pipeline']['outputs'].pop('preprocess_observation')
    elif changed != 'none':
        key = {'observation_bytes':'preprocess_observation','records_bytes':'records','metadata_bytes':'metadata',
            'contact_bytes':'contact_sheet'}[changed]
        receipt = packet['metadata'] if key == 'metadata' else packet['outputs'][key]
        staging.objects[receipt['uri']] = b'changed'
    with pytest.raises(ValueError):
        mod.verify_native_empty_resolution(expected,records,payload,contact)


def test_partial_deployment_without_observation_validator_fails_closed(content,tmp_path,staging,monkeypatch):
    canonical(content,tmp_path,staging)
    monkeypatch.setitem(sys.modules,'src.services.hakodate_preprocess_observation',None)
    with pytest.raises(ValueError,match='observation validator unavailable'):
        mod.verify_native_empty_resolution(*content)


@pytest.mark.parametrize('content',[True],indirect=True)
def test_nonwhite_source_blank_empty_raw_keeps_full_source_and_contact_validation(content,tmp_path,staging):
    canonical(content,tmp_path,staging)
    assert content[1][1]['recognizer_input_evidence']['all_white'] is False
    assert mod.verify_native_empty_resolution(*content) == {
        'white_pixels':0,'source_blank_count':1,'numeric':3,'review_required':1}


def test_canonical_serialized_records_without_internal_fields_pass_full_boundary(serialized_content, tmp_path, staging):
    canonical(serialized_content, tmp_path, staging)
    assert mod.verify_native_empty_resolution(*serialized_content) == {
        'white_pixels': 1, 'source_blank_count': 1, 'numeric': 3, 'review_required': 1}


def test_white_real_one_is_not_adopted_even_with_consistent_pixel_receipts(content):
    expected,records,payload,contact = content
    source = records[2]
    with Image.open(contact) as image:
        box = source['ocr_contact_crop_box']
        image.paste('white',box)
        image.save(contact)
        proof = runtime.contact_crop_evidence(image,[[box[0],box[1]],[box[2],box[1]],
            [box[2],box[3]],[box[0],box[3]]])
    source['recognizer_input_evidence'] = proof
    source['recognizer_preprocess_observation']['contact'] = deepcopy(proof)
    source['recognizer_preprocess_ink_removed'] = bool(source['recognizer_preprocess_observation']['bbox_ink_pixels'])
    payload['hakodate_ocr_evidence_records'][2]['engine_metadata']['recognizer_output'] = deepcopy(source)
    payload['hakodate_ocr_evidence']['records'] = deepcopy(payload['hakodate_ocr_evidence_records'])
    from src.services.hakodate_ocr_evidence_service import recognition_input_issue
    assert recognition_input_issue(source) == 'hakodate_blank_input_nonempty_prediction'
    with pytest.raises(ValueError,match='hakodate_ocr_quantity_unresolved'):
        mod._native_empty_records(*content)


@pytest.mark.parametrize('location',['pipeline','metadata','source_parent','checkpoint','image','pdf_parent'])
@pytest.mark.parametrize('foreign',['gs://sawahospitalsystem-raw/foreign','local','file:///private/foreign',
    'https://example.invalid/foreign',mod.BUCKET+'foreign?generation=1&extra=2',mod.BUCKET+'wild*'])
def test_recursive_staging_fence_precedes_all_foreign_reads(
        content,tmp_path,staging,monkeypatch,location,foreign):
    canonical(content,tmp_path,staging)
    pipeline = content[2]['hakodate_canonical_pipeline']
    packet = pipeline['artifact_packet']
    forbidden = tmp_path/'must-not-read'
    forbidden.write_bytes(b'not evidence')
    if foreign == 'local': foreign = str(forbidden)
    reads = []
    original = Path.read_bytes
    def read(path):
        if path == forbidden: reads.append(path)
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    nested = location in ('checkpoint','image','pdf_parent')
    if location == 'pipeline': pipeline['outputs']['records'] = foreign
    elif location == 'metadata': packet['metadata']['uri'] = foreign
    elif location == 'source_parent':
        content[2]['hakodate_repair_source_receipt']['selected_pdf_receipt']['parent'] = {'storage_uri':foreign}
    else:
        receipt = packet['outputs']['preprocess_observation']
        value = json.loads(staging.objects[receipt['uri']])
        checkpoint = value['checkpoint']
        if location == 'checkpoint': checkpoint['checkpoint']['uri'] = foreign
        elif location == 'image': checkpoint['image']['uri'] = foreign
        else: checkpoint['source']['selected_pdf_receipt']['parent'] = {'storage_uri':foreign}
        data = json.dumps(value).encode()
        staging.objects[receipt['uri']] = data
        receipt.update(sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data))
    staging.reads.clear(); staging.downloads.clear()
    with pytest.raises(ValueError): mod.verify_native_empty_resolution(*content)
    assert not reads and not staging.reads
    assert len(staging.downloads) == int(nested)


@pytest.mark.parametrize('changed',['none','job','attempt','pdf','previous','selectable','status'])
def test_new_path_keeps_exact_review_identity_and_nonadoption_guards(changed):
    args = list(case()); evidence,workflow = args[:2]
    source = evidence['payload_json']['hakodate_ocr_evidence_records'][0]['engine_metadata']['recognizer_output']
    source.update(resolution_state='review_required',quantity_source_issue='hakodate_blank_input_nonempty_prediction')
    if changed in ('job','attempt','pdf'):
        execution = evidence['payload_json']['_ocr_execution']
        if changed == 'pdf': execution['selected_pdf_receipt']['document_id'] = 'other'
        else: execution[{'job':'job_id','attempt':'workflow_attempt_id'}[changed]] = 'other'
    elif changed == 'previous': args[-1] = evidence['id']
    elif changed == 'selectable': evidence['selectable'] = True
    elif changed == 'status': workflow['ocr_job']['status'] = 'done'
    assert mod.review_required_evidence(*args,review_resolution=True) is (changed == 'none')


@pytest.mark.parametrize('changed',['old_review','old_blank','done','shape'])
def test_manifest_exclusivity_fails_before_pdf_or_api_access(tmp_path,monkeypatch,changed):
    item = {'expected_outcome':'recognition_review_required','native_empty_resolution':{}}
    if changed == 'old_review': item['review_resolution'] = {}
    elif changed == 'old_blank': item['blank_resolution'] = {}
    elif changed == 'done': item['expected_outcome'] = 'done'
    elif changed == 'shape': item['native_empty_resolution'] = []
    calls = []
    def download(uri,path):
        calls.append(uri)
        path.write_text(json.dumps({'cases':[item]}))
    monkeypatch.setattr(sys,'argv',['verifier','--manifest','fixture-manifest','--output-dir',str(tmp_path/'out')])
    monkeypatch.setattr(mod,'download',download)
    monkeypatch.setattr(mod,'request',lambda *a,**kw:pytest.fail('API must not be accessed'))
    with pytest.raises(ValueError,match='Native empty'):
        mod.main()
    assert calls == ['fixture-manifest']
