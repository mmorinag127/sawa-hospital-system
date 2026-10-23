#!/usr/bin/env python3
"""Run both preserved read-only verifiers with before/after serving SHA checks."""
import os
import re
from pathlib import Path
import subprocess
from local_hospital_legacy_step import execute


def main():
    if os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI'):
        raise SystemExit('OCR UI checks require local execution')
    for key in ('EXPECTED_SOURCE_SHA', 'OCR_EXISTING_RUN', 'REVIEW_ORDER_ID', 'LIVE_ID_TOKEN',
                'GOOGLE_OAUTH_CLIENT_ID'):
        if not os.getenv(key):
            raise SystemExit(key + ' required for local read-only staging UI verification')
    expected, run_id, review = (os.environ[key] for key in ('EXPECTED_SOURCE_SHA', 'OCR_EXISTING_RUN', 'REVIEW_ORDER_ID'))
    if not re.fullmatch('[0-9a-f]{40}', expected) or not re.fullmatch('[0-9]+', run_id) or not re.fullmatch('ORD[0-9a-f]{8}', review):
        raise ValueError('exact source, artifact run and review order are required')
    audience = os.environ['GOOGLE_OAUTH_CLIENT_ID']
    if not re.fullmatch('[0-9]+-[a-z0-9]+\\.apps\\.googleusercontent\\.com', audience) or audience == '167795504375-hu5316gut0ke8vruc857qsb524q4kq50.apps.googleusercontent.com':
        raise ValueError('explicit staging audience required')
    root = Path(__file__).resolve().parents[1]
    def git(*argv):
        return subprocess.check_output(['git', *argv], cwd=root, text=True).strip()
    if git('rev-parse', expected + '^{commit}') != expected:
        raise ValueError('exact deployed commit required')
    git('merge-base', '--is-ancestor', expected, 'HEAD')
    git('diff', '--exit-code', '--quiet', expected, 'HEAD', '--', '.',
        ':(top,exclude,literal).github/workflows/verify-stg-ocr-ui.yml',
        ':(top,exclude,literal)infra/terraform/tests/test_stg_ocr_ui_readonly.py',
        ':(top,exclude,literal)docs/workflow-demand-read-20261020.md')
    artifact = root / 'tmp/ocr-verification'
    if artifact.exists():
        raise ValueError('existing OCR input directory must be reviewed before another download')
    execute('verify-stg-ocr-ui.yml', 'verify',
            ['Validate existing staging OCR artifact provenance'])
    subprocess.run(['gh', 'run', 'download', run_id, '--repo', 'mmorinag127/sawa-hospital-system',
                    '--name', 'live-ocr-stg', '--dir', str(artifact)], cwd=root, check=True)
    execute('verify-stg-ocr-ui.yml', 'verify',
            ['Verify current web before and after existing read-only UI checks'])


if __name__ == '__main__':
    main()
