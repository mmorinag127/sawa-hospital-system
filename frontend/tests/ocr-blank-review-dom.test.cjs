const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport } = require('./helpers/modernization-react-dom.cjs');

test('Step2 preserves raw prediction and requires explicit approval after opening review', async () => {
  const t = transport(), order = 'blank-review', root = `/orders/${order}`;
  const workflow = {order_id: order, revision: 7, state: 'ocr_completed',
    selected_ocr_result_id: null, blockers: [], warnings: []};
  const cells = [{sheet_cell: 'F11', raw_text: '1', reason: 'hakodate_blank_input_nonempty_prediction'}];
  t.on('get', '/auth/me', () => ({data: {role: 'operator'}}));
  t.on('get', '/facilities', () => ({data: {source: 'canonical_json', revision: 'test', facilities: []}}));
  t.on('get', `${root}/workflow-v2`, () => ({data: workflow}));
  const results = [
    {ocr_result_id: 'blocked-result', selectable: false, recognition_review: {count: 1, cells}},
    {ocr_result_id: 'ready-result', selectable: true},
    {ocr_result_id: 'unknown-result'},
  ];
  t.onWorkflowInspection(`${root}/workflow-v2/inspection?include_ocr_results=true&history_details=false&include_order_header=true`, () => ({data: {
    workflow, saved_sheet: null, ocr_candidates: require('./helpers/ocr-candidates.cjs')(order, results, workflow),
  }}));
  t.on('get', root, () => ({data: {id: order, current_version: {document_id: 'pdf'},
    versions: [{document_id: 'pdf', is_current: true}]}}));
  t.on('get', `${root}/week-options`, () => ({data: {options: []}}));
  t.on('get', `${root}/document`, () => ({data: new Blob(['synthetic'])}));
  t.on('get', `${root}/workflow-v2/recognition-review`, call => {
    assert.equal(call.config.params.ocr_result_id, 'blocked-result');
    assert.equal(call.config.params.document_id, 'pdf');
    return {data: {order_id: order, mode: call.config.params.mode, retained_cell_count: 1, source_receipt: {workflow_revision: 7,
      recognition_mode: call.config.params.mode,
      recognition_evidence_run_id: 'blocked-result', selected_pdf_receipt: {document_id: 'pdf'}},
      review_cells: cells, cells: [{cell: 'F11', image_png_base64: ''}]}};
  });
  const probe = {component: 'OrderWorkflowV2Page', bindings: ['ocrResults', 'setVisibleStep']};
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', {
    api: t.api, probe, route: `${root}/workflow-v2`, routerState: {query: {id: order}},
  });
  const originalShadowRoot = Object.getOwnPropertyDescriptor(global, 'ShadowRoot');
  Object.defineProperty(global, 'ShadowRoot', {configurable: true, value: h.document.defaultView.ShadowRoot});
  try {
    await h.waitFor(() => probe.current?.ocrResults.length === 3);
    await h.settle(() => probe.current.setVisibleStep(2));
    const cards = [...h.document.querySelectorAll('.ocr-card')];
    assert.equal(cards.length, 3);
    assert.match(cards[0].textContent, /要確認: 1セル/);
    assert.match(cards[0].textContent, /F11: 「1」/);
    assert.match(cards[0].textContent, /認識画像は白紙/);
    const select = card => [...card.querySelectorAll('button')].find(b => b.textContent === '正解にする');
    const review = [...cards[0].querySelectorAll('button')].find(b => b.textContent === '確認して正解にする');
    assert.equal(review.disabled, false);
    assert.equal(select(cards[1]).disabled, false);
    assert.equal(select(cards[2]).disabled, true);
    assert.equal(cells[0].raw_text, '1');
    const mutations = () => t.calls.filter(call => ['put', 'post', 'delete'].includes(call.method));
    assert.equal(mutations().length, 0);
    await h.click(review);
    await h.waitFor(() => h.document.querySelector('[role="dialog"]'));
    const dialog = h.document.querySelector('[role="dialog"]');
    assert.match(dialog.textContent, /読み取り結果の確認・採用/);
    assert.match(dialog.textContent, /読み取り原文: 「1」/);
    assert.match(dialog.textContent, /認識画像は白紙/);
    assert.ok(dialog.querySelector('img[alt="F11のFAX原画像"]'));
    assert.ok(h.button('確認した値を採用して次へ'));
    assert.equal(mutations().length, 0);
    assert.equal(workflow.selected_ocr_result_id, null);
    assert.equal(cells[0].raw_text, '1');
    await h.click(h.button('閉じる'));
    await h.waitFor(() => !h.document.querySelector('[role="dialog"]'));
    await h.click([...cards[0].querySelectorAll('button')].find(b => b.textContent === 'セル画像を確認・訂正'));
    await h.waitFor(() => h.document.querySelector('[role="dialog"]'));
    assert.match(h.document.querySelector('[role="dialog"]').textContent, /未確認 1 セル/);
    assert.equal(mutations().length, 0);
  } finally {
    await h.close();
    if (originalShadowRoot) Object.defineProperty(global, 'ShadowRoot', originalShadowRoot);
    else delete global.ShadowRoot;
  }
});
