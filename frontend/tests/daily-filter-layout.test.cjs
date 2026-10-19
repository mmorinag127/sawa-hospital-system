// jsdom/MUI policy + synthetic API proof, not browser geometry or stg acceptance.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

const filename = path.resolve(__dirname, '../src/pages/daily-delivery-notes.tsx');
function policy() {
  const ast = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  assert.deepEqual(ast.parseDiagnostics, []);
  const found = {};
  function literal(node) {
    if (ts.isStringLiteral(node)) return node.text;
    if (ts.isNumericLiteral(node)) return Number(node.text);
    assert.ok(ts.isObjectLiteralExpression(node), 'layout policy must be explicit');
    return Object.fromEntries(node.properties.map(property => {
      assert.ok(ts.isPropertyAssignment(property));
      return [property.name.getText(ast), literal(property.initializer)];
    }));
  }
  function visit(node) {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const open = node.openingElement || node;
      const attributes = Object.fromEntries(open.attributes.properties.filter(ts.isJsxAttribute).map(attribute => [attribute.name.text, attribute.initializer]));
      if (open.tagName.getText(ast) === 'Stack' && attributes.component?.text === 'form') found.direction = literal(attributes.direction.expression);
      if (open.tagName.getText(ast) === 'FormTextField' && ['date', 'status'].includes(attributes.name?.text)) found[attributes.name.text] = literal(attributes.sx.expression);
      if (open.tagName.getText(ast) === 'Button' && attributes.type?.text === 'submit') found.button = literal(attributes.sx.expression);
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return found;
}

function fixture() {
  const api = transport();
  api.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  api.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'synthetic', facilities: [] } }));
  api.on('get', '/orders/daily-output-context', call => ({ data: {
    date: call.config.params.date, status: call.config.params.status || null, facility: null,
    sections: Object.fromEntries((call.config.params.section === 'primary' ? ['orders', 'meal_counts']
      : call.config.params.section === 'bags' ? ['daily_bags', 'daily_bags_audit'] : ['totals']).map(name => [name,
        { status: 'fulfilled', data: name === 'orders' ? { orders: [] } : name === 'totals' ? { rows: [] } : { groups: [] } }])) } }));
  return api;
}

test('same filter uses mobile column and desktop equal shrinkable inputs without shrinking/wrapping action', () => {
  assert.deepEqual(policy(), {
    direction: { xs: 'column', sm: 'row' },
    date: { flex: { sm: '1 1 0' }, minWidth: 0 },
    status: { flex: { sm: '1 1 0' }, minWidth: 0 },
    button: { flexShrink: 0, whiteSpace: 'nowrap' },
  });
});

test('actual RHF/MUI CSS, filter submission and pending/disabled labels remain coherent', async () => {
  const api = fixture();
  const h = await mount('src/pages/daily-delivery-notes.tsx', { api: api.api });
  try {
    const date = h.document.querySelector('input[name="date"]');
    const status = h.document.querySelector('input[name="status"]');
    assert.ok(date && status);
    assert.equal(date.type, 'date');
    const button = h.button('表示・集計');
    assert.equal(button.form, date.form);
    assert.equal(status.form, date.form);
    assert.equal(button.type, 'submit');
    const computed = h.document.defaultView.getComputedStyle(button);
    assert.equal(computed.whiteSpace, 'nowrap');
    assert.equal(computed.flexShrink, '0');
    const rules = [...h.document.styleSheets].flatMap(sheet => [...sheet.cssRules]);
    for (const input of [date, status]) {
      const field = input.closest('.MuiFormControl-root');
      assert.ok(field);
      assert.match(h.document.defaultView.getComputedStyle(field).minWidth, /^0(?:px)?$/);
      const selectors = [...field.classList].map(name => `.${name}`);
      assert.ok(rules.some(rule => rule.conditionText?.includes('min-width:600px')
        && [...rule.cssRules].some(inner => selectors.some(selector => inner.selectorText?.includes(selector))
          && inner.style.flex === '1 1 0')), 'real MUI emits desktop flex basis0 for each input');
    }
    await h.change(h.document.querySelector('input[name="date"]'), '2026-10-19');
    await h.select('status', '要確認');
    const pending = deferred();
    api.on('get', '/orders/daily-output-context', () => pending.promise);
    await h.click(h.button('表示・集計'));
    const pendingButton = h.button('集計中...');
    assert.equal(pendingButton.disabled, true);
    assert.equal(h.document.defaultView.getComputedStyle(pendingButton).whiteSpace, 'nowrap');
    const calls = api.calls.filter(call => call.url === '/orders/daily-output-context');
    assert.deepEqual(calls.map(call => call.config.params.section).sort(), ['bags', 'primary', 'totals']);
    assert.ok(calls.every(call => call.config.params.date === '2026-10-19' && call.config.params.status === '要確認'));
    await h.settle(() => pending.reject(new Error('synthetic guarded stop')));
    await h.waitFor(() => !h.button('表示・集計').disabled);
    assert.equal(h.document.querySelector('input[name="date"]').value, '2026-10-19');
    assert.equal(h.document.querySelector('input[name="status"]').value, '要確認');
  } finally { await h.close(); }
});
