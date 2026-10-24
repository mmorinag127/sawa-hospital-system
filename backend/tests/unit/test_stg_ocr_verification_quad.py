import importlib.util
import ast
from datetime import datetime
import json
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/verify_stg_ocr_upload.py"
spec = importlib.util.spec_from_file_location("verify_stg_ocr_upload", SCRIPT)
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


def case():
    return {
        "identity_verified": True,
        "facility_id": "FAC00015", "week_id": "2026-10@2026-10-25~2026-10-31",
        "effective_date": "2026-10-25", "sha256": "a" * 64,
        "approved_quad": {
            "coordinate_space": {"mode": "render_width", "width": 1864},
            "decision": "approved_estimate",
            "quad_px": [[1, 2], [3, 4], [5, 6], [7, 8]],
        },
    }


def test_verified_quad_uses_operator_save_endpoint(monkeypatch):
    value = case()
    receipt = {"selected_pdf_receipt": {"order_id": "ORDtest", "document_id": "doc", "sha256": value['sha256']},
               "order_input_identity": {"facility_code": value['facility_id'], "week_code": value['week_id']},
               "workflow_revision": 7, "geometry_source_digest": "geometry"}
    review = {"order_id": "ORDtest", "source_receipt": receipt,
              "coordinate_space": value['approved_quad']['coordinate_space'], "suggested_quad_px": value['approved_quad']['quad_px']}
    request = Mock(side_effect=[review, {"order_id": "ORDtest", "revision": 7}, {"saved": True}])
    monkeypatch.setattr(verification, "request", request)
    assert verification.apply_verified_quad(value, "ORDtest", "doc") == {"saved": True}
    assert request.call_args.args[0] == "/orders/ORDtest/workflow-v2/quad-review"
    assert request.call_args.kwargs == {"method": "PUT", "revision": 7}
    assert request.call_args.args[2] == "application/json"
    assert json.loads(request.call_args.args[1]) == {
        "decision": "approved_estimate",
        "quad_px": [[1, 2], [3, 4], [5, 6], [7, 8]],
        "source_receipt": receipt,
    }


@pytest.mark.parametrize("quad", [None, {}, {"quad_px": []}])
def test_invalid_quad_does_not_send_request(monkeypatch, quad):
    request = Mock()
    monkeypatch.setattr(verification, "request", request)
    value = case()
    value["approved_quad"] = quad
    with pytest.raises(ValueError):
        verification.apply_verified_quad(value, "ORDtest")
    request.assert_not_called()


@pytest.mark.parametrize("invalid", [float("nan"), "1", True])
def test_invalid_coordinate_rejected(invalid):
    value = case()
    value["approved_quad"]["quad_px"][0][0] = invalid
    with pytest.raises(ValueError):
        verification.apply_verified_quad(value, "ORDtest")


def test_unverified_identity_rejected():
    value = case()
    value["identity_verified"] = False
    with pytest.raises(ValueError):
        verification.apply_verified_quad(value, "ORDtest")


def test_no_operator_quad_is_unchanged():
    assert verification.apply_verified_quad({}, "ORDtest") is None


@pytest.mark.parametrize('fault', [None, 'facility_id', 'effective_date', 'source', 'revision', 'version'])
def test_canonical_selection(monkeypatch, fault):
    value = case()
    resolved = {'facility_id': value['facility_id'], 'effective_date': value['effective_date'],
                'source': 'canonical_json', 'revision': 'recorded-revision',
                'version': {'version_id': 'initial-recorded', 'valid_from': None, 'valid_to': None}}
    if fault:
        resolved[fault] = None
    mock = Mock(return_value=resolved)
    monkeypatch.setattr(verification, 'request', mock)
    if fault:
        with pytest.raises(ValueError):
            verification.resolve_selection(value)
    else:
        assert verification.resolve_selection(value) == resolved
        assert 'effective_date=2026-10-25' in mock.call_args.args[0]


