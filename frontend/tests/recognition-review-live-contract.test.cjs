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

test('manual deployment excludes hosted OCR browser verification', () => {
  assert.deepEqual(Object.keys(workflow.on), ['workflow_dispatch']);
  assert.equal(workflow.on.workflow_dispatch.inputs.verify_review_order_id, undefined);
  const steps = Object.values(workflow.jobs).flatMap(job => job.steps || []);
  for (const step of steps) {
    assert.doesNotMatch(step.run || '', /verify-ocr-live|verify-recognition-review-live|playwright install/);
  }
  const local = readFileSync(resolve(__dirname, '../../scripts/run_local_stg_ocr_ui_check.py'), 'utf8');
  assert.ok(local.includes('Validate existing staging OCR artifact provenance'));
  assert.ok(local.includes('Verify current web before and after existing read-only UI checks'));
  assert.ok(local.includes('existing OCR input directory must be reviewed'));
  assert.ok(local.includes("'merge-base', '--is-ancestor'"));
  assert.ok(local.includes("'diff', '--exit-code', '--quiet'"));
});

for (const [name, change, accepted] of [
  ['exact source', {}, true],
  ['missing artifact', { OCR_EXISTING_RUN: '' }, false],
  ['unbound order', { REVIEW_ORDER_ID: 'ORDinvalid' }, false],
  ['query suffix', { REVIEW_ORDER_ID: 'ORD581f55d6?other=1' }, false],
  ['short source', { EXPECTED_SOURCE_SHA: 'a'.repeat(12) }, false],
  ['hosted execution', { GITHUB_ACTIONS: 'true' }, false],
  ['CI execution', { CI: 'true' }, false],
  ['missing token', { LIVE_ID_TOKEN: '' }, false],
  ['production audience', { GOOGLE_OAUTH_CLIENT_ID: '167795504375-hu5316gut0ke8vruc857qsb524q4kq50.apps.googleusercontent.com' }, false],
]) test(`local OCR input guard ${accepted ? 'accepts' : 'rejects'} ${name} before any cloud command`, () => {
  const root = resolve(__dirname, '../..');
  const result = spawnSync('python3', ['-c',
    "import os; from run_local_stg_ocr_ui_check import validate_inputs; validate_inputs(os.environ)"],
    { encoding: 'utf8', env: {
      PATH: process.env.PATH, PYTHONPATH: resolve(root, 'scripts'),
      EXPECTED_SOURCE_SHA: 'a'.repeat(40), OCR_EXISTING_RUN: '39109725288',
      REVIEW_ORDER_ID: 'ORD581f55d6', LIVE_ID_TOKEN: 'synthetic-context-only',
      GOOGLE_OAUTH_CLIENT_ID: '123-staging.apps.googleusercontent.com', ...change,
    } });
  assert.equal(result.status === 0, accepted, result.stderr);
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

async function permitted(method, url, allowedExternalUrls) {
  const {gateReadonlyResource} = await import('../scripts/verify-ocr-live.mjs');
  let result, writes = 0;
  await gateReadonlyResource({request: () => ({method: () => method, url: () => url, resourceType: () => 'image'}),
    continue: () => {result = true;}, abort: () => {result = false;}},
  {origin, allowedExternalUrls, externalGate: Promise.resolve(), onWrite: () => writes++});
  assert.equal(writes, ['GET', 'HEAD'].includes(method) ? 0 : 1);
  return result;
}

for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'CONNECT']) {
  test(`review verifier blocks ${method}, including explicitly whitelisted asset URLs`, async () => {
    const asset = 'https://storage.googleapis.com/owned-stg-artifact.png?signature=synthetic';
    assert.equal(await permitted(method, origin + '/api/orders', new Set()), false);
    assert.equal(await permitted(method, asset, new Set([asset])), false);
  });
}

for (const method of ['GET', 'HEAD']) {
  test(`review verifier allows ${method} only for exact first-party origin or verified exact asset URL`, async () => {
    const asset = 'https://storage.googleapis.com/owned-stg-artifact.png?signature=synthetic';
    assert.equal(await permitted(method, origin + '/api/orders', new Set()), true);
    assert.equal(await permitted(method, asset, new Set([asset])), true);
    for (const url of [origin + '.invalid/api/orders', 'https://web-prod-avlnzjjrca-dt.a.run.app/api/orders',
      asset + '&other=1', 'https://unrelated.invalid/']) {
      assert.equal(await permitted(method, url, new Set([asset])), false);
    }
  });
}
