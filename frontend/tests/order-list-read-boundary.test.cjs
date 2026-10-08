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
const names = ['hasBlockedCandidateResolution', 'candidateBoundaryWarning', 'normalizeWeekGroup', 'weekMenuId', 'inlineFacilityHint', 'renderOrderCard'];
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
