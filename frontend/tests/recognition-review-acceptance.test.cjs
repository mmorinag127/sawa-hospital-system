const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Actual React/MUI page; synthetic HTTP and actor, not staging or OCR accuracy proof.
const ORDER = 'synthetic-review', BASE = `/orders/${ORDER}/workflow-v2`;
const cells = ['H17', 'G20', 'G21', 'G29', 'J38', 'G45', 'I49', 'I50', 'I60', 'J60'];
const flags = cells.map(sheet_cell => ({sheet_cell, raw_text: ['J38', 'I50'].includes(sheet_cell) ? '--' : '-', reason: 'hakodate_ocr_quantity_non_numeric'}));
const copy = value => JSON.parse(JSON.stringify(value));
async function setup({ normal = false, job = null, initialStep = 2 } = {}) {
  const t = transport(), state = { revision: 7, selected: null, accepted: false };
  const current = () => ({order_id: ORDER, revision: state.revision, state: state.selected ? 'ocr_selected' : 'ocr_failed', selected_ocr_result_id: state.selected, blockers: [], warnings: [],
    headline: 'Step1要対応: OCR失敗', ocr_job: job});
  const result = () => ({ocr_result_id: 'ocr-A', order_id: ORDER, status: 'diagnostic_only', selectable: normal || state.accepted,
    recognition_review: normal ? null : {count: flags.length, cells: copy(flags)}, recognition_acceptance: state.accepted ? {values: copy(state.values)} : null});
  t.on('get', '/auth/me', () => ({data: {role: 'operator'}}));
  t.on('get', '/facilities', () => ({data: {source: 'canonical_json', revision: 'synthetic', facilities: []}}));
  t.on('get', BASE, () => ({data: current()}));
  t.on('get', `${BASE}/ocr-results`, () => ({data: require('./helpers/ocr-candidates.cjs')(ORDER, [result()], current())}));
  t.on('get', `${BASE}/inspection?include_ocr_results=true&history_details=false`, () => ({data: {workflow: current(), ocr_candidates: require('./helpers/ocr-candidates.cjs')(ORDER, [result()], current()), saved_sheet: null, pre_save_checks: {}}}));
  t.on('get', `/orders/${ORDER}`, () => ({data: {id: ORDER, current_version: {document_id: 'pdf-A'}, versions: [{document_id: 'pdf-A', is_current: true}, {document_id: 'pdf-B', is_current: false}]}}));
  t.on('get', `/orders/${ORDER}/week-options`, () => ({data: {options: []}}));
  t.on('get', `/orders/${ORDER}/document`, () => ({data: new Blob(['synthetic component only'])}));
  const receipt = {workflow_revision: 7, recognition_evidence_run_id: 'ocr-A', selected_pdf_receipt: {document_id: 'pdf-A'}};
  t.on('get', `${BASE}/recognition-review`, call => ({data: {order_id: ORDER, mode: call.config.params.mode, retained_cell_count: 11,
    source_receipt: {...copy(receipt), recognition_mode: call.config.params.mode},
    review_cells: copy(flags), cells: [...cells, 'E11'].map(cell => ({cell, image_png_base64: ''}))}}));
  t.on('put', `${BASE}/recognition-review/accept`, call => {
    assert.equal(call.config.headers['If-Match'], '"7"');
    state.accepted = true; ++state.revision;
    state.values = copy(call.body.values);
    return {data: current()};
  });
  t.on('post', `${BASE}/ocr-results/ocr-A/select`, call => {
    assert.equal(call.config.headers['If-Match'], `"${state.revision}"`);
    state.selected = 'ocr-A'; ++state.revision;
    return {data: current()};
  });
  const probe = {component: 'OrderWorkflowV2Page', bindings: ['workflow', 'effectiveSelectedDocumentId', 'visibleStep', 'setVisibleStep',
    'recognitionReview', 'recognitionValues', 'recognitionDisplayCells', 'saveRecognitionReview', 'acceptRecognitionReview', 'busy', 'error',
    'setSelectedDocumentId', 'editorVersion', 'beginWorkflowEditorEdit', 'loadRecognitionReview', 'setRecognitionValues', 'recognitionReviewReason']};
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', {api: t.api, probe, route: `/orders/${ORDER}/workflow-v2`, routerState: {query: {id: ORDER}}});
  const shadow = Object.getOwnPropertyDescriptor(global, 'ShadowRoot');
  Object.defineProperty(global, 'ShadowRoot', {configurable: true, value: h.document.defaultView.ShadowRoot});
  const close = h.close;
  h.close = async () => {try {await close();} finally {if (shadow) Object.defineProperty(global, 'ShadowRoot', shadow); else delete global.ShadowRoot;}};
  try {
    await h.waitFor(() => probe.current?.workflow?.revision === 7 && probe.current.effectiveSelectedDocumentId === 'pdf-A');
    await h.settle(() => probe.current.setVisibleStep(initialStep));
    return {h, probe, t, state, receipt};
  } catch (error) {await h.close(); throw error;}
}
const mutations = f => f.t.calls.filter(call => ['put', 'post', 'delete'].includes(call.method));
async function open(f) {await f.h.click(f.h.button('確認して正解にする')); await f.h.waitFor(() => f.probe.current.recognitionReview !== null && !f.probe.current.busy);}

