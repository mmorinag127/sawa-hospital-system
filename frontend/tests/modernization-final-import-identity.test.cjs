const test = require('node:test');
const assert = require('node:assert/strict');
const { extract } = require('./helpers/modernization-final-extract.cjs');
const file = 'src/pages/menus/[monthId].tsx';
const hasSavedImportSource = extract(file, 'hasSavedImportSource');
const hasSavedImportReview = extract(file, 'hasSavedImportReview');
const validate = extract(file, 'validateImportReceipt', { monthId: 'synthetic-month', hasSavedImportSource, hasSavedImportReview });
const snapshot = extract(file, 'validateSnapshot');

// Authored synthetic envelopes are not a FAX, OCR, database or live-flow proof.
function receipt(state = 'review') {
  const source = { filename: 'fixture.xlsx', file_uri: 'synthetic://source',
    content_sha256: 'a'.repeat(64), sheet_name: 'fixture', media_type: 'application/octet-stream',
    scope_override: null, diet_types: null };
  const review = { issues: [], resolutions: [], master_diffs: [], item_count: 0,
    plans: {}, items: [], entries: [], scope_override: null,
    input_snapshot: { revision: 1, digest: 'b'.repeat(64) } };
  return { id: 'receipt-A', month_id: 'synthetic-month', state, revision: 'revision-A',
    input_revision: 'input-A', current_input_revision: 'input-A', source, review, current_review: review };
}

for (const operation of ['resume', 'review', 'apply']) {
  const state = operation === 'apply' ? 'applied' : 'review';
  test(`${operation} exact receipt is accepted without mutation`, () => {
    const value = receipt(state), before = structuredClone(value);
    validate(value, 'receipt-A', operation === 'resume' ? undefined : state);
    assert.deepEqual(value, before);
  });
  for (const [name, patch] of [
    ['foreign id', { id: 'receipt-B' }], ['missing id', { id: undefined }],
    ['numeric id', { id: 1 }], ['blank id', { id: ' ' }],
    ['foreign month', { month_id: 'other-month' }], ['missing source', { source: null }],
    ['missing current review', { current_review: null }], ['missing revision', { revision: '' }],
    ['missing input revision', { input_revision: '' }], ['unknown state', { state: 'queued' }],
  ]) test(`${operation} rejects ${name} without modifying operator input`, () => {
    const value = { ...receipt(state), ...patch }, before = structuredClone(value);
    assert.throws(() => validate(value, 'receipt-A', operation === 'resume' ? undefined : state), /保存原本/);
    assert.deepEqual(value, before);
  });
  if (operation !== 'resume') test(`${operation} rejects the sibling receipt state`, () => {
    assert.throws(() => validate(receipt(state === 'review' ? 'applied' : 'review'), 'receipt-A', state), /保存原本/);
  });
}

test('new upload has no guessed receipt id, but must have a complete source', () => {
  validate({ ...receipt(), id: 'new-server-receipt' });
  for (const key of Object.keys(receipt().source)) {
    const value = receipt(); delete value.source[key];
    assert.throws(() => validate(value), /保存原本/, key);
  }
});

const envelope = () => ({ source_month_id: 'synthetic-month', menu: { id: 'synthetic-month' },
  items: [], entries: [], master_checks: { issues: [] }, revision: 'revision-A' });
test('generic snapshot accepts legitimate neighboring source month and nullable precreation menu', () => {
  snapshot({ ...envelope(), source_month_id: 'neighbor-month' });
  snapshot({ ...envelope(), menu: null });
});
test('apply-only source/display identity rejects foreign or missing month before adoption', () => {
  snapshot(envelope(), 'synthetic-month');
  snapshot({ ...envelope(), menu: null }, 'synthetic-month');
  for (const patch of [{ source_month_id: 'neighbor-month' }, { source_month_id: undefined },
    { menu: { id: 'neighbor-month' } }, { menu: {} }]) {
    assert.throws(() => snapshot({ ...envelope(), ...patch }, 'synthetic-month'), /月次メニュー/);
  }
});
test('missing snapshot containers/revision stop rather than substituting older data', () => {
  for (const patch of [{ items: null }, { entries: {} }, { master_checks: {} }, { revision: undefined }]) {
    assert.throws(() => snapshot({ ...envelope(), ...patch }), /月次メニュー/);
  }
});

