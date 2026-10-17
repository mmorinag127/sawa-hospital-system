"""Pure snapshot parity and actual artifact validation without DB configuration."""

import ast
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
BASE = 'cbcdec9e1b2def40958297de9297a18556ffe103'
NAMES = ('_json_snapshot', 'canonical_snapshot_json', 'snapshots_equal')
ORIGINAL_AST_SHA256 = {
    '_json_snapshot': '85f7f543c11bb7386ead7e112c840db0ad1ab567150ca0d30826f8e28db99041',
    'canonical_snapshot_json': 'e7348a3cad1aaa7a59cb5a9134023ea818132f9aef8ff26f6ad5befce6c24127',
    'snapshots_equal': '936a88028e5e110fa1447d82528d5f219457cbf85ac25c339d0c6eb65c5821cb',
}


def test_snapshot_functions_match_pinned_original_outputs_hashes_and_errors():
    from src.services import snapshot_serialization as pure

    tree = ast.parse((ROOT / 'backend/src/services/snapshot_serialization.py').read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
    assert len(functions) == 3
    assert {node.name: hashlib.sha256(ast.dump(node).encode()).hexdigest()
            for node in functions} == ORIGINAL_AST_SHA256
    samples = [(None, 'null'), (False, 'false'), (True, 'true'), (0, '0'), (1, '1'),
               ('', '""'), ('日本語', '"日本語"'),
               ([False, 0, True, 1], '[false, 0, true, 1]'),
               ({'z': [None, {'b': 2, 'a': '日'}], 'a': ()}, '{"a": [], "z": [null, {"a": "日", "b": 2}]}'),
               (date(2026, 10, 17), '"2026-10-17"'),
               (datetime(2026, 10, 17, 12, 34, 56, 789), '"2026-10-17T12:34:56.000789"'),
               (datetime(2026, 10, 17, 12, 34, tzinfo=timezone.utc), '"2026-10-17T12:34:00+00:00"'),
               ({1: 'integer key'}, '{"1": "integer key"}'),
               ({'1': 'string key'}, '{"1": "string key"}')]
    for sample, expected in samples:
        assert pure._json_snapshot(sample) == json.loads(expected)
        actual = pure.canonical_snapshot_json(sample)
        assert actual == expected
        assert actual.encode() == expected.encode()
        assert hashlib.sha256(actual.encode()).digest() == hashlib.sha256(expected.encode()).digest()
    for left, left_json in samples:
        for right, right_json in samples:
            assert pure.snapshots_equal(left, right) is (left_json == right_json)
    assert pure.snapshots_equal({'b': 2, 'a': 1}, {'a': 1, 'b': 2})
    assert not pure.snapshots_equal(True, 1)
    for value, error_type, message in (
            (object(), TypeError, 'execution_snapshot_non_json_value'),
            ({1, 2}, TypeError, 'execution_snapshot_non_json_value'),
            (b'bytes', TypeError, 'execution_snapshot_non_json_value'),
            (float('nan'), ValueError, 'Out of range float values are not JSON compliant'),
            (float('inf'), ValueError, 'Out of range float values are not JSON compliant'),
            (float('-inf'), ValueError, 'Out of range float values are not JSON compliant'),
            ({'nested': [float('nan')]}, ValueError, 'Out of range float values are not JSON compliant')):
        for name in NAMES[:2]:
            try:
                getattr(pure, name)(value)
            except (TypeError, ValueError) as error:
                assert type(error) is error_type
                assert str(error) == message
            else:
                raise AssertionError('invalid snapshot accepted')


def test_guard_reexports_identical_pure_functions():
    from src.services import snapshot_serialization as pure, workflow_attempt_guard_service as guard

    for name in NAMES:
        assert getattr(guard, name) is getattr(pure, name)


def test_master_lazy_menu_attribute_preserves_module_identity_and_rejects_unknown_names():
    from src.services import master_order_form_template_service as master, menu_service

    assert master.menu_service is menu_service
    assert master.menu_service is master.__getattr__('menu_service')
    try:
        master.__getattr__('unknown_menu_service')
    except AttributeError as error:
        assert 'unknown_menu_service' in str(error)
    else:
        raise AssertionError('unknown module attribute accepted')


def test_new_process_validates_canonical_artifact_without_db_imports(tmp_path):
    # An allowlist, rather than inherited DB variables, also excludes future DB selectors.
    for inherit_pythonpath in (True, False):
        child_root = tmp_path / ('inherited' if inherit_pythonpath else 'no-pythonpath')
        child_root.mkdir()
        environment = {key: os.environ[key] for key in ('PATH', 'SNAPSHOT_PROBE_DEPENDENCY_PATH') if key in os.environ}
        if inherit_pythonpath and 'PYTHONPATH' in os.environ:
            environment['PYTHONPATH'] = os.environ['PYTHONPATH']
        environment.update(HOME=str(child_root), TMPDIR=str(child_root), PYTHONDONTWRITEBYTECODE='1',
                           OCR_ARTIFACT_DIR=str(child_root / 'artifacts'), HF_HUB_OFFLINE='1',
                           TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1')
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(child_root)],
                                   env=environment, cwd=ROOT, text=True, capture_output=True)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert json.loads(completed.stdout)['forbidden_imports'] == []