def test_date_outside_week_never_requests(monkeypatch):
    mock = Mock()
    monkeypatch.setattr(verification, 'request', mock)
    value = case(); value['effective_date'] = '2026-11-01'
    with pytest.raises(ValueError):
        verification.resolve_selection(value)
    mock.assert_not_called()


@pytest.mark.parametrize('fault', [None, 'facility', 'week_value', 'document_id', 'id', 'current_version'])
def test_registered_owner(monkeypatch, fault):
    value = case()
    state = {'current_order_id': 'ord', 'current_document_id': 'doc', 'content_sha256': value['sha256']}
    order = {'id': 'ord', 'document_id': 'doc', 'current_version': {'document_id': 'doc'},
             'facility': value['facility_id'], 'week_value': value['week_id']}
    if fault:
        order[fault] = None
        with pytest.raises(ValueError):
            verification.verify_owner(value, state, order)
    else:
        assert verification.verify_owner(value, state, order) == 'doc'


@pytest.mark.parametrize('fault', [None, 'old', 'attempt', 'document', 'job'])
def test_only_own_new_evidence_completes(fault):
    job = {'order_id': 'ord', 'order_document_id': 'doc', 'ocr_job_id': 'job', 'workflow_attempt_id': 'attempt', 'status': 'done'}
    workflow = {'order_id': 'ord', 'ocr_job': job}
    execution = {'job_id': 'job', 'workflow_attempt_id': 'attempt', 'evidence_run_id': 'new',
                 'selected_pdf_receipt': {'order_id': 'ord', 'document_id': 'doc'}}
    evidence = {'id': 'new', 'status': 'done', 'producer_version': 'hakodate_best_method_pipeline', 'payload_json': {'_ocr_execution': execution}}
    if fault == 'attempt': execution['workflow_attempt_id'] = 'old'
    if fault == 'document': execution['selected_pdf_receipt']['document_id'] = 'other'
    if fault == 'job': execution['job_id'] = 'other'
    assert verification.completed_evidence(evidence, workflow, workflow, 'ord', 'doc', 'new' if fault == 'old' else 'previous') == (fault is None)


def source_function(module, name, namespace):
    # Execute the production serializer body without importing its DB services.
    path = SCRIPT.parent.parent / 'backend/src/services' / (module + '.py')
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    unit = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(unit), str(path), 'exec'), namespace)
    return namespace[name], function


def row_for(function, argument, **values):
    fields = {node.attr: None for node in ast.walk(function) if isinstance(node, ast.Attribute)
              and isinstance(node.value, ast.Name) and node.value.id == argument}
    fields.update(values)
    return SimpleNamespace(**fields)


