const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/services/weeklyWeightPeriod.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const context = { exports: {} };
vm.runInNewContext(code, context);
const period = context.exports;
const plain = value => JSON.parse(JSON.stringify(value));
const saved = '2026-09@2026-09-13~2026-09-19';

test('reported saved Sunday week and manual ISO input have the same dates in every timezone', () => {
  const previous = process.env.TZ;
  try {
    for (const zone of ['UTC', 'Asia/Tokyo', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
      process.env.TZ = zone;
      const actual = period.storedWeeklyWeightPeriod(saved);
      assert.deepEqual(plain(actual), { start: '2026-09-13', end: '2026-09-19' });
      assert.equal(period.weekInputForPeriod(actual), '2026-W38');
      assert.deepEqual(plain(period.weeklyWeightPeriod('2026-W38')), plain(actual));
      assert.deepEqual(plain(period.weeklyWeightDates(actual)),
        ['2026-09-13', '2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18', '2026-09-19']);
    }
  } finally { if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous; }
});

test('split-month keys and exact end dates remain distinct, including year and leap boundaries', () => {
  for (const [key, week, length] of [
    ['2026-09@2026-09-27~2026-09-30', '2026-W40', 4],
    ['2026-10@2026-10-01~2026-10-03', '2026-W40', 3],
    ['2020-12@2020-12-27~2021-01-02', '2020-W53', 7],
    ['2021-01@2021-01-03~2021-01-09', '2021-W01', 7],
    ['2024-02@2024-02-29~2024-03-02', '2024-W09', 3],
  ]) {
    const actual = period.storedWeeklyWeightPeriod(key);
    assert.ok(actual, key);
    assert.equal(period.weekInputForPeriod(actual), week);
    assert.equal(period.weeklyWeightDates(actual).length, length);
    assert.equal(period.weeklyWeightConditionsPeriod({ week, savedWeek: key }).end, key.split('~')[1]);
  }
});

test('invalid stored/date/ISO inputs stop rather than defaulting or expanding', () => {
  for (const key of ['', '2026-09', '2026-09@2026-09-13~2026-09-12',
    '2026-09@2026-09-13~2026-09-20', '2026-09@2026-09-19~2026-09-20',
    '2026-10@2026-09-27~2026-10-03', '2026-02@2026-02-29~2026-02-29',
    '2026-09@2026-09-31~2026-09-31', '0000-01@0000-01-01~0000-01-02']) {
    assert.equal(period.storedWeeklyWeightPeriod(key), null, key);
  }
  for (const key of ['', '2026-W00', '2026-W54', '2021-W53', '2026-W1', '2026-09-13']) {
    assert.equal(period.weeklyWeightPeriod(key), null, key);
  }
  assert.equal(period.businessPeriodForDate('2026-02-29'), null);
  assert.equal(period.weeklyWeightConditionsPeriod({ week: '2026-W37', savedWeek: saved }), null);
});

function setup() {
  const t = transport();
  const orders = Array.from({ length: 5 }, (_, index) => ({ id: `synthetic-${index}`, week_value: saved, line_count: index }));
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/orders', () => ({ data: { orders: [...orders,
    { id: 'split-a', week_value: '2026-09@2026-09-27~2026-09-30' },
    { id: 'split-b', week_value: '2026-10@2026-10-01~2026-10-03' }] } }));
  t.on('get', '/orders/by-line-date', call => ({ data: { orders: call.config.params.date >= '2026-09-13'
    && call.config.params.date <= '2026-09-19' ? orders : [] } }));
  t.on('get', '/outputs/weekly-weight', () => ({ data: new Blob(['synthetic-excel']), headers: {} }));
  return { t, probe: { component: 'WeeklyWeightOutputScreen', bindings: [
    'conditionForm', 'week', 'weekLabel', 'orders', 'loadWeeklyOrders', 'downloadWeeklyWeight'] } };
}
async function choose(h, label) {
  const button = [...h.document.querySelectorAll('.week-chip')].find(node => node.textContent.includes(label));
  assert.ok(button, label);
  await h.click(button);
}

