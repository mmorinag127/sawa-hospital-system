import assert from 'node:assert/strict';
import test from 'node:test';
import { STG_OUTPUT_SOURCE_ORIGIN, isAllowedOutputSourceOrigin } from '../../scripts/output-source-live-origin.mjs';

test('output-source browser verifier only accepts the exact staging origin', () => {
  assert.equal(isAllowedOutputSourceOrigin(STG_OUTPUT_SOURCE_ORIGIN), true);
  for (const origin of ['', 'https://web-stg-avlnzjjrca-dt.a.run.app/', 'https://web.example.invalid', 'https://web-prod-avlnzjjrca-dt.a.run.app', 'http://localhost:3000']) {
    assert.equal(isAllowedOutputSourceOrigin(origin), false);
  }
});
