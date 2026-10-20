import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {waitForWorkflowReadiness} from '../../scripts/verify-ocr-live.mjs';
const require = createRequire(import.meta.url);
const {mount, transport, deferred} = require('../helpers/modernization-react-dom.cjs');
const candidates = require('../helpers/ocr-candidates.cjs');

// Mount the production page with inspection already received but its paired order delayed.
async function setup() {
  const t = transport(), pendingOrder = deferred();
  const workflow = {order_id: 'race', revision: 10, state: 'ocr_failed', headline: 'synthetic loaded workflow',
    selected_ocr_result_id: null, blockers: [], warnings: []};
  const inspection = {order_id: 'race', workflow,
    ocr_candidates: candidates('race', [{ocr_result_id: 'E', selected: false, detail_state: 'not_loaded'}], workflow)};
  const order = {id: 'race', current_version: {document_id: 'pdf'}, versions: [{document_id: 'pdf', is_current: true}]};
  t.on('get', '/auth/me', () => ({data: {role: 'operator'}}));
  t.on('get', '/facilities', () => ({data: {source: 'canonical_json', revision: 'fixture', facilities: []}}));
  t.on('get', '/orders/race/workflow-v2/inspection?include_ocr_results=true&history_details=false', () => ({data: inspection}));
  t.on('get', '/orders/race', () => pendingOrder.promise);
  t.on('get', '/orders/race/week-options', () => ({data: {options: []}}));
  t.on('get', '/orders/race/document', () => ({data: new Blob(['synthetic'])}));
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', {
    api: t.api, route: '/orders/race/workflow-v2', routerState: {isReady: true, query: {id: 'race'}},
  });
  const button = () => [...h.document.querySelectorAll('.step-nav button')].find(node => node.textContent.includes('OCR選択'));
  const text = selector => h.document.querySelector(selector)?.textContent;
  const page = {locator: selector => selector};
  const expect = selector => ({
    toBeVisible: () => h.waitFor(() => Boolean(h.document.querySelector(selector))),
    toHaveText: regex => h.waitFor(() => regex.test(text(selector) || '')),
    not: {toHaveText: value => h.waitFor(() => Boolean(text(selector)) && text(selector) !== value)},
  });
  return {h, order, inspection, pendingOrder, button, page, expect};
}

test('production DOM reproduces early STEP2 click being reset by delayed workflow effect', async () => {
  const f = await setup();
  try {
    assert.equal(f.h.document.querySelector('.state-panel-main > p.subtle').textContent, 'workflow-v2 を読み込み中です。');
    assert.equal(f.button().disabled, false);
    await f.h.click(f.button());
    assert.ok(f.h.document.querySelector('.step-nav button.active').textContent.includes('OCR選択'));
    await f.h.settle(() => f.pendingOrder.resolve({data: f.order}));
    assert.ok(!f.h.document.querySelector('.step-nav button.active').textContent.includes('OCR選択'));
    assert.equal(f.h.document.querySelectorAll('.ocr-card').length, 0);
  } finally {await f.h.close();}
});

test('readiness waits for actual production DOM before click; delayed order cannot reset STEP2', async () => {
  const f = await setup(); let clicks = 0;
  try {
    const action = waitForWorkflowReadiness({...f, identity: {orderId: 'race', documentId: 'pdf'}, revision: 10})
      .then(async () => {clicks++; await f.h.click(f.button());});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(clicks, 0);
    f.pendingOrder.resolve({data: f.order});
    await action;
    await f.h.settle();
    assert.equal(clicks, 1);
    assert.ok(f.h.document.querySelector('.step-nav button.active').textContent.includes('OCR選択'));
    assert.equal(f.h.document.querySelectorAll('.ocr-card').length, 1);
  } finally {await f.h.close();}
});

for (const mismatch of ['order', 'document', 'revision']) test(`readiness rejects ${mismatch} mismatch before DOM interaction`, async () => {
  const order = {id: 'race', current_version: {document_id: 'pdf'}};
  const inspection = {order_id: 'race', workflow: {order_id: 'race', revision: 10}};
  if (mismatch === 'order') order.id = 'foreign';
  if (mismatch === 'document') order.current_version.document_id = 'foreign';
  if (mismatch === 'revision') inspection.workflow.revision = 11;
  await assert.rejects(waitForWorkflowReadiness({order, inspection, identity: {orderId: 'race', documentId: 'pdf'},
    revision: 10, page: {locator: () => assert.fail('must not interact with mismatched page')}}), {name: 'AssertionError'});
});
