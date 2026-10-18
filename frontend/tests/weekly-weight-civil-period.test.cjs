const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Execute the actual calendar declarations, without reconstructing their logic.
const source = fs.readFileSync(path.join(__dirname, '../src/pages/weekly-weight-output.tsx'), 'utf8');
const calendarSource = source.slice(source.indexOf('const isoDate'), source.indexOf('const queryValue'));
const calendar = vm.runInNewContext(ts.transpileModule(`${calendarSource}\n({parseCivilDate, dateToWeekValue, weekValueToMonday, weekDatesForValue, todayWeek});`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText, { Date });
const dates = week => Array.from(calendar.weekDatesForValue(week));

function fixture(orders = []) {
  const t = transport();
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/orders', () => ({ data: { orders } }));
  t.on('get', '/orders/by-line-date', () => ({ data: { orders: [{ id: 'synthetic', line_count: 0 }] } }));
  t.on('get', '/outputs/weekly-weight', () => ({ data: new Blob(['synthetic,0']), headers: {} }));
  return t;
}
const probeFor = () => ({ component: 'WeeklyWeightOutputScreen', bindings: [
  'conditionForm', 'week', 'weekLabel', 'weekHistory', 'orders', 'loading', 'message', 'loadWeeklyOrders', 'downloadWeeklyWeight',
] });
const calls = t => t.calls.filter(call => call.url === '/orders/by-line-date');

for (const timezone of ['Asia/Tokyo', 'UTC', 'America/New_York']) {
  test(`${timezone}: strict civil calendar and ISO Monday/business Sunday inverse`, () => {
    const old = process.env.TZ;
    process.env.TZ = timezone;
    try {
      for (const [date, week, sunday] of [
        ['2026-06-28', '2026-W27', '2026-06-28'], ['2026-07-01', '2026-W27', '2026-06-28'],
        ['2020-12-31', '2020-W53', '2020-12-27'], ['2021-01-02', '2020-W53', '2020-12-27'],
        ['2026-12-31', '2026-W53', '2026-12-27'],
        ['2021-01-03', '2021-W01', '2021-01-03'], ['2024-02-29', '2024-W09', '2024-02-25'],
        ['2026-03-08', '2026-W11', '2026-03-08'], ['2026-11-01', '2026-W45', '2026-11-01'],
      ]) {
        assert.equal(calendar.dateToWeekValue(date), week);
        assert.equal(dates(week)[0], sunday);
        assert.equal(dates(week).length, 7);
        for (const day of dates(week)) assert.equal(calendar.dateToWeekValue(day), week);
      }
      assert.equal(calendar.weekValueToMonday('2026-W27'), '2026-06-29');
      for (const invalid of ['2025-W53', '2021-W53', '2026-W00', '2026-W54', '0000-W01', '10000-W01', '2026-W1', '', '9999-W52']) {
        assert.deepEqual(dates(invalid), [], invalid);
      }
      for (const invalid of ['2026-02-29', '2024-02-30', '2026-13-01', '2026-00-01', '2026-04-31', '0000-01-01', '10000-01-01', '2026-7-1', '', 'junk']) {
        assert.equal(calendar.dateToWeekValue(invalid), '', invalid);
      }
      // Exhaust all ISO weeks over leap and ordinary years, including week 53.
      for (let year = 2019; year <= 2030; year++) for (let week = 1; week <= 53; week++) {
        const value = `${year}-W${String(week).padStart(2, '0')}`;
        for (const day of dates(value)) assert.equal(calendar.dateToWeekValue(day), value);
      }
      const realDate = Date;
      const instant = '2026-07-04T22:30:00Z'; // Tokyo is already Sunday, UTC/New York are Saturday.
      class Clock extends realDate { constructor(...args) { super(...(args.length ? args : [instant])); } }
      const today = vm.runInNewContext(ts.transpileModule(`${calendarSource}\ntodayWeek();`, {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
      }).outputText, { Date: Clock });
      assert.equal(today, timezone === 'Asia/Tokyo' ? '2026-W28' : '2026-W27');
    } finally { if (old === undefined) delete process.env.TZ; else process.env.TZ = old; }
  });

  for (const [query, week] of [
    [{ date: '2026-06-28' }, '2026-W27'], [{ date: '2026-07-01' }, '2026-W27'],
    [{ week: '2026-W27' }, '2026-W27'], [{ date: '2021-01-03' }, '2021-W01'],
    [{ date: '2026-12-31' }, '2026-W53'], [{ date: '2026-03-08' }, '2026-W11'],
    [{ date: '2026-11-01' }, '2026-W45'], [{ date: '2024-02-29' }, '2024-W09'],
  ]) {
    test(`${timezone}: actual React/RHF/MUI query ${JSON.stringify(query)} uses exactly seven days and identical output Sunday`, async () => {
      const old = process.env.TZ; process.env.TZ = timezone;
      const t = fixture(), probe = probeFor();
      const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe, routerState: { query, isReady: true } });
      try {
        await h.waitFor(() => calls(t).length === 7 && !probe.current.loading);
        assert.equal(probe.current.conditionForm.getValues().week, week);
        assert.deepEqual(calls(t).map(call => call.config.params.date), dates(week));
        assert.equal(probe.current.weekLabel, `${dates(week)[0]} から ${dates(week)[6]}`);
        assert.equal(probe.current.orders[0].line_count, 0);
        await h.click(h.button('Excel出力'));
        assert.equal(t.calls.find(call => call.url === '/outputs/weekly-weight').config.params.date, dates(week)[0]);
        assert.equal(await h.blobs[0].text(), 'synthetic,0');
      } finally { await h.close(); if (old === undefined) delete process.env.TZ; else process.env.TZ = old; }
    });
  }
}

