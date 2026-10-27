import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { savedWorkflowContract, readSavedWorkflow, assertSavedCells, assertSavedInspectionDisplay, verifySavedWorkflowPages } from '../../scripts/saved-workflow-readonly.mjs';
import { businessPath, readonlyPageStatus } from '../../scripts/hospital-readonly-pages.mjs';

const origin = 'https://fixture.invalid';
const id = 'ORDfixture';
function fixture() {
  const workflow = { order_id: id, revision: 7, facility_id: 'FACfixture', week_start: '2026-09-13',
    week_end: '2026-09-19', template_id: 'template', template_version_id: 'version', saved_sheet_id: 'saved7', state: 'confirmed' };
  const document = { document_id: 'doc', is_current: true, version_no: 2 };
  return { workflow, inspection: { order_id: id, workflow: structuredClone(workflow),
    saved_sheet: { saved_sheet_id: 'saved7', sheet: { fields: ['date', 'quantity'], header: ['日付', '数量'],
      rows: [['2026-09-13', 0], ['2026-09-14', '001']], extra: { unknown: ['preserved'] } } } },
    order: { id, document_id: 'doc', current_version: document, versions: [document], version_count: 1 } };
}
test('saved contract preserves all sheet metadata and zero/string quantities without mutating input', () => {
  const data = fixture(), before = structuredClone(data);
  data.workflow.menu_amount_resolution = { status: 'ready' };
  const result = savedWorkflowContract(data, id);
  assert.deepEqual(result.sheet, before.inspection.saved_sheet.sheet);
  assert.equal(result.sheet, data.inspection.saved_sheet.sheet);
  delete data.workflow.menu_amount_resolution;
  assert.deepEqual(data, before);
});
test('foreign order, revision, sheet identity, missing sheet and ambiguous PDF stop', () => {
  const mutations = [d => d.order.id = 'other', d => d.inspection.order_id = 'other',
    d => d.inspection.workflow.revision++, d => d.inspection.saved_sheet.saved_sheet_id = 'other',
    d => d.inspection.saved_sheet = null, d => d.order.versions.push(d.order.current_version),
    d => d.workflow.week_start = '2026-02-30', d => d.order.current_version.is_current = false];
  for (const mutate of mutations) { const data = fixture(); mutate(data); assert.throws(() => savedWorkflowContract(data, id)); }
});
test('displayed saved version and complete sheet include unknown fields; only transport sidecar is absent', () => {
  const inspection = fixture().inspection;
  assertSavedInspectionDisplay(JSON.stringify(inspection), { ...inspection, ocr_candidates: { results: [] } });
  for (const mutate of [d => d.saved_sheet.sheet.extra.unknown.pop(), d => d.workflow.revision++,
    d => d.saved_sheet.sheet.rows[0][1] = '0', d => d.saved_sheet.saved_sheet_id = 'old']) {
    const changed = structuredClone(inspection); mutate(changed);
    assert.throws(() => assertSavedInspectionDisplay(JSON.stringify(changed), inspection));
  }
  assert.throws(() => assertSavedInspectionDisplay('<html>login</html>', inspection));
});
function response(path, body, status = 200, method = 'GET', host = origin) {
  return { url: () => host + path, status: () => status, request: () => ({ method: () => method }),
    json: async () => body, body: async () => Buffer.from('%PDF-complete-fixture') };
}
function listeners() {
  const waits = [];
  return { waits, page: { waitForResponse: predicate => new Promise((resolve, reject) => waits.push({ predicate, resolve, reject })),
    locator: () => ({filter: () => ({click: async () => {}})}) },
    emit(value) { for (const wait of waits) if (wait.predicate(value)) wait.resolve(value); } };
}
function emitJSON(events, data, status = 200) {
  events.emit(response(`/api/orders/${id}/workflow-v2`, data.workflow, status));
  events.emit(response(`/api/orders/${id}/workflow-v2/display?scope=summary`, {...data.inspection, order_header: data.order}, status));
  events.emit(response(`/api/orders/${id}/workflow-v2/display?scope=sheet`, {...data.inspection, order_header: data.order}, status));
  events.emit(response(`/api/orders/${id}`, data.order));
}
test('all four waiters precede navigation and delayed PDF body prevents early success', async () => {
  const events = listeners(), data = fixture();
  let done = false;
  const pending = readSavedWorkflow({ ...events, origin, orderId: id, mark() {}, action: async () => {
    assert.equal(events.waits.length, 4); emitJSON(events, data);
  } }).then(value => { done = true; return value; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(done, false);
  events.emit(response(`/api/orders/${id}/document?document_id=doc`));
  assert.deepEqual((await pending).saved, data.inspection.saved_sheet);
});
test('response matching rejects foreign order/origin, POST and absent or duplicate sidecar query', async () => {
  const events = listeners();
  const pending = readSavedWorkflow({ ...events, origin, orderId: id, mark() {}, action: async () => {} });
  for (const value of [response('/api/orders/other/workflow-v2'), response(`/api/orders/${id}/workflow-v2`, {}, 200, 'POST'),
    response(`/api/orders/${id}/workflow-v2`, {}, 200, 'GET', 'https://other.invalid')]) {
    assert.equal(events.waits.some(wait => wait.predicate(value)), false);
  }
  for (const query of ['', '?include_ocr_results=true&include_ocr_results=true'])
    assert.equal(events.waits.some(wait => wait.predicate(response(`/api/orders/${id}/workflow-v2/inspection${query}`))), false);
  events.waits.forEach(wait => wait.reject(new Error('fixture timeout')));
  await assert.rejects(pending);
});
test('HTTP failure, truncated PDF, wrong selected PDF and body failure cannot pass', async () => {
  for (const mode of ['403', '500', 'truncated', 'foreign-pdf', 'body-failure']) {
    const events = listeners();
    await assert.rejects(readSavedWorkflow({ ...events, origin, orderId: id, mark() {}, action: async () => {
      emitJSON(events, fixture(), ['403', '500'].includes(mode) ? Number(mode) : 200);
      const pdf = response(`/api/orders/${id}/document?document_id=${mode === 'foreign-pdf' ? 'other' : 'doc'}`);
      if (mode === 'truncated') pdf.body = async () => Buffer.from('%PD');
      if (mode === 'body-failure') pdf.body = async () => { throw new Error('secret not logged'); };
      events.emit(pdf);
    } }));
  }
});
function cellDOM(sheet) {
  const dom = new JSDOM('<table class="sheet-table"><thead><tr></tr></thead><tbody></tbody></table>');
  const document = dom.window.document;
  for (const label of ['#', ...sheet.header]) { const th = document.createElement('th'); th.textContent = label; document.querySelector('thead tr').append(th); }
  sheet.rows.forEach((row, r) => {
    const tr = document.createElement('tr');
    row.forEach((cell, c) => { const td = document.createElement('td'), input = document.createElement('input');
      input.dataset.sheetRow = r; input.dataset.sheetCol = c; input.value = String(cell ?? ''); td.append(input); tr.append(td); });
    document.querySelector('tbody').append(tr);
  });
  function locator(selector) { return { locator, allTextContents: async () => [...document.querySelectorAll(selector)].map(node => node.textContent),
    evaluateAll: async fn => fn([...document.querySelectorAll(selector)]), selector }; }
  return { document, page: { locator }, expect: value => ({ toBeVisible: async () => assert.ok(document.querySelector(value.selector)),
    toHaveCount: async count => assert.equal(document.querySelectorAll(value.selector).length, count) }), mark() {} };
}
test('actual input DOM preserves every ordered value, rejecting changed quantity and missing row', async () => {
  const sheet = fixture().inspection.saved_sheet.sheet;
  const dom = cellDOM(sheet);
  await assertSavedCells({ ...dom, sheet });
  dom.document.querySelectorAll('input')[1].value = '1';
  await assert.rejects(assertSavedCells({ ...dom, sheet }));
  dom.document.querySelectorAll('input')[1].value = '0';
  dom.document.querySelector('tbody tr').remove();
  await assert.rejects(assertSavedCells({ ...dom, sheet }));
});
test('STEP3 still runs after STEP1 failure; unauthorized/error results remain failed', async () => {
  const names = [];
  await verifySavedWorkflowPages({ page: {}, expect() {}, origin, orderId: id, mark() {}, navigate() {},
    receipt: async () => { throw new Error('denied'); }, check: async (name, action) => { names.push(name); await assert.rejects(action); } });
  assert.deepEqual(names, ['saved-workflow-step1', 'saved-workflow-step3']);
  assert.equal(readonlyPageStatus([{ status: 403 }], true), 'blocked');
  assert.equal(readonlyPageStatus([{ status: 500 }], true), 'failed');
});
test('source-grounded controls, saved lineage and safe screenshot path; manual remains last', () => {
  const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
  const ui = read('../../src/pages/orders/[id]/workflow-v2.tsx');
  for (const text of ['workflow steps', '施設 (Step1 必須)', '週 (Step1 必須)', 'data-sheet-row={rowIdx}',
    'data-sheet-col={colIdx}', 'デバッグ用 raw JSON を開く', '{workflow?.saved_sheet_id || "-"}']) assert.ok(ui.includes(text));
  assert.ok(read('../../src/pages/orders/index.tsx').includes('`/orders/${order.id}/workflow-v2`'));
  const connection = read('../../scripts/hospital-readonly-pages.mjs');
  assert.ok(connection.indexOf('await verifySavedWorkflowPages') < connection.indexOf("await check('manual-library'"));
  assert.ok(connection.includes("assert.ok(pages.every(entry => entry.status === 'passed'))"));
  assert.equal(businessPath(`/orders/${id}/workflow-v2`), true);
  assert.equal(businessPath(`/orders/${id}/workflow-v2/edit`), false);
  const helper = read('../../scripts/saved-workflow-readonly.mjs');
  assert.doesNotMatch(helper, /\.fill\(|\.selectOption\(|\.fetch\(|\.post\(|\.put\(/);
});