def _probe(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path[1:1] = [path for path in os.environ.get('SNAPSHOT_PROBE_DEPENDENCY_PATH', '').split(os.pathsep) if path]
    import importlib.abc

    forbidden = ('src.db', 'src.models', 'sqlalchemy')

    class RejectDatabaseImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
                raise AssertionError('DB-free validation imported ' + fullname)

    sys.meta_path.insert(0, RejectDatabaseImports())
    assert not any(name in os.environ for name in (
        'DATABASE_URL', 'DB_URI', 'SAWA_SQLITE_DB_PATH', 'ALEMBIC_DB_URI',
        'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'DB_HOST', 'DB_PORT', 'DB_DRIVER'))
    from src.services.snapshot_serialization import canonical_snapshot_json
    from src.services import hakodate_preprocess_observation as observation
    try:
        observation.validate_artifact(b'{}', [], b'', {})
    except ValueError as error:
        assert str(error) == 'hakodate_observation_artifact_invalid'
    else:
        raise AssertionError('invalid manifest accepted')
    from src.services import hakodate_ocr_checkpoint_service as checkpoints
    from src.hakodate_best_method_runtime.run_text_recognizer_corner_noise_trial import build_recognizer_contact_sheet
    import numpy as np

    pdf = root / 'source.pdf'
    pdf.write_bytes(b'%PDF-synthetic-checkpoint-receipt')
    receipt = {'storage_uri': str(pdf), 'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest()}
    item = {'order_id': 'PURE-ORDER', 'facility_code': 'PURE-FACILITY', 'week_code': '2026-W42',
            'selected_pdf_receipt': receipt, 'configured_input': {'fixture': 'pure'},
            'original_physical_draft': {'physical_menu_row_ids': ['row-1']},
            'original_target_semantics': [{'sheet_cell': 'E11', 'physical_menu_row_id': 'row-1'}],
            'original_expected_sheet_cells': ['E11'], 'selected_page': 1,
            'quad_px': [[0, 0], [239, 0], [239, 239], [0, 239]], 'quad_source': 'authored',
            'quad_override': None, 'header_axis_override': None, 'row_axis_override': None}
    regions = [{'sheet_cell': 'E11', 'role': 'quantity', 'field': 'qty.regular',
                'bbox': [80., 80., 160., 140.],
                'polygon': [[80., 80.], [160., 80.], [160., 140.], [80., 140.]]}]
    raw = np.full((240, 240, 3), 255, dtype=np.uint8)
    checkpoint = checkpoints.persist_recognizer_checkpoint(item=item, raw_rectified_bgr=raw,
        regions=regions, expected_sheet_cells=['E11'], metadata={
            'axis_evidence': {}, 'rectified_quad_points': item['quad_px'], 'snap_debug': {}})
    image, records, _, skipped = build_recognizer_contact_sheet(
        rectified_fax_bgr=raw, regions=regions, line_mask=None, mode='corner_cc')
    assert not skipped
    for record in records:
        record['recognizer_input_evidence'] = dict(record['recognizer_preprocess_observation']['contact'])
    from io import BytesIO
    contact = BytesIO()
    image.save(contact, format='PNG')
    manifest = {'contract': observation.CONTRACT, 'checkpoint': checkpoint,
                'observations': [row['recognizer_preprocess_observation'] for row in records]}
    source = {'selected_pdf_receipt': receipt, 'expected_sheet_cells': ['E11'],
              'configured_input_digest': hashlib.sha256(canonical_snapshot_json(item['configured_input']).encode()).hexdigest()}
    observation.validate_artifact(json.dumps(manifest).encode(), records, contact.getvalue(), source)
    changed = dict(source, configured_input_digest='0' * 64)
    try:
        observation.validate_artifact(json.dumps(manifest).encode(), records, contact.getvalue(), changed)
    except ValueError as error:
        assert str(error) == 'hakodate_observation_source_changed'
    else:
        raise AssertionError('changed source accepted')
    imported = [name for name in sys.modules if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)]
    assert imported == []
    print(json.dumps({'forbidden_imports': imported, 'artifact_validation': 'passed', 'source_guard': 'rejected'}))


if __name__ == '__main__':
    _probe(Path(sys.argv[1]))
