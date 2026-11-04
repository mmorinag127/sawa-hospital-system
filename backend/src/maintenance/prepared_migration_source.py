"""Build-time migration head, bound to the exact immutable migration sources."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'prepared_migration_source.json'


def fingerprint():
    digest = hashlib.sha256()
    paths = [ROOT / 'alembic.ini', *sorted((ROOT / 'migrations').glob('*.py'))]
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def write(head):
    MANIFEST.write_text(json.dumps({'head': head, 'source_sha256': fingerprint()}))


def read_head():
    if not MANIFEST.exists():
        if os.getenv('SAWA_PREPARED_RUNTIME') == '1':
            raise RuntimeError('prepared migration source is missing')
        return None
    record = json.loads(MANIFEST.read_text())
    if record.get('source_sha256') != fingerprint() or not isinstance(record.get('head'), str) or not record['head']:
        raise RuntimeError('prepared migration source does not match the image')
    return record['head']
