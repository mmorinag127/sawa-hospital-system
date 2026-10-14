"""Database-free validation using an explicit synthetic initial snapshot."""
import json
import os
from pathlib import Path
import subprocess
import sys


def validate_in_fresh_process(master):
    master = Path(master).resolve(strict=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('DB_', 'DATABASE_', 'PG', 'SQLALCHEMY_'))}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    code = r'''
import copy, json, sys
from pathlib import Path
from src.services import canonical_facility_store as store
master = json.loads(Path(sys.argv[1]).read_text())
assert store._decode(store._encode(master)) == master
assert store.digest({}) == '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'
facility = master['facilities'][0]['versions'][0]['facility']
initial = store.initial_facility_version(facility, 'a' * 64)
assert store.version_start(initial) is None
for fault in ('proof', 'content', 'template'):
    bad = copy.deepcopy(master)
    version = bad['facilities'][0]['versions'][0]
    if fault == 'proof':
        version['initial_snapshot']['configuration_sha256'] = '0' * 64
    elif fault == 'content':
        version['facility']['facility_id'] = 'foreign'
    else:
        version['facility']['fax_template']['columns'] = []
        bad['facilities'][0]['versions'][0] = store.initial_facility_version(version['facility'], 'a' * 64)
    try:
        store._encode(bad)
    except store.CanonicalStoreError:
        pass
    else:
        raise AssertionError('invalid snapshot accepted: ' + fault)
assert not any(name == 'src.db' or name == 'src.models' or name.startswith('src.models.') for name in sys.modules)
print('full initial JSON validation and invalid snapshots checked without DB/models')
'''
    result = subprocess.run([sys.executable, '-c', code, str(master)], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_initial_master_without_database(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from src.services.canonical_facility_store import initial_facility_version

    facility = {
        'facility_id': 'TEST', 'facility_name': 'Synthetic validation fixture', 'areas': None,
        'fax_template': {
            'template_id': 'synthetic', 'units': 'normalized',
            'table_box': [0, 0, 1, 1], 'header_rows': 0,
            'columns': [{'index': 0, 'source_index': 0, 'role': 'quantity',
                         'diet_type': 'regular', 'area_id': 'X'}],
        },
    }
    master = {'facilities': [{'facility_id': 'TEST', 'versions': [
        initial_facility_version(facility, 'a' * 64),
    ]}]}
    path = tmp_path / 'synthetic-initial.json'
    path.write_text(json.dumps(master))
    validate_in_fresh_process(path)