// Actual handler closures with explicit transport/setter bindings: unit-only,
// not a mounted editor/owner-hook or persisted import integration test.
function handlers(reply) {
  const originalFile = { name: 'operator-original.xlsx' };
  const originalPending = { receipt: receipt(), saved: true, ownsInput: () => true, issues: [],
    resolutions: { raw: { issue_key: 'raw', amount: 0, nullable: null, unknown: { keep: '0.000100' } } } };
  const state = { file: originalFile, pendingReview: originalPending, uploadInputKey: 2,
    menu: { id: 'old-menu' }, items: [{ quantity: 0 }], entries: [{ raw: null }], revision: 'old-revision',
    masterCheckState: { issues: [], resolutions: {} }, submitting: false, message: '', notice: '' };
  const trace = [], calls = [];
  let live = true, changed = 0;
  const current = () => live; current.active = () => live;
  const owner = { capture: () => current, changed: () => { changed++; }, checkpoint: () => current };
  const setter = key => value => {
    trace.push(key);
    state[key] = typeof value === 'function' ? value(state[key]) : value;
  };
  const bindings = {
    monthId: 'synthetic-month', pendingReview: originalPending, owner,
    hasUnsavedChanges: true, window: { confirm: () => true }, itemFormEpoch: { current: 0 },
    validateImportReceipt: validate, validateSnapshot: snapshot,
    buildPendingReviewIssueStates: extract(file, 'buildPendingReviewIssueStates'),
    buildPendingReviewResolution: extract(file, 'buildPendingReviewResolution'),
    formatErrorDetail: extract(file, 'formatErrorDetail'),
    formatScopeLabel: extract(file, 'formatScopeLabel', { facilities: [] }),
    focusPendingReviewIssue: () => { throw new Error('Unexpected issue focus in empty-issue fixture'); },
    applyPendingReviewIssues: () => { throw new Error('Unexpected server issue projection'); },
    apiClient: {
      get: async (...args) => { calls.push(['get', ...args]); return { data: await reply() }; },
      post: async (...args) => { calls.push(['post', ...args]); return { data: await reply() }; },
    },
    setFile: setter('file'), setPendingReview: setter('pendingReview'),
    setUploadInputKey: setter('uploadInputKey'), setReviewSubmitting: setter('submitting'),
    setMessage: setter('message'), setPendingReviewNotice: setter('notice'), setLastUpload: setter('lastUpload'),
    loadUploadHistory: () => { trace.push('history'); },
    setMenu: setter('menu'), setItems: setter('items'), setItemPage: setter('itemPage'),
    setEntries: setter('entries'), setRevision: setter('revision'),
    itemFormDirty: true, exceptionDirty: true, itemRevision: { current: 'dirty-item' },
    exceptionRevision: { current: 'dirty-exception' }, masterRevisions: { current: {} },
    setMasterCheckState: setter('masterCheckState'),
  };
  bindings.adoptImport = extract(file, 'adoptImport', bindings);
  const actualAdoptSnapshot = extract(file, 'adoptSnapshot', bindings);
  bindings.adoptSnapshot = (...args) => { trace.push('adoptSnapshot'); return actualAdoptSnapshot(...args); };
  return { state, trace, calls, originalFile, originalPending, bindings,
    resume: extract(file, 'resumeImport', bindings),
    confirm: extract(file, 'confirmPendingReview', bindings),
    adoptSnapshot: actualAdoptSnapshot, retire: () => { live = false; }, changes: () => changed };
}

for (const action of ['resume', 'review', 'apply']) {
  for (const [label, patch] of [['foreign-id', { id: 'receipt-B' }], ['missing-id', { id: undefined }],
    ['foreign-month', { month_id: 'neighbor-month' }], ['missing-source', { source: undefined }],
    ['invalid-state', { state: 'queued' }],
    ...(action === 'resume' ? [] : [['wrong-state', { state: action === 'apply' ? 'review' : 'applied' }]])]) {
    test(`actual ${action} handler blocks ${label} before adoption/clear/successor`, async () => {
      const value = { ...receipt(action === 'apply' ? 'applied' : 'review'), ...patch };
      const h = handlers(async () => action === 'apply' ? { receipt: value, snapshot: envelope(), item_count: 0 } : value);
      const before = structuredClone({ items: h.state.items, entries: h.state.entries, revision: h.state.revision });
      await (action === 'resume' ? h.resume('receipt-A') : h.confirm(action === 'apply'));
      assert.equal(h.calls.length, 1);
      assert.equal(h.state.file, h.originalFile);
      assert.equal(h.state.pendingReview, h.originalPending);
      assert.equal(h.state.uploadInputKey, 2);
      assert.deepEqual({ items: h.state.items, entries: h.state.entries, revision: h.state.revision }, before);
      for (const key of ['adoptSnapshot', 'file', 'pendingReview', 'uploadInputKey', 'history']) assert.equal(h.trace.includes(key), false, key);
      assert.equal(h.state.submitting, false);
    });
  }
}