def test_actual_serializer_contracts(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent.parent / 'backend'))
    from src.services import sheet_week_service
    value = case()
    upload_serializer, upload_ast = source_function('uploaded_pdf_service', '_serialize_uploaded_pdf', {})
    state = upload_serializer(row_for(upload_ast, 'row', current_order_id='ord', current_document_id='doc', content_sha256=value['sha256']))
    version_serializer, version_ast = source_function('order_serialization_service', '_serialize_order_version', {'selected_version_snapshot': lambda row: {}})
    version = version_serializer(row_for(version_ast, 'version', document_id='doc', is_current=True))
    job_fields, _ = source_function('ocr_job_service', 'recorded_order_job_fields', {})
    recorded_fields, _ = source_function('order_serialization_service', '_recorded_job_fields', {
        'object_session': lambda row: None,
        'get_recorded_order_job_bindings': lambda ids, **kwargs: {},
        'recorded_order_job_fields': job_fields,
    })
    serializer, order_ast = source_function('order_serialization_service', 'serialize_order', {
        'sheet_week_service': sheet_week_service, '_build_menu_amount_meta': lambda row: {},
        '_list_order_versions_for_serialization': lambda row: [version],
        '_recorded_job_fields': recorded_fields,
    })
    order = serializer(row_for(order_ast, 'order', id='ord', current_document_id='doc', facility_code=value['facility_id'], week_code=value['week_id']))
    assert order['ocr_job_id'] is None and order['ocr_job_binding_error'] is None
    assert verification.verify_owner(value, state, order) == 'doc'

    serializer, job_ast = source_function('order_workflow_v2_service', '_serialize_ocr_job', {
        '_coerce_float': lambda value: None, '_coerce_datetime': lambda value: None,
        '_serialize_datetime': lambda value: None, '_normalize_id': lambda value: str(value or ''),
        '_ocr_progress_payload': lambda job, metrics: {}, 'datetime': datetime,
    })
    job = serializer(row_for(job_ast, 'job', id='job', order_id='ord', order_document_id='doc', status='done', metrics={'workflow_attempt_id': 'attempt'}))
    path = SCRIPT.parent.parent / 'backend/src/services/ocr_evidence_service.py'
    tree = ast.parse(path.read_text())
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                              and target.slice.value == '_ocr_execution' for target in node.targets)
                      and isinstance(node.value, ast.Dict))
    scope = {'workflow_job_id': 'job', 'workflow_attempt_id': 'attempt', 'expected_workflow_revision': 7,
             'evidence_id': 'new', 'result_disposition': None, 'execution_input': None,
             'processing_start_read_input': lambda: {},
             'processing_start_pdf_receipt': lambda: {'order_id': 'ord', 'document_id': 'doc'}}
    execution = eval(compile(ast.Expression(assignment.value), str(path), 'eval'), scope)
    evidence = {'id': 'new', 'status': 'done', 'producer_version': 'hakodate_best_method_pipeline', 'payload_json': {'_ocr_execution': execution}}
    workflow = {'order_id': 'ord', 'ocr_job': job}
    assert verification.completed_evidence(evidence, workflow, workflow, 'ord', 'doc', 'previous')
    job['order_document_id'] = 'foreign'
    assert not verification.completed_evidence(evidence, workflow, workflow, 'ord', 'doc', 'previous')


def test_quad_response_coordinate_contract_and_no_approval(monkeypatch):
    path = SCRIPT.parent.parent / 'backend/src/services/order_workflow_v2_service.py'
    tree = ast.parse(path.read_text())
    response = next(node for node in ast.walk(tree) if isinstance(node, ast.Dict)
                    and any(isinstance(key, ast.Constant) and key.value == 'tolerance_policy' for key in node.keys))
    coordinate = next(value for key, value in zip(response.keys, response.values) if isinstance(key, ast.Constant) and key.value == 'coordinate_space')
    assert eval(compile(ast.Expression(coordinate), str(path), 'eval'), {'render_width': 1864}) == case()['approved_quad']['coordinate_space']
    value = case()
    del value['approved_quad']
    request = Mock(side_effect=AssertionError('automatic detection must not approve quad'))
    monkeypatch.setattr(verification, 'request', request)
    assert verification.apply_verified_quad(value, 'ord', 'doc') is None
    request.assert_not_called()


def test_request_preserves_if_match_and_staging_guard(monkeypatch):
    import io
    monkeypatch.setenv('VERIFICATION_TOKEN', 'test-token')
    opened = Mock(return_value=io.StringIO('{}'))
    monkeypatch.setattr(verification.urllib.request, 'urlopen', opened)
    assert verification.request('/orders/ord/workflow-v2/ocr-runs', b'{}', 'application/json', revision=7) == {}
    request = opened.call_args.args[0]
    assert request.get_header('If-match') == '"7"'
    assert request.get_method() == 'POST'
    monkeypatch.setattr(verification, 'BASE', 'https://production.invalid/api')
    with pytest.raises(ValueError, match='staging'):
        verification.request('/orders/ord')
    assert opened.call_count == 1


