#!/usr/bin/env python3
"""Run the complete preserved frontend context, image and vendor proof locally."""
from local_hospital_legacy_step import execute
import subprocess
from local_hospital_test_runner import git
from pathlib import Path

if __name__ == '__main__':
    execute('modernization-image-ci.yml', 'frontend-image',
            ['Build actual frontend image and verify vendored dependency'])
    verifier = Path(__file__).resolve().with_name('verify_tls_trust.mjs')
    subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'node', '-v',
                    str(verifier) + ':/tmp/verify_tls_trust.mjs:ro',
                    'sawa-frontend-proof:' + git('rev-parse', 'HEAD'),
                    '/tmp/verify_tls_trust.mjs'], check=True)
    smoke = Path(__file__).resolve().with_name('verify_frontend_standalone.mjs')
    subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'node', '-v',
                    str(smoke) + ':/tmp/verify_frontend_standalone.mjs:ro',
                    'sawa-frontend-proof:' + git('rev-parse', 'HEAD'),
                    '/tmp/verify_frontend_standalone.mjs'], check=True)
