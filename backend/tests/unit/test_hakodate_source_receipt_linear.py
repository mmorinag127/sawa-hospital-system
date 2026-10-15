from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.services import order_service
from src.services import hakodate_source_receipt as receipts
from test_modernization_repair_validation_boundary import owned_boundary
from test_h2_operator_repair_revision import repair_order


def cell_receipts(payload):
    return [cell['source_receipt'] for cell in payload['hakodate_preprocessing']['target_cell_map']] + [
        record['engine_metadata']['source_receipt'] for record in payload['hakodate_ocr_evidence']['records']]


def compact(payload, source):
    for cell in payload['hakodate_preprocessing']['target_cell_map']:
        cell['source_receipt'] = receipts.source_reference(source)
    for record in payload['hakodate_ocr_evidence']['records']:
        record['engine_metadata']['source_receipt'] = receipts.source_reference(source)
    payload['hakodate_ocr_evidence_records'] = deepcopy(payload['hakodate_ocr_evidence']['records'])


@pytest.mark.parametrize('operator', [False, True])
def test_legacy_and_reference_validate_and_expand_identically(owned_boundary, operator):
    _, _, _, source, payload = owned_boundary
    if operator:
        for record in payload['hakodate_ocr_evidence']['records']:
            meta = record['engine_metadata']
            region = meta.pop('recognizer_output')
            meta.update(source='operator', edited_by='unit-operator', operator_output={
                **region, 'recognition_source': 'operator', 'operator_value': '12'})
            record.update(raw_text='12', normalized_value='12', source_scope='operator_cell_review', engine='operator')
        payload['hakodate_ocr_evidence_records'] = deepcopy(payload['hakodate_ocr_evidence']['records'])
    order_service._validate_hakodate_owned_artifacts(payload, source)
    original = deepcopy(payload)
    compact(payload, source)
    order_service._validate_hakodate_owned_artifacts(payload, source)
    receipts.validate_receipts(cell_receipts(payload), source)
    for cell in payload['hakodate_preprocessing']['target_cell_map']:
        cell['source_receipt'] = deepcopy(source)
    for key in ('hakodate_ocr_evidence_records',):
        for record in payload[key]:
            record['engine_metadata']['source_receipt'] = deepcopy(source)
    for record in payload['hakodate_ocr_evidence']['records']:
        record['engine_metadata']['source_receipt'] = deepcopy(source)
    assert receipts.canonical_json(payload) == receipts.canonical_json(original)


@pytest.mark.parametrize('fault', ['hash', 'unknown', 'missing', 'mixed', 'source', 'target', 'record', 'extra'])
def test_shared_validator_rejects_tampering(owned_boundary, fault):
    _, _, _, source, payload = owned_boundary
    compact(payload, source)
    cell = payload['hakodate_preprocessing']['target_cell_map'][0]
    if fault == 'hash':
        cell['source_receipt']['sha256'] = '0' * 64
    elif fault == 'unknown':
        cell['source_receipt']['contract'] = 'hakodate_source_receipt_ref/v999'
    elif fault == 'missing':
        del cell['source_receipt']
    elif fault == 'mixed':
        cell['source_receipt'] = deepcopy(source)
    elif fault == 'source':
        payload['hakodate_repair_source_receipt']['selected_pdf_receipt']['sha256'] = '0' * 64
    elif fault == 'target':
        cell['sheet_cell'] = 'Z999'
    elif fault == 'record':
        payload['hakodate_ocr_evidence']['records'][0]['engine_metadata']['sheet_cell'] = 'Z999'
        payload['hakodate_ocr_evidence_records'] = deepcopy(payload['hakodate_ocr_evidence']['records'])
    else:
        cell['source_receipt']['extra'] = 'unexpected'
    with pytest.raises(ValueError):
        order_service._validate_hakodate_owned_artifacts(payload, source)


def test_canonical_unicode_order_and_nan():
    source = {'z': '函館', 'a': [1, None, False]}
    ref = receipts.source_reference(source)
    assert ref == receipts.source_reference(dict(reversed(list(source.items()))))
    assert ref['sha256'] == hashlib.sha256(json.dumps(source, ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    for invalid in (float('nan'), float('inf'), -float('inf')):
        with pytest.raises(ValueError):
            receipts.source_reference({'value': invalid})


def check_linear_sizes(source):
    sizes = []
    for n in (98, 196, 392):
        part = {**source, 'expected_sheet_cells': source['expected_sheet_cells'][:n],
                'expected_target_semantics': source['expected_target_semantics'][:n]}
        ref = receipts.source_reference(part)
        payload = {'hakodate_repair_source_receipt': part, 'artifact_packet': {'source_receipt': part},
                   'targets': [ref for _ in range(n)], 'evidence': [ref for _ in range(n)],
                   'evidence_alias': [ref for _ in range(n)]}
        sizes.append(len(receipts.canonical_json(payload).encode()))
        receipts.validate_receipts(payload['targets'] + payload['evidence'], part)
    assert sizes[1] < sizes[0] * 2.2
    assert sizes[2] < sizes[1] * 2.2
    assert sizes[2] < 600000
    return sizes


def test_synthetic_linear_wire_size(record_property):
    source = {'selected_pdf_receipt': {'order_id': 'unit-order', 'sha256': 'a' * 64},
              'expected_sheet_cells': [f'E{index + 11}' for index in range(392)],
              'expected_target_semantics': [{'sheet_cell': f'E{index + 11}', 'field': 'qty.regular',
                                            'menu': 'synthetic menu', 'source': 'unit'} for index in range(392)]}
    sizes = check_linear_sizes(source)
    record_property('receipt_wire_sizes_98_196_392', sizes)


@pytest.mark.skipif(not os.environ.get('HAKODATE_RECEIPT_METADATA_PATH'), reason='optional read-only live metadata')
def test_real_metadata_linear_wire_size(record_property):
    source = json.loads(Path(os.environ['HAKODATE_RECEIPT_METADATA_PATH']).read_text())['source_receipt']
    assert len(source['expected_sheet_cells']) == 392
    assert len(receipts.canonical_json(source).encode()) == 187979
    sizes = check_linear_sizes(source)
    record_property('receipt_wire_sizes_98_196_392', sizes)


@pytest.mark.parametrize('operator', [False, True])
def test_actual_producer_emits_only_references(owned_boundary, repair_order, monkeypatch, operator):
    from test_ocr_operator_evidence_boundary import test_order_service_resumes_operator_checkpoint_without_preprocessing
    from test_h2_operator_repair_revision import test_actual_canonical_producer_json_writer_and_selected_read
    producer = order_service._hakodate_canonical_payload_from_manifest_item
    observed = []
    def check(**kwargs):
        result = producer(**kwargs)
        source = result['hakodate_repair_source_receipt']
        refs = cell_receipts(result)
        assert refs and all(ref == receipts.source_reference(source) for ref in refs)
        assert result['hakodate_canonical_pipeline']['artifact_packet']['source_receipt'] == source
        observed.append(result)
        return result
    monkeypatch.setattr(order_service, '_hakodate_canonical_payload_from_manifest_item', check)
    if operator:
        test_order_service_resumes_operator_checkpoint_without_preprocessing(owned_boundary, monkeypatch, False)
    else:
        test_actual_canonical_producer_json_writer_and_selected_read(repair_order, monkeypatch, None)
    assert len(observed) == 1