test('legacy correction after closing adoption still saves all cells for resume without acceptance or selection', async () => {
  const f = await setup();
  try {
    await open(f);
    await f.h.click(f.h.button('閉じる'));
    await f.h.click(f.h.button('セル画像を確認・訂正'));
    await f.h.waitFor(() => f.probe.current.recognitionReview !== null && !f.probe.current.busy);
    assert.equal(f.probe.current.recognitionDisplayCells.length, 11);
    assert.equal(f.h.document.body.textContent.includes('要確認の全セルを空欄に指定'), false);
    const choices = Array.from(f.h.document.querySelectorAll('[role="dialog"] input[type="checkbox"]'));
    assert.equal(choices.length, 11);
    for (const choice of choices) await f.h.click(choice);
    f.t.on('put', `${BASE}/recognition-review`, call => {
      assert.equal(call.config.headers['If-Match'], '"7"');
      assert.deepEqual(call.body.values, Object.fromEntries([...cells, 'E11'].map(cell => [cell, ''])));
      ++f.state.revision;
      return {data: {order_id: ORDER, revision: f.state.revision, state: 'context_confirmed', selected_ocr_result_id: null}};
    });
    await f.h.click(f.h.button('手入力を保存'));
    await f.h.waitFor(() => !f.probe.current.busy && f.probe.current.recognitionReview === null);
    assert.deepEqual(mutations(f).map(call => call.url), [`${BASE}/recognition-review`]);
    assert.equal(f.state.accepted, false);
    assert.equal(f.state.selected, null);
  } finally {await f.h.close();}
});

for (const result_state of ['review_required', 'failed']) test(`header classifies ${result_state} without changing the raw failed job`, async () => {
  const job = {order_id: ORDER, status: 'failed', result_state, recovery_action: 'review_recognition_cells',
    processing_stage: 'cell_recognition', error_detail: 'raw diagnostic: synthetic failure'};
  const f = await setup({job, initialStep: 1});
  try {
    const panel = f.h.document.querySelector('.state-panel');
    if (result_state === 'review_required') {
      assert.match(panel.querySelector('h2').textContent, /Step2: 読み取り結果の確認待ち/);
      assert.doesNotMatch(panel.querySelector('h2').textContent, /OCR失敗|Step1/);
      assert.doesNotMatch(panel.querySelector('.summary-grid').textContent, /OCR停止|OCR失敗/);
      assert.match(panel.querySelector('details').textContent, /raw diagnostic: synthetic failure/);
      const readsBefore = f.t.calls.filter(call => call.url.endsWith('/recognition-review')).length;
      assert.equal(f.probe.current.visibleStep, 1);
      await f.h.click(f.h.button('Step2で読み取り結果を確認'));
      assert.equal(f.probe.current.visibleStep, 2);
      assert.equal(f.probe.current.recognitionReview, null);
      assert.equal(f.t.calls.filter(call => call.url.endsWith('/recognition-review')).length, readsBefore);
      assert.equal(f.probe.current.workflow.selected_ocr_result_id, null);
      assert.equal(mutations(f).length, 0);
    } else {
      assert.match(panel.querySelector('h2').textContent, /Step1要対応: OCR失敗/);
      assert.doesNotMatch(panel.querySelector('h2').textContent, /確認待ち/);
      assert.match(f.h.document.querySelector('.notice.error').textContent, /OCR失敗:.*raw diagnostic/);
    }
    assert.equal(f.probe.current.workflow.ocr_job.status, 'failed');
    assert.equal(f.probe.current.workflow.ocr_job.error_detail, job.error_detail);
  } finally {await f.h.close();}
});