test('registered Sunday and partial-month history share full period label, selection and requests', async () => {
  const t = fixture([
    { id: 'sunday', week_value: '2026-06@2026-06-28~2026-07-04', week_label: 'old partial label' },
    { id: 'partial', week_value: '2026-07@2026-07-01~2026-07-04' },
    { id: 'invalid', week_value: '2026-02@2026-02-30~2026-03-01' },
  ]), probe = probeFor();
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe, routerState: { query: { week: '2026-W26' }, isReady: true } });
  try {
    await h.waitFor(() => probe.current.weekHistory.length === 1);
    assert.equal(probe.current.weekHistory[0].orderCount, 2);
    assert.equal(probe.current.weekHistory[0].label, '2026-06-28 から 2026-07-04');
    await h.click(h.document.querySelector('.week-chip'));
    assert.equal(probe.current.conditionForm.getValues().week, '2026-W27');
    const before = calls(t).length;
    await h.click(h.button('表示・集計'));
    assert.deepEqual(calls(t).slice(before).map(call => call.config.params.date), dates('2026-W27'));
  } finally { await h.close(); }
});

for (const query of [{ date: '2026-02-30' }, { date: '' }, { week: '2025-W53' }, { week: '' },
  { date: ['2026-07-01', '2026-07-08'] }, { week: '2026-W27', date: '2026-07-08' }]) {
  test(`invalid explicit query blocks, never substitutes today: ${JSON.stringify(query)}`, async () => {
    const t = fixture(), probe = probeFor();
    const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe, routerState: { query, isReady: true } });
    try {
      await h.settle();
      assert.equal(calls(t).length, 0);
      assert.equal(probe.current.conditionForm.getValues().week, '');
      assert.match(probe.current.message, /有効な週/);
      await h.click(h.button('Excel出力'));
      assert.equal(t.calls.filter(call => call.url === '/outputs/weekly-weight').length, 0);
      await h.settle(() => probe.current.conditionForm.setValue('week', '2025-W53'));
      await h.click(h.button('表示・集計')); await h.click(h.button('Excel出力'));
      assert.equal(calls(t).length, 0);
    } finally { await h.close(); }
  });
}

test('router readiness prevents an early today request; deferred old period cannot replace new selection', async () => {
  const t = fixture(), probe = probeFor(), pending = deferred();
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe, routerState: { query: {}, isReady: false } });
  try {
    assert.equal(calls(t).length, 0);
    t.on('get', '/orders/by-line-date', () => pending.promise);
    h.nav.router.isReady = true; h.nav.router.query = { date: '2026-07-01', status: '確定' };
    await h.render();
    assert.deepEqual(calls(t).map(call => call.config.params.date), dates('2026-W27'));
    assert.ok(calls(t).every(call => call.config.params.status === '確定'));
    await h.change(h.document.querySelector('input[name="week"]'), '2026-W28');
    await h.settle(() => pending.resolve({ data: { orders: [{ id: 'obsolete' }] } }));
    assert.deepEqual(probe.current.orders, []);
    assert.equal(probe.current.week, '');
    t.on('get', '/orders/by-line-date', () => ({ data: { orders: [] } }));
    await h.click(h.button('表示・集計'));
    assert.deepEqual(calls(t).slice(-7).map(call => call.config.params.date), dates('2026-W28'));
    assert.equal(probe.current.week, '2026-W28');
  } finally { await h.close(); }
});