test('real history click lists all five orders and Excel receives the identical applied interval', async () => {
  const { t, probe } = setup();
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe });
  try {
    await h.waitFor(() => h.document.querySelectorAll('.week-chip').length === 3);
    await choose(h, '2026-09-13 - 2026-09-19');
    assert.equal(probe.current.conditionForm.getValues('week'), '2026-W38');
    t.calls.length = 0;
    await h.click(h.button('表示・集計'));
    assert.equal(probe.current.orders.length, 5);
    assert.equal(probe.current.weekLabel, '2026-09-13 から 2026-09-19');
    assert.deepEqual(t.calls.filter(call => call.url === '/orders/by-line-date').map(call => call.config.params.date),
      plain(period.weeklyWeightDates(period.storedWeeklyWeightPeriod(saved))));
    await h.click(h.button('Excel出力'));
    assert.deepEqual(t.calls.at(-1).config.params, { start: '2026-09-13', end: '2026-09-19', status: undefined });
    assert.equal(h.downloads.length, 1);
  } finally { await h.close(); }
});

test('same ISO-week split selections preserve their own list/Excel intervals; manual change clears saved source', async () => {
  const { t, probe } = setup();
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe });
  try {
    await h.waitFor(() => h.document.querySelectorAll('.week-chip').length === 3);
    for (const [start, end, days] of [['2026-09-27', '2026-09-30', 4], ['2026-10-01', '2026-10-03', 3]]) {
      await choose(h, `${start} - ${end}`);
      t.calls.length = 0;
      await h.click(h.button('表示・集計'));
      assert.equal(probe.current.weekLabel, `${start} から ${end}`);
      assert.equal(t.calls.filter(call => call.url === '/orders/by-line-date').length, days);
      await h.click(h.button('Excel出力'));
      assert.deepEqual(t.calls.at(-1).config.params, { start, end, status: undefined });
    }
    await h.change(h.document.querySelector('input[name="week"]'), '2026-W41');
    assert.equal(probe.current.conditionForm.getValues('savedWeek'), '');
    await h.click(h.button('Excel出力'));
    assert.deepEqual(t.calls.at(-1).config.params, { start: '2026-10-04', end: '2026-10-10', status: undefined });
  } finally { await h.close(); }
});

test('same-week different saved interval retires a pending download', async () => {
  const { t, probe } = setup(), pending = deferred();
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe });
  try {
    await h.waitFor(() => h.document.querySelectorAll('.week-chip').length === 3);
    await choose(h, '2026-09-27 - 2026-09-30');
    t.on('get', '/outputs/weekly-weight', () => pending.promise);
    let request;
    await h.settle(() => { request = probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues()); });
    await choose(h, '2026-10-01 - 2026-10-03');
    await h.settle(() => pending.resolve({ data: new Blob(['obsolete']), headers: {} }));
    await request;
    assert.equal(h.downloads.length, 0);
  } finally { await h.close(); }
});

test('date deep link resolves the business week, malformed query cannot dispatch list/output', async () => {
  for (const query of [{ date: '2026-09-13' }, { date: '2026-02-29' }, { week: '2021-W53' },
    { week: saved, date: '2026-09-12' }]) {
    const { t, probe } = setup();
    const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe, routerState: { query } });
    try {
      await h.settle();
      if (query.date === '2026-09-13') {
        assert.equal(probe.current.weekLabel, '2026-09-13 から 2026-09-19');
      } else {
        assert.equal(t.calls.filter(call => call.url === '/orders/by-line-date').length, 0);
        await h.settle(() => probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues()));
        assert.equal(t.calls.filter(call => call.url === '/outputs/weekly-weight').length, 0);
      }
    } finally { await h.close(); }
  }
});