test('review_required alone does not hide a genuine failure with another recovery action', async () => {
  const f = await setup({job: {status: 'failed', result_state: 'review_required', recovery_action: 'retry', error_detail: 'transport failed'}});
  try {
    assert.match(f.h.document.querySelector('.state-panel h2').textContent, /OCR失敗/);
    assert.match(f.h.document.querySelector('.notice.error').textContent, /OCR失敗:.*transport failed/);
    assert.equal(f.h.document.querySelector('.state-panel').textContent.includes('Step2で読み取り結果を確認'), false);
  } finally {await f.h.close();}
});

test('ten dash cells have a usable primary review path; explicit bulk blank adopts then selects with new CAS', async () => {
  const f = await setup();
  try {
    assert.equal(f.h.button('確認して正解にする').disabled, false);
    assert.match(f.h.document.body.textContent, /数量として解釈できない文字・記号/);
    assert.doesNotMatch(f.h.document.body.textContent, /認識画像と読み取り結果が一致しないため/);
    await open(f);
    assert.equal(f.probe.current.recognitionDisplayCells.length, 10); // not all 11 supplied images
    assert.equal(Object.keys(f.probe.current.recognitionValues).length, 0);
    assert.equal(f.h.button('確認した値を採用して次へ').disabled, true);
    assert.equal(mutations(f).length, 0);
    await f.h.click(f.h.button('要確認の全セルを空欄に指定'));
    assert.deepEqual(JSON.parse(JSON.stringify(f.probe.current.recognitionValues)), Object.fromEntries(cells.map(cell => [cell, ''])));
    await f.h.click(f.h.button('確認した値を採用して次へ'));
    await f.h.waitFor(() => f.probe.current.visibleStep === 3 && !f.probe.current.busy);
    assert.deepEqual(mutations(f).map(call => call.url), [`${BASE}/recognition-review/accept`, `${BASE}/ocr-results/ocr-A/select`]);
    assert.deepEqual(mutations(f)[0].body.values, Object.fromEntries(cells.map(cell => [cell, ''])));
    assert.equal(mutations(f)[1].config.headers['If-Match'], '"8"');
    assert.equal(f.probe.current.workflow.selected_ocr_result_id, 'ocr-A');
    assert.equal(f.t.calls.some(call => call.url.endsWith('/ocr-runs')), false);
  } finally {await f.h.close();}
});

for (const cancel of [true, false]) test(`bulk blank ${cancel ? 'cancellation' : 'session retirement during confirmation'} never adopts values`, async () => {
  const f = await setup();
  try {
    await open(f);
    window.confirm = () => {if (!cancel) f.h.loader.load('src/services/browserSession.ts').clearBrowserSession(); return !cancel;};
    await f.h.click(f.h.button('要確認の全セルを空欄に指定'));
    assert.equal(Object.keys(f.probe.current.recognitionValues).length, 0);
    assert.equal(mutations(f).length, 0);
  } finally {await f.h.close();}
});

for (const retirement of ['pdf', 'session', 'input']) test(`deferred accept cannot select after ${retirement} retirement`, async () => {
  const f = await setup(), pending = deferred();
  try {
    await open(f); await f.h.click(f.h.button('要確認の全セルを空欄に指定'));
    f.t.on('put', `${BASE}/recognition-review/accept`, () => pending.promise);
    let command;
    await f.h.settle(() => {command = f.probe.current.acceptRecognitionReview();});
    if (retirement === 'session') await f.h.session(false);
    else await f.h.settle(() => retirement === 'pdf' ? f.probe.current.setSelectedDocumentId('pdf-B') : f.probe.current.beginWorkflowEditorEdit('recognition'));
    await f.h.settle(() => pending.resolve({data: {order_id: ORDER, revision: 8, state: 'ocr_failed'}}));
    await command;
    assert.equal(mutations(f).filter(call => call.method === 'post').length, 0);
    assert.notEqual(f.probe.current.workflow.revision, 8);
  } finally {await f.h.close();}
});