for (const query of [{ date: '2026-07-01' }, { date: '2026-02-30' }, { date: '' },
  { week: '2025-W53' }, { week: '2026-W27', date: '2026-07-08' }]) {
  test(`unready actual form buttons/submit/handlers cannot use today; followup ${JSON.stringify(query)}`, async () => {
    const t = fixture(), probe = probeFor();
    const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe,
      routerState: { query, isReady: false } });
    try {
      assert.equal(h.button('表示・集計').disabled, true);
      assert.equal(h.button('Excel出力').disabled, true);
      await h.click(h.button('表示・集計')); await h.click(h.button('Excel出力'));
      await h.settle(() => h.document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
      await h.settle(async () => {
        await probe.current.loadWeeklyOrders(probe.current.conditionForm.getValues());
        await probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues());
      });
      assert.equal(calls(t).length, 0);
      assert.equal(t.calls.filter(call => call.url === '/outputs/weekly-weight').length, 0);
      assert.equal(h.downloads.length, 0);
      h.nav.router.isReady = true; await h.render();
      const valid = query.date === '2026-07-01';
      assert.equal(calls(t).length, valid ? 7 : 0);
      if (!valid) {
        await h.click(h.button('表示・集計')); await h.click(h.button('Excel出力'));
        assert.equal(calls(t).length, 0);
        assert.equal(h.downloads.length, 0);
      }
      // A resolved bad URL is not a permanent lock: a valid operator correction works.
      await h.change(h.document.querySelector('input[name="week"]'), '2026-W28');
      const previous = calls(t).length;
      await h.click(h.button('表示・集計'));
      assert.deepEqual(calls(t).slice(previous).map(call => call.config.params.date), dates('2026-W28'));
      await h.click(h.button('Excel出力'));
      assert.equal(t.calls.find(call => call.url === '/outputs/weekly-weight').config.params.date, '2026-07-05');
      assert.equal(h.downloads.length, 1);
    } finally { await h.close(); }
  });
}

for (const nextQuery of [{ date: '2026-07-08' }, { date: '2026-02-30' }]) {
  test(`in-place query change rejects stale commands before passive resolution: ${JSON.stringify(nextQuery)}`, async () => {
    const t = fixture(), probe = probeFor();
    let beforeEffects;
    const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe,
      routerState: { query: { date: '2026-07-01' }, isReady: true },
      component(loader, React) {
        const Page = loader.load('src/pages/weekly-weight-output.tsx', probe).default;
        return function LayoutBoundary() {
          React.useLayoutEffect(() => { const check = beforeEffects; beforeEffects = null; check?.(); });
          return React.createElement(Page);
        };
      },
    });
    try {
      await h.waitFor(() => calls(t).length === 7);
      const prior = calls(t).length;
      const stale = probe.current;
      // Mutate the same query object, keeping asPath identical, before any new effects.
      h.nav.router.query.date = nextQuery.date;
      await h.settle(async () => {
        await stale.loadWeeklyOrders(stale.conditionForm.getValues());
        await stale.downloadWeeklyWeight(stale.conditionForm.getValues());
      });
      assert.equal(calls(t).length, prior);
      assert.equal(h.downloads.length, 0);
      let checked = false;
      beforeEffects = () => {
        checked = true;
        assert.equal(h.button('表示・集計').disabled, true);
        assert.equal(h.button('Excel出力').disabled, true);
        void probe.current.loadWeeklyOrders(probe.current.conditionForm.getValues());
        void probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues());
        assert.equal(calls(t).length, prior);
      };
      await h.render();
      assert.equal(checked, true);
      if (nextQuery.date === '2026-07-08') {
        assert.deepEqual(calls(t).slice(prior).map(call => call.config.params.date), dates('2026-W28'));
        await h.click(h.button('Excel出力'));
        assert.equal(t.calls.find(call => call.url === '/outputs/weekly-weight').config.params.date, '2026-07-05');
      } else {
        assert.equal(calls(t).length, prior);
        await h.click(h.button('Excel出力'));
        assert.equal(h.downloads.length, 0);
      }
    } finally { await h.close(); }
  });
}

