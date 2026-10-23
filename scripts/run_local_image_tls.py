#!/usr/bin/env python3
"""Check each explicitly supplied, locally built same-source runtime image once."""
import os
from pathlib import Path
import subprocess
from local_hospital_test_runner import git, reject_hosted_runner

ROOT = Path(__file__).resolve().parents[1]


def main():
    reject_hosted_runner()
    source = git('rev-parse', 'HEAD')
    seen = set()
    for component in ('BACKEND', 'OCR_PIPELINE', 'AGENT_RUNTIME'):
        key = 'SAWA_LOCAL_TEST_IMAGE_' + component
        image = os.environ.get(key)
        if not image:
            raise ValueError(key + ' must identify an already locally built test image')
        identity = subprocess.check_output(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip()
        label = subprocess.check_output(['docker', 'image', 'inspect', image, '--format',
                                         '{{index .Config.Labels "sawa.git_sha"}}'], text=True).strip()
        if label != source:
            raise ValueError('local image source SHA differs from checkout')
        if identity in seen:
            continue
        seen.add(identity)
        subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'python',
                        '-v', str(ROOT / 'scripts/verify_tls_trust.py') + ':/tmp/verify_tls_trust.py:ro',
                        image, '/tmp/verify_tls_trust.py', '--online'], check=True)


if __name__ == '__main__':
    main()
