import re
import unittest
from pathlib import Path

import yaml


WORKFLOW = yaml.safe_load((Path(__file__).resolve().parents[3] / 'local-checks/legacy/deploy-stg.yml').read_text())
STEPS = WORKFLOW['jobs']['deploy-frontend']['steps']
OCR = [step for step in STEPS if step.get('id', '').startswith('ocr-ui-')]


def run_gates(*, existing=True, failed=None, cancelled=False, prior_failure=False, **overrides):
    values = {
        'needs.source-gate.result': 'success', 'needs.deploy-backend.result': 'success',
        'steps.auth-frontend.outcome': 'success', 'steps.deploy-web.outcome': 'success',
        'github.event_name': 'workflow_dispatch', 'github.ref': 'refs/heads/develop',
        'inputs.ocr_verification_manifest': '' if existing else 'manifest',
        'inputs.ocr_existing_artifact_run_id': '123' if existing else '',
        **overrides,
    }
    results = {}
    for step in OCR:
        expression = step['if']
        # Evaluate the actual workflow gates, including Actions' implicit success rule.
        explicit_status = 'cancelled()' in expression
        expression = expression.replace('!cancelled()', repr(not cancelled))
        expression = re.sub(r'\b(?:needs|steps|github|inputs)\.[\w.-]+', lambda m: repr(values.get(m[0], 'skipped')), expression)
        expression = expression.replace('&&', ' and ').replace('||', ' or ')
        allowed = eval(expression, {'__builtins__': {}}, {}) and (explicit_status or not prior_failure)
        outcome = ('failure' if step['id'] == failed else 'success') if allowed else 'skipped'
        results[step['id']] = outcome
        values['steps.' + step['id'] + '.outcome'] = outcome
        prior_failure |= outcome == 'failure'
    return results, prior_failure


class IndependentOCRWorkflowTest(unittest.TestCase):
    def test_both_branches_run_after_prior_failure_without_erasing_it(self):
        for existing in [True, False]:
            for prior in [True, False]:
                result, failure = run_gates(existing=existing, prior_failure=prior)
                self.assertEqual(result['ocr-ui-verify'], 'success')
                self.assertEqual(failure, prior)
                self.assertEqual(result['ocr-ui-new-artifact'], 'skipped' if existing else 'success')
                self.assertEqual(result['ocr-ui-existing-artifact'], 'success' if existing else 'skipped')

    def test_failed_prerequisites_and_cancel_do_not_run_verifier(self):
        for existing in [True, False]:
            failures = ['ocr-ui-init', 'ocr-ui-node', 'ocr-ui-deps'] + (
                ['ocr-ui-provenance', 'ocr-ui-existing-artifact'] if existing else ['ocr-ui-new-artifact'])
            for failed in failures:
                result, failure = run_gates(existing=existing, failed=failed)
                self.assertEqual(result['ocr-ui-verify'], 'skipped', failed)
                self.assertTrue(failure)
            self.assertTrue(all(v == 'skipped' for v in run_gates(existing=existing, cancelled=True)[0].values()))
        for key, value in [('needs.source-gate.result', 'failure'), ('steps.auth-frontend.outcome', 'failure'),
                           ('steps.deploy-web.outcome', 'failure'), ('github.ref', 'refs/heads/main'),
                           ('github.event_name', 'push')]:
            self.assertEqual(run_gates(**{key: value})[0]['ocr-ui-verify'], 'skipped')
        self.assertEqual(run_gates(existing=False, **{'needs.deploy-backend.result': 'failure'})[0]['ocr-ui-verify'], 'skipped')
        self.assertEqual(run_gates(**{'inputs.ocr_verification_manifest': 'also-set'})[0]['ocr-ui-verify'], 'skipped')

    def test_existing_artifact_and_readonly_verifier_contracts_retained(self):
        by_id = {step['id']: step for step in OCR}
        provenance = by_id['ocr-ui-provenance']
        self.assertEqual(provenance['run'], 'python3 scripts/verify_existing_ocr_artifact.py "$OCR_EXISTING_RUN"')
        self.assertEqual(by_id['ocr-ui-existing-artifact']['with'], {
            'name': 'live-ocr-stg', 'path': 'tmp/ocr-verification',
            'run-id': '${{ inputs.ocr_existing_artifact_run_id }}', 'github-token': '${{ github.token }}',
            'repository': 'mmorinag127/sawa-hospital-system'})
        self.assertEqual(by_id['ocr-ui-new-artifact']['with'], {'name': 'live-ocr-stg', 'path': 'tmp/ocr-verification'})
        self.assertEqual(by_id['ocr-ui-verify']['run'], 'node frontend/scripts/verify-ocr-live.mjs tmp/ocr-verification tmp/ocr-live')
        self.assertEqual(by_id['ocr-ui-verify']['env']['LIVE_ID_TOKEN'], '${{ steps.auth-frontend.outputs.id_token }}')
        for step in STEPS:
            self.assertNotIn('continue-on-error', step)
        self.assertIn('npm ci --prefix frontend --ignore-scripts', by_id['ocr-ui-deps']['run'])
        self.assertIn('playwright install --with-deps webkit', by_id['ocr-ui-deps']['run'])


if __name__ == '__main__':
    unittest.main()