for (const ready of [true, false]) for (const kind of ['list', 'output']) for (const failed of [false, true]) {
  test(`fresh public router snapshot blocks old ${kind} ${failed ? 'error' : 'success'} resolved in layout; ready=${ready}`, async () => {
    const t = fixture(), probe = probeFor(), pending = deferred();
    let inLayout;
    const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe,
      routerState: { query: { date: '2026-07-01' }, isReady: true },
      component(loader, React) {
        const Page = loader.load('src/pages/weekly-weight-output.tsx', probe).default;
        return function SnapshotBoundary() {
          React.useLayoutEffect(() => { const check = inLayout; inLayout = null; check?.(); });
          return React.createElement(Page);
        };
      },
    });
    try {
      await h.waitFor(() => calls(t).length === 7 && probe.current.orders.length === 1);
      const originalRouter = h.nav.router, originalQuery = originalRouter.query;
      const saved = probe.current;
      const endpoint = kind === 'list' ? '/orders/by-line-date' : '/outputs/weekly-weight';
      t.on('get', endpoint, () => pending.promise);
      let operation;
      await h.settle(() => { operation = kind === 'list'
        ? saved.loadWeeklyOrders(saved.conditionForm.getValues())
        : saved.downloadWeeklyWeight(saved.conditionForm.getValues()); operation.catch(() => {}); });
      const beforeList = calls(t).length;
      const beforeOutput = t.calls.filter(call => call.url === '/outputs/weekly-weight').length;
      // Next replaces the public router and query objects. Neither old object is mutated.
      // Same business week deliberately keeps RHF values unchanged during query reset.
      const replacement = { ...originalRouter, query: { date: '2026-07-02' }, isReady: ready };
      h.nav.imports.get('next/router').useRouter = () => replacement;
      t.on('get', '/orders/by-line-date', () => ({ data: { orders: [{ id: 'current-snapshot' }] } }));
      inLayout = () => {
        void saved.loadWeeklyOrders(saved.conditionForm.getValues());
        void saved.downloadWeeklyWeight(saved.conditionForm.getValues());
        assert.equal(calls(t).length, beforeList);
        assert.equal(t.calls.filter(call => call.url === '/outputs/weekly-weight').length, beforeOutput);
        if (failed) pending.reject({ response: { data: { detail: 'obsolete-snapshot-error' } } });
        else pending.resolve({ data: kind === 'list' ? { orders: [{ id: 'obsolete-snapshot-row' }] } : new Blob(['obsolete-snapshot-bytes']), headers: {} });
      };
      await h.render(); await operation.catch(() => {}); await h.settle();
      assert.equal(originalRouter.query, originalQuery);
      assert.deepEqual(originalQuery, { date: '2026-07-01' });
      assert.equal(originalRouter.isReady, true);
      assert.equal(h.downloads.length, 0);
      assert.doesNotMatch(probe.current.message, /obsolete-snapshot/);
      assert.ok(probe.current.orders.every(row => row.id !== 'obsolete-snapshot-row'));
      if (ready) assert.equal(probe.current.orders[0].id, 'current-snapshot');
      else {
        assert.equal(h.button('表示・集計').disabled, true);
        assert.equal(h.button('Excel出力').disabled, true);
        h.nav.imports.get('next/router').useRouter = () => ({ ...replacement, isReady: true });
        t.on('get', '/outputs/weekly-weight', () => ({ data: new Blob(['restored-owner-bytes']), headers: {} }));
        await h.render();
        await h.click(h.button('Excel出力'));
        assert.equal(h.downloads.length, 1);
        assert.equal(await h.blobs[0].text(), 'restored-owner-bytes');
      }
    } finally { await h.close(); }
  });
}

for (const kind of ['list', 'output']) {
  test(`old saved ${kind} handler cannot supersede pending newer snapshot ownership`, async () => {
    const t = fixture(), probe = probeFor(), pending = deferred();
    const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe,
      routerState: { query: { date: '2026-07-01' }, isReady: true } });
    try {
      const old = probe.current, originalRouter = h.nav.router;
      const replacement = { ...originalRouter, query: { date: '2026-07-08' } };
      h.nav.imports.get('next/router').useRouter = () => replacement;
      t.on('get', '/orders/by-line-date', () => pending.promise);
      await h.render();
      let currentOutput;
      if (kind === 'output') {
        t.on('get', '/outputs/weekly-weight', () => pending.promise);
        await h.settle(() => { currentOutput = probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues()); });
      }
      const before = t.calls.length;
      await h.settle(() => {
        void old.loadWeeklyOrders(probe.current.conditionForm.getValues()).catch(() => {});
        void old.downloadWeeklyWeight(probe.current.conditionForm.getValues());
      });
      assert.equal(t.calls.length, before);
      await h.settle(() => pending.resolve({ data: kind === 'list'
        ? { orders: [{ id: 'new-owned-row' }] } : new Blob(['new-owned-bytes']), headers: {} }));
      if (kind === 'list') assert.equal(probe.current.orders[0].id, 'new-owned-row');
      else {
        await currentOutput;
        assert.equal(h.downloads.length, 1);
        assert.equal(await h.blobs[0].text(), 'new-owned-bytes');
      }
      assert.deepEqual(originalRouter.query, { date: '2026-07-01' });
    } finally { await h.close(); }
  });
}