for (const missing of [undefined, null]) test(`actual apply rejects absent snapshot ${String(missing)} before clearing dirty inputs`, async () => {
  const h = handlers(async () => ({ receipt: receipt('applied'), snapshot: missing, item_count: 0 }));
  await h.confirm(true);
  assert.equal(h.state.file, h.originalFile);
  assert.equal(h.state.pendingReview, h.originalPending);
  assert.equal(h.state.uploadInputKey, 2);
  assert.equal(h.calls.length, 1);
  for (const key of ['adoptSnapshot', 'file', 'pendingReview', 'uploadInputKey', 'history']) assert.equal(h.trace.includes(key), false, key);
});

for (const patch of [{ source_month_id: 'neighbor-month' }, { source_month_id: undefined },
  { menu: { id: 'neighbor-month' } }, { items: null }]) {
  test(`actual apply checks snapshot before clearing original input ${JSON.stringify(patch)}`, async () => {
    const h = handlers(async () => ({ receipt: receipt('applied'), snapshot: { ...envelope(), ...patch }, item_count: 0 }));
    await h.confirm(true);
    assert.equal(h.state.file, h.originalFile);
    assert.equal(h.state.pendingReview, h.originalPending);
    assert.equal(h.trace.includes('adoptSnapshot'), false);
    assert.equal(h.trace.includes('history'), false);
    assert.equal(h.calls.length, 1);
  });
}

for (const action of ['resume', 'review', 'apply']) {
  for (const missing of [undefined, null]) test(`actual ${action} rejects absent receipt ${String(missing)} before clearing dirty inputs`, async () => {
    const h = handlers(async () => action === 'apply' ? { receipt: missing, snapshot: envelope(), item_count: 0 } : missing);
    await (action === 'resume' ? h.resume('receipt-A') : h.confirm(action === 'apply'));
    assert.equal(h.state.file, h.originalFile);
    assert.equal(h.state.pendingReview, h.originalPending);
    assert.equal(h.state.uploadInputKey, 2);
    assert.equal(h.bindings.itemRevision.current, 'dirty-item');
    assert.equal(h.bindings.exceptionRevision.current, 'dirty-exception');
    assert.equal(h.calls.length, 1);
    for (const key of ['adoptSnapshot', 'file', 'pendingReview', 'uploadInputKey', 'history']) assert.equal(h.trace.includes(key), false, key);
  });
}

test('actual review sends raw resolutions/input revision and If-Match, adopts proper receipt once', async () => {
  const saved = receipt(), h = handlers(async () => saved);
  const resolutions = Object.values(h.originalPending.resolutions);
  await h.confirm(false);
  assert.deepEqual(h.calls, [['post', '/monthly-menus/synthetic-month/imports/receipt-A/review',
    { resolutions, input_revision: 'input-A' }, { headers: { 'If-Match': 'revision-A' } }]]);
  assert.equal(h.state.pendingReview.receipt, saved);
  assert.equal(h.changes(), 1);
  assert.equal(h.state.file, h.originalFile);
  assert.equal(h.trace.includes('history'), false);
});

test('actual resume keeps the requested receipt and original file without successor dispatch', async () => {
  const saved = receipt(); saved.current_review.unknown = { quantity: 0, nullable: null };
  const h = handlers(async () => saved);
  await h.resume('receipt-A');
  assert.deepEqual(h.calls, [['get', '/monthly-menus/synthetic-month/imports/receipt-A']]);
  assert.equal(h.state.pendingReview.receipt, saved);
  assert.equal(h.state.file, h.originalFile);
  assert.equal(h.state.uploadInputKey, 2);
  assert.deepEqual(saved.current_review.unknown, { quantity: 0, nullable: null });
  assert.equal(h.trace.includes('adoptSnapshot'), false);
  assert.equal(h.trace.includes('history'), false);
});

for (const menu of [{ id: 'synthetic-month' }, null]) {
  test(`actual valid apply accepts ${menu === null ? 'nullable menu' : 'owned menu'}, preserves raw payload and clears only after validation`, async () => {
    const value = { ...envelope(), menu, items: [{ quantity: 0, unknown: { raw: '0.000100' } }], entries: [{ quantity: null }] };
    const h = handlers(async () => ({ receipt: receipt('applied'), snapshot: value, item_count: 0 }));
    await h.confirm(true);
    assert.deepEqual(h.calls, [['post', '/monthly-menus/synthetic-month/imports/receipt-A/apply', undefined,
      { headers: { 'If-Match': 'revision-A' } }]]);
    assert.equal(h.state.items, value.items);
    assert.equal(h.state.entries, value.entries);
    assert.equal(h.state.file, null);
    assert.equal(h.state.pendingReview, null);
    assert.equal(h.state.uploadInputKey, 3);
    assert.ok(h.trace.indexOf('adoptSnapshot') < h.trace.indexOf('file'));
    assert.equal(h.trace.filter(key => key === 'history').length, 1);
    assert.equal(h.bindings.itemRevision.current, 'dirty-item');
    assert.equal(h.bindings.exceptionRevision.current, 'dirty-exception');
  });
}

