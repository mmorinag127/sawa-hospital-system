// Actual page functions/card in a VM; partial rendering, not browser/live proof.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const filename = path.join(__dirname, '../src/pages/orders/index.tsx');
const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ['hasBlockedCandidateResolution', 'candidateBoundaryWarning', 'normalizeWeekGroup', 'weekMenuId', 'inlineFacilityHint', 'renderOrderCard', 'hydrateRuntimeOrders'];
const definitions = new Map();
function collect(node) {
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && names.includes(node.name.text)) {
    definitions.set(node.name.text, `const ${node.getText(source)};`);
  }
  ts.forEachChild(node, collect);
}
collect(source);
assert.equal(definitions.size, names.length);
const code = ts.transpileModule([...definitions.values()].join('\n') + '\nmodule.exports = {' + names.join(',') + '};', {
  compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const context = {
  React, module: { exports: {} },
  Link: ({ children, ...props }) => React.createElement('a', props, children),
  visibleReviewBadges: () => ({ workflowBadge: null, secondaryBadges: [] }),
  effectiveOrderStatus: (order) => order.status,
  reviewToneClass: () => '', statusClass: () => '',
  facilityLabel: (order) => order.facility || 'Unregistered',
  workflowHeadlineText: () => null, workflowPrimaryActionLabel: () => null,
  workflowSupportText: () => null, processingStageLabel: () => null,
  useCallback: fn => fn, runtimeOperation: {current: null}, AbortController, URLSearchParams,
  browserSessionKey: () => 'fixture-session', setIsHydratingRuntime: () => {},
  setLoadError: () => {}, setRuntimeError: () => {}, setOrders: () => {}, apiClient: {},
};
vm.runInNewContext(code, context, { filename });
const page = context.module.exports;
const range = '2026-01@2026-01-01~2026-01-03';
function row(blocked) {
  return { id: 'owned', status: '要確認', document: 'fixture', facility: null, week: null,
    candidate_resolution: { reference_hints: { facility_hint: 'reference-only', week_hint: range }, resolutions: {
      facility: { blocked, blocked_reasons: blocked ? ['missing_selection'] : [], resolved_value: 'F', resolved_label: 'Facility' },
      week: { blocked, blocked_reasons: blocked ? ['missing_week'] : [], resolved_value: range, resolved_label: 'Candidate week' },
    } } };
}

test('blocked actual card displays confirmation without adopting candidate grouping/facility', () => {
  const order = row(true);
  const before = structuredClone(order);
  const markup = renderToStaticMarkup(page.renderOrderCard(order));
  assert.match(markup, /role="alert"/);
  assert.match(markup, /施設・対象週の確認が必要です/);
  assert.equal(page.normalizeWeekGroup(order).key, 'unresolved');
  assert.equal(page.inlineFacilityHint(order), null);
  assert.equal(page.weekMenuId(order), '');
  assert.deepEqual(order, before);
});

test('normal candidate retains existing card/grouping behavior', () => {
  const order = row(false);
  const markup = renderToStaticMarkup(page.renderOrderCard(order));
  assert.doesNotMatch(markup, /施設・対象週の確認が必要です/);
  assert.equal(page.normalizeWeekGroup(order).key, range);
  assert.equal(page.inlineFacilityHint(order).facility_id, 'F');
  assert.equal(page.weekMenuId(order), '2026-01');
});

test('blocked sibling resolution cannot promote another resolution or reference hint', () => {
  const order = row(false);
  order.candidate_resolution.resolutions.facility.blocked = true;
  order.candidate_resolution.resolutions.facility.blocked_reasons = ['damaged_source'];
  assert.equal(page.normalizeWeekGroup(order).key, 'unresolved');
  assert.equal(page.inlineFacilityHint(order), null);
  assert.equal(page.weekMenuId(order), '');
  assert.equal(page.candidateBoundaryWarning(order), '施設・対象週の確認が必要です');
});

test('blocked candidates do not change explicitly stored week/status', () => {
  const order = row(true);
  order.week_value = '2026-02@2026-02-01~2026-02-03';
  const before = structuredClone(order);
  assert.equal(page.normalizeWeekGroup(order).key, order.week_value);
  assert.equal(page.weekMenuId(order), '2026-02');
  assert.deepEqual(order, before);
});

test('expanded week requests only its order IDs in serial bounded pages, not full list runtime', async () => {
  const group = Array.from({length: 105}, (_, index) => ({id: `group-${index}`, status: '未着'}));
  group.push({id: 'uploaded-only', source_kind: 'uploaded_pdf', status: '登録済み'});
  let rows = [...group, {id: 'other-week', status: '未着'}];
  const calls = [];
  let active = 0, maximum = 0;
  context.setOrders = change => {rows = change(rows);};
  context.apiClient.get = async (url, options) => {
    calls.push(url); maximum = Math.max(maximum, ++active);
    assert.equal(options.signal.aborted, false);
    assert.ok(url.startsWith('/orders/display-context?'));
    const ids = new URLSearchParams(url.split('?')[1]).getAll('order_ids');
    assert.ok(ids.length <= 100);
    await Promise.resolve(); --active;
    return {data: {orders: Object.fromEntries(ids.map(id => [id, {validation_state: 'references_only',
      workflow_state: {state: 'uploaded'}, job: null, error: null}]))}};
  };
  await page.hydrateRuntimeOrders(group);
  assert.equal(calls.length, 2);
  assert.equal(maximum, 1);
  const requested = calls.flatMap(url => new URLSearchParams(url.split('?')[1]).getAll('order_ids'));
  assert.deepEqual(requested, group.slice(0, 105).map(order => order.id));
  assert.equal(rows.find(order => order.id === 'other-week').workflow_state, undefined);
  assert.equal(rows.find(order => order.id === 'uploaded-only').workflow_state, undefined);
  assert.equal(rows[0].workflow_state.state, 'uploaded');
});

test('a superseded week response cannot alter current rows or clear its loading state', async () => {
  const pending = [];
  let rows = [{id: 'old-week'}, {id: 'new-week'}];
  let loading = false;
  context.setOrders = change => {rows = change(rows);};
  context.setIsHydratingRuntime = value => {loading = value;};
  context.apiClient.get = (url, options) => new Promise(resolve => pending.push({url, options, resolve}));
  const old = page.hydrateRuntimeOrders([rows[0]]);
  const current = page.hydrateRuntimeOrders([rows[1]]);
  assert.equal(pending[0].options.signal.aborted, true);
  pending[0].resolve({data: {orders: {'old-week': {validation_state: 'references_only', workflow_state: {state: 'stale'}}}}});
  await old;
  assert.equal(loading, true);
  assert.equal(rows[0].workflow_state, undefined);
  pending[1].resolve({data: {orders: {'new-week': {validation_state: 'references_only', workflow_state: {state: 'current'}}}}});
  await current;
  assert.equal(loading, false);
  assert.equal(rows[1].workflow_state.state, 'current');
});

test('a failed supplement keeps base rows and reports a separate error', async () => {
  const rows = [{id: 'still-visible', status: '未着'}];
  let supplementalError = '';
  context.setRuntimeError = value => {supplementalError = value;};
  context.setLoadError = () => {throw new Error('supplement must not hide the base list');};
  context.setOrders = () => {throw new Error('failed read must not change rows');};
  context.apiClient.get = async () => {throw new Error('owned supplement failure');};
  await page.hydrateRuntimeOrders(rows);
  assert.equal(supplementalError, 'owned supplement failure');
  assert.deepEqual(rows, [{id: 'still-visible', status: '未着'}]);
});
