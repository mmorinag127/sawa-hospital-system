const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { spawnSync } = require('node:child_process');
const yaml = require('js-yaml');
const verifier = import('../scripts/verify-recognition-review-live.mjs');
const origin = 'https://web-stg-avlnzjjrca-dt.a.run.app';
const identity = { orderId: 'ORD581f55d6', expectedOutcome: 'recognition_review_required', review: { count: 10 } };
const workflow = yaml.load(readFileSync(resolve(__dirname, '../../.github/workflows/deploy-stg.yml'), 'utf8'));

test('new opt-in keeps the original verifier and uses the same authenticated, provenance-gated dependency step', () => {
  assert.equal(workflow.on.workflow_dispatch.inputs.verify_review_order_id.default, '');
  const steps = workflow.jobs['deploy-frontend'].steps;
  const oldCheck = steps.find(step => step.id === 'ocr-ui-verify');
  const newCheck = steps.find(step => step.name === 'Inspect reviewed OCR confirmation dialog without submitting');
  assert.equal(oldCheck.run, 'node frontend/scripts/verify-ocr-live.mjs tmp/ocr-verification tmp/ocr-live');
  assert.ok(oldCheck.if.includes("inputs.verify_review_order_id == ''"));
  assert.ok(newCheck.if.includes("steps.ocr-ui-deps.outcome == 'success'"));
  assert.ok(newCheck.if.includes("inputs.verify_review_order_id != ''"));
  assert.equal(newCheck.env.LIVE_ID_TOKEN, oldCheck.env.LIVE_ID_TOKEN);
  assert.equal(newCheck.env.REVIEW_ORDER_ID, '${{ inputs.verify_review_order_id }}');
});

for (const [name, change, accepted] of [
  ['exact source', {}, true],
  ['missing artifact', { OCR_EXISTING_RUN: '' }, false],
  ['unbound order', { REVIEW_ORDER: 'ORDinvalid' }, false],
  ['query suffix', { REVIEW_ORDER: 'ORD581f55d6?other=1' }, false],
  ['different repository', { GITHUB_REPOSITORY: 'other/repo' }, false],
  ['release branch', { GITHUB_REF: 'refs/heads/release/prod-test' }, false],
  ['push event', { GITHUB_EVENT_NAME: 'push' }, false],
  ['recovery mode', { DEPLOY_MODE: 'recover-web-image' }, false],
  ['ambiguous source', { OCR_MANIFEST: 'gs://unexpected/manifest.json' }, false],
]) test(`workflow source guard ${accepted ? 'accepts' : 'rejects'} ${name} without any cloud command`, () => {
  const guard = workflow.jobs['source-gate'].steps.find(step => step.name === 'Validate deployment mode and recovery inputs').run;
  const result = spawnSync('bash', ['-c', guard], { encoding: 'utf8', env: {
    PATH: process.env.PATH, GITHUB_REPOSITORY: 'mmorinag127/sawa-hospital-system',
    GITHUB_REF: 'refs/heads/develop', GITHUB_EVENT_NAME: 'workflow_dispatch', DEPLOY_MODE: 'normal',
    RECOVERY_CONFIRMATION: '', EXPECTED_REVISION: '', EXPECTED_IMAGE: '', OCR_MANIFEST: '',
    OCR_EXISTING_RUN: '39109725288', MENU_MANIFEST: '', REVIEW_ORDER: 'ORD581f55d6', VERIFY_MENU: 'false',
    OUTPUT_ORDER: '', OUTPUT_DATE: '', ...change,
  } });
  assert.equal(result.status === 0, accepted);
});

test('all five known review reasons have distinct descriptions; unknown codes cannot pass live proof', async () => {
  const { expectedReviewReason } = await verifier;
  const codes = ['hakodate_blank_input_nonempty_prediction', 'hakodate_ocr_quantity_non_numeric',
    'hakodate_preprocess_ink_loss', 'hakodate_ocr_quantity_low_confidence', 'hakodate_ocr_quantity_candidate_conflict'];
  assert.equal(new Set(codes.map(expectedReviewReason)).size, 5);
  assert.equal(expectedReviewReason(codes[1]), '数量として解釈できない文字・記号です');
  assert.throws(() => expectedReviewReason('unknown'));
});

test('review verifier selects only the unique explicitly requested artifact identity', async () => {
  const { requestedIdentity } = await verifier;
  assert.equal(requestedIdentity([{ ...identity, orderId: 'ORDffffffff' }, identity], identity.orderId), identity);
  for (const entries of [[], [identity, identity], [{ ...identity, expectedOutcome: 'done' }],
    [{ ...identity, review: { count: 0 } }]]) assert.throws(() => requestedIdentity(entries, identity.orderId));
  for (const value of ['', undefined, 'ORD', '../ORD581f55d6', 'ORD581f55d6?x=1']) {
    assert.throws(() => requestedIdentity([identity], value));
  }
});

for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'CONNECT']) {
  test(`review verifier blocks ${method}, including explicitly whitelisted asset URLs`, async () => {
    const { readonlyRequestAllowed } = await verifier;
    const asset = 'https://storage.googleapis.com/owned-stg-artifact.png?signature=synthetic';
    assert.equal(readonlyRequestAllowed(method, origin + '/api/orders', new Set()), false);
    assert.equal(readonlyRequestAllowed(method, asset, new Set([asset])), false);
  });
}

for (const method of ['GET', 'HEAD']) {
  test(`review verifier allows ${method} only for exact first-party origin or verified exact asset URL`, async () => {
    const { readonlyRequestAllowed } = await verifier;
    const asset = 'https://storage.googleapis.com/owned-stg-artifact.png?signature=synthetic';
    assert.equal(readonlyRequestAllowed(method, origin + '/api/orders', new Set()), true);
    assert.equal(readonlyRequestAllowed(method, asset, new Set([asset])), true);
    for (const url of [origin + '.invalid/api/orders', 'https://web-prod-avlnzjjrca-dt.a.run.app/api/orders',
      asset + '&other=1', 'https://unrelated.invalid/']) {
      assert.equal(readonlyRequestAllowed(method, url, new Set([asset])), false);
    }
  });
}