def test_main_automatic_detection_saves_new_execution(monkeypatch, tmp_path):
    import hashlib
    import sys
    value = case()
    del value['approved_quad']
    raw = b'synthetic transport PDF bytes; no OCR accuracy claim'
    value.update(source_order_id='synthetic', pdf_uri=verification.BUCKET + 'synthetic.pdf',
                 sha256=hashlib.sha256(raw).hexdigest(), expected_menu_rows=2)
    state = {'current_order_id': 'ord', 'current_document_id': 'doc', 'content_sha256': value['sha256'], 'status': 'completed'}
    order = {'id': 'ord', 'document_id': 'doc', 'current_version': {'document_id': 'doc'}, 'facility': value['facility_id'], 'week_value': value['week_id']}
    job = {'order_id': 'ord', 'order_document_id': 'doc', 'ocr_job_id': 'job', 'workflow_attempt_id': 'attempt', 'status': 'done'}
    workflow = {'order_id': 'ord', 'revision': 7, 'ocr_job': job}
    payload = {'_ocr_execution': {'job_id': 'job', 'workflow_attempt_id': 'attempt', 'evidence_run_id': 'new',
                                 'selected_pdf_receipt': {'order_id': 'ord', 'document_id': 'doc'}},
               'hakodate_overlay': {'uri': verification.BUCKET + 'overlay.png', 'sheet_review_base_uri': verification.BUCKET + 'corrected.png'},
               'hakodate_canonical_pipeline': {'metrics': {'row_axis_source': 'fax_intersections', 'row_axis_draft_body_row_count': 2, 'row_axis': []}}}
    evidence = {'id': 'new', 'order_id': 'ord', 'status': 'done', 'producer_version': 'hakodate_best_method_pipeline', 'payload_json': payload}
    started = False

    def request(path, body=None, content_type=None, **kwargs):
        nonlocal started
        if path.startswith('/facilities/'):
            return {'facility_id': value['facility_id'], 'effective_date': value['effective_date'], 'source': 'canonical_json', 'revision': 'canonical-revision', 'version': {'version_id': 'version'}}
        if path == '/ingest/upload':
            for field, expected in [('effective_date', value['effective_date']), ('expected_canonical_revision', 'canonical-revision'), ('selected_version_id', 'version')]:
                assert (f'name="{field}"\r\n\r\n{expected}\r\n').encode() in body
            assert b'skip_ocr' not in body
            return {'uploaded_pdf_id': 'upload'}
        if path == '/ingest/uploads/upload': return state
        if path == '/orders/ord': return order
        if path == '/orders/ord/workflow-v2': return workflow
        if path == '/orders/ord/evidence': return evidence if started else {'id': 'previous'}
        if path == '/orders/ord/workflow-v2/ocr-results':
            return {'results': [{'ocr_result_id': evidence['id'], 'execution_lineage': payload['_ocr_execution']}] if started else []}
        if path == '/orders/ord/workflow-v2/ocr-results/new': return evidence
        if path == '/orders/ord/workflow-v2/ocr-runs':
            assert kwargs == {'revision': 7}
            assert json.loads(body) == {'mode': 'hakodate', 'document_id': 'doc', 'stale_action': 'retry', 'force': True}
            started = True
            return {'workflow': workflow}
        raise AssertionError('Unexpected API request: ' + path)

    def download(uri, path):
        assert uri.startswith(verification.BUCKET)
        if uri.endswith('manifest.json'): path.write_text(json.dumps({'cases': [value]}))
        elif uri.endswith('.pdf'): path.write_bytes(raw)
        else: path.write_bytes(b'synthetic image transport bytes')

    monkeypatch.setattr(verification, 'request', request)
    monkeypatch.setattr(verification, 'download', download)
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', verification.BUCKET + 'manifest.json', '--output-dir', str(tmp_path)])
    verification.main()
    folder = tmp_path / 'synthetic'
    assert json.loads((folder / 'ocr-payload.json').read_text()) == payload
    assert json.loads((folder / 'workflow.json').read_text()) == workflow
    assert json.loads((folder / 'upload-state.json').read_text()) == state
    assert (folder / 'overlay.png').is_file() and (folder / 'corrected.png').is_file()
