"""Sign local test records; this key cannot authenticate to GitHub or GCP."""
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sign(payload):
    key = Path(os.environ.get('SAWA_LOCAL_TEST_SIGNING_KEY',
                              str(Path.home()/'.codex/local-test-signing/key.pem')))
    info = key.stat()
    if not stat.S_ISREG(info.st_mode) or key.is_symlink() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('A private owner-only local test signing key is required')
    signed = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(key)],
                            input=canonical(payload), capture_output=True, check=True)
    return {'payload': payload, 'signature': base64.b64encode(signed.stdout).decode()}


def verify_signed(envelope, public_key=None):
    public_key = public_key or os.environ.get('SAWA_LOCAL_TEST_PUBLIC_KEY', '')
    if not public_key or set(envelope) != {'payload', 'signature'}:
        raise ValueError('Signed local test receipt and configured public key are required')
    with tempfile.TemporaryDirectory(prefix='sawa-receipt-') as directory:
        root = Path(directory)
        key, data, signature = (root/name for name in ('public.pem', 'payload.json', 'signature'))
        key.write_text(public_key)
        data.write_bytes(canonical(envelope['payload']))
        signature.write_bytes(base64.b64decode(envelope['signature'], validate=True))
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(key),
                                 '-signature', str(signature), str(data)], capture_output=True)
        if result.returncode:
            raise ValueError('Local test receipt signature is invalid')
    return envelope['payload']