test('actual generic adoption keeps legitimate neighboring source month without imposing apply-only fence', () => {
  const h = handlers(async () => null), value = { ...envelope(), source_month_id: 'neighbor-month',
    items: [{ quantity: 0 }], entries: [{ quantity: null }] };
  h.adoptSnapshot(value);
  assert.equal(h.state.menu, value.menu);
  assert.equal(h.state.items, value.items);
  assert.equal(h.state.entries, value.entries);
});

for (const action of ['resume', 'review', 'apply']) test(`actual ${action} handler suppresses late success after captured owner retirement`, async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const h = handlers(() => response);
  const operation = action === 'resume' ? h.resume('receipt-A') : h.confirm(action === 'apply');
  h.retire();
  finish(action === 'apply' ? { receipt: receipt('applied'), snapshot: envelope(), item_count: 0 } : receipt());
  await operation;
  assert.equal(h.state.file, h.originalFile);
  assert.equal(h.state.pendingReview, h.originalPending);
  assert.equal(h.trace.includes('adoptSnapshot'), false);
  assert.equal(h.trace.includes('history'), false);
  assert.equal(h.state.message, '');
});

for (const action of ['resume', 'review', 'apply']) test(`actual ${action} suppresses retired transport failure without changing dirty input`, async () => {
  let reject;
  const response = new Promise((resolve, fail) => { reject = fail; });
  const h = handlers(() => response);
  const operation = action === 'resume' ? h.resume('receipt-A') : h.confirm(action === 'apply');
  h.retire();
  reject({ response: { status: 409, data: { detail: 'obsolete-error' } } });
  await operation;
  assert.equal(h.state.file, h.originalFile);
  assert.equal(h.state.pendingReview, h.originalPending);
  assert.equal(h.state.message, '');
  assert.equal(h.trace.includes('history'), false);
  assert.equal(h.calls.length, 1);
});

function downloadFixture(reply) {
  let live = true;
  const calls = [], created = [], revoked = [], clicked = [], messages = [];
  const download = extract(file, 'downloadImport', {
    monthId: 'synthetic-month', hasSavedImportSource,
    owner: { capture: () => () => live }, formatErrorDetail: extract(file, 'formatErrorDetail'),
    apiClient: { get: async (...args) => { calls.push(args); return { data: await reply() }; } },
    URL: { createObjectURL: blob => { created.push(blob); return 'blob:synthetic-original'; },
      revokeObjectURL: url => revoked.push(url) },
    document: { createElement: name => {
      assert.equal(name, 'a');
      return { href: '', download: '', click() { clicked.push({ href: this.href, filename: this.download }); } };
    } },
    setMessage: message => messages.push(message),
  });
  return { download, calls, created, revoked, clicked, messages, retire: () => { live = false; } };
}

test('actual original download retains exact bytes, server MIME and saved filename, clicks and revokes only its URL', async () => {
  const bytes = Uint8Array.from([0, 255, 13, 10, 37, 80, 68, 70]);
  const blob = new Blob([bytes], { type: 'application/octet-stream' });
  const h = downloadFixture(async () => blob), source = receipt();
  source.source.filename = 'operator-original.xlsx';
  await h.download(source);
  assert.deepEqual(h.calls, [['/monthly-menus/synthetic-month/imports/receipt-A/download', { responseType: 'blob' }]]);
  assert.equal(h.created.length, 1);
  assert.equal(h.created[0], blob);
  assert.equal(h.created[0].type, 'application/octet-stream');
  assert.deepEqual(new Uint8Array(await h.created[0].arrayBuffer()), bytes);
  assert.deepEqual(h.clicked, [{ href: 'blob:synthetic-original', filename: 'operator-original.xlsx' }]);
  assert.deepEqual(h.revoked, ['blob:synthetic-original']);
  assert.deepEqual(h.messages, []);
});

for (const outcome of ['success', 'error']) test(`actual original download stops retired ${outcome} before objectURL/click/feedback`, async () => {
  let resolve, reject;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  const h = downloadFixture(() => pending), operation = h.download(receipt());
  h.retire();
  if (outcome === 'success') resolve(new Blob([Uint8Array.from([0, 255])], { type: 'application/pdf' }));
  else reject({ response: { data: { detail: 'obsolete-download' } } });
  await operation;
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.created, []);
  assert.deepEqual(h.clicked, []);
  assert.deepEqual(h.revoked, []);
  assert.deepEqual(h.messages, []);
});

for (const patch of [{ month_id: 'neighbor-month' }, { source: null }]) test(`actual original download invalid source stops before HTTP ${JSON.stringify(patch)}`, async () => {
  const h = downloadFixture(async () => { throw new Error('Forbidden download'); });
  await h.download({ ...receipt(), ...patch });
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.created, []);
  assert.deepEqual(h.clicked, []);
  assert.equal(h.messages.length, 1);
});