test('accept success and selection conflict truthfully reports saved acceptance without moving step', async () => {
  const f = await setup();
  try {
    await open(f); await f.h.click(f.h.button('要確認の全セルを空欄に指定'));
    f.t.on('post', `${BASE}/ocr-results/ocr-A/select`, () => Promise.reject({response: {status: 409}}));
    await f.h.click(f.h.button('確認した値を採用して次へ'));
    await f.h.waitFor(() => !f.probe.current.busy && f.probe.current.error);
    assert.match(f.probe.current.error, /確認した値は保存済み.*選択は完了していません/);
    assert.equal(f.probe.current.visibleStep, 2);
    assert.equal(f.probe.current.workflow.revision, 8);
  } finally {await f.h.close();}
});

test('normal warning-free selection keeps its existing command without review', async () => {
  const f = await setup({normal: true});
  try {
    await f.h.click(f.h.button('正解にする'));
    await f.h.waitFor(() => f.probe.current.visibleStep === 3);
    assert.deepEqual(mutations(f).map(call => call.url), [`${BASE}/ocr-results/ocr-A/select`]);
  } finally {await f.h.close();}
});

test('each actual review reason has a distinct concrete description; unknown reason stays explicit', async () => {
  const f = await setup();
  try {
    const reason = f.probe.current.recognitionReviewReason;
    for (const [code, text] of Object.entries({hakodate_blank_input_nonempty_prediction: '白紙', hakodate_ocr_quantity_non_numeric: '文字・記号',
      hakodate_preprocess_ink_loss: '筆跡', hakodate_ocr_quantity_low_confidence: '認識スコア', hakodate_ocr_quantity_candidate_conflict: '認識候補'})) assert.match(reason(code), new RegExp(text));
    assert.match(reason('unrecognized-code'), /不明.*unrecognized-code/);
  } finally {await f.h.close();}
});

test('individual numeric interpretation retains dash history and shows confirmed status', async () => {
  const f = await setup();
  try {
    await open(f); await f.h.click(f.h.button('要確認の全セルを空欄に指定'));
    const field = [...f.h.document.querySelectorAll('input')].find(input => [...(input.labels ?? [])].some(label => label.textContent === 'H17'));
    await f.h.change(field, '4');
    assert.equal(f.probe.current.recognitionValues.H17, '4');
    await f.h.click(f.h.button('確認した値を採用して次へ'));
    await f.h.waitFor(() => f.probe.current.visibleStep === 3 && !f.probe.current.busy);
    assert.equal(mutations(f)[0].body.values.H17, '4');
    assert.equal(mutations(f)[0].body.values.J38, '');
    await f.h.settle(() => f.probe.current.setVisibleStep(2));
    assert.match(f.h.document.body.textContent, /確認済み: 10セル/);
    assert.match(f.h.document.body.textContent, /H17: 「-」.*採用値: 4/);
    assert.doesNotMatch(f.h.document.body.textContent, /要確認: 10セル/);
  } finally {await f.h.close();}
});

for (const retirement of ['pdf', 'input']) test(`bulk confirmation cannot alter values after ${retirement} changes within confirm`, async () => {
  const f = await setup();
  try {
    await open(f);
    window.confirm = () => {if (retirement === 'pdf') f.probe.current.setSelectedDocumentId('pdf-B'); else f.probe.current.beginWorkflowEditorEdit('sheet'); return true;};
    await f.h.click(f.h.button('要確認の全セルを空欄に指定'));
    assert.equal(Object.keys(f.probe.current.recognitionValues).length, 0);
    assert.equal(mutations(f).length, 0);
  } finally {await f.h.close();}
});

test('saved cross-evidence draft refusal is concrete and retains explicit review choices', async () => {
  const f = await setup();
  try {
    await open(f); await f.h.click(f.h.button('要確認の全セルを空欄に指定'));
    f.t.on('put', `${BASE}/recognition-review/accept`, () => Promise.reject({response: {status: 400, data: {detail: 'recognition_acceptance_saved_draft_conflict'}}}));
    await f.h.click(f.h.button('確認した値を採用して次へ'));
    await f.h.waitFor(() => !f.probe.current.busy && f.probe.current.error);
    assert.match(f.probe.current.error, /別のOCR結果.*保存済みシート.*編集内容を保持/);
    assert.ok(f.probe.current.recognitionReview);
    assert.equal(Object.keys(f.probe.current.recognitionValues).length, 10);
    assert.equal(mutations(f).filter(call => call.method === 'post').length, 0);
  } finally {await f.h.close();}
});
