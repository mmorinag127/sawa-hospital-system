// Isolated DOM/API component cases, not actual upload/OCR/registration or business-oracle proof.
const test = require('node:test'), assert = require('node:assert/strict');
const { mount, deferred } = require('./helpers/pdf-upload-selection.cjs');

function fixture() {
  const reads = [], posts = [], resolutions = [], saves = [];
  const api = {
    get(url, options) {
      reads.push([url, options]);
      if (url === '/facilities') return Promise.resolve({ data: { facilities: [{ id: 'F', name: 'Component F' }, { id: 'G', name: 'Component G' }] } });
      if (url === '/ingest/uploads') return Promise.resolve({ data: { items: [] } });
      assert.match(url, /^\/facilities\/[FG]\/resolved$/);
      const reply = deferred(); resolutions.push({ url, date: options.params.effective_date, ...reply });
      return reply.promise;
    },
    post(url, data) {
      assert.equal(url, '/ingest/upload');
      posts.push({ url, fields: Object.fromEntries(data.entries()) });
      const reply = deferred(); saves.push(reply); return reply.promise;
    },
  };
  return { api, reads, posts, resolutions, saves };
}

function resolved(request, version = 'authored-A', revision = 'fixture-revision') {
  return { data: { source: 'canonical_json', facility_id: request.url.split('/')[2],
    effective_date: request.date, revision, version: { version_id: version } } };
}

async function conditions(ui) {
  await ui.change(ui.document.querySelector('input[type="month"]'), '2026-01');
  await ui.select('facility_hint', 'F');
  await ui.select('week_hint', '2026-01@2026-01-01~2026-01-03');
  await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-01-02');
  await ui.file();
}

test('explicit date/confirmation, input invalidation, period bounds and stale preview response', async () => {
  const f = fixture(), ui = await mount(f.api);
  try {
    assert.equal(ui.document.querySelector('input[name="effective_date"]').value, '');
    assert.equal(ui.submit().disabled, true);
    await conditions(ui);
    const old = f.resolutions.at(-1);
    await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-01-03');
    const current = f.resolutions.at(-1);
    await ui.settle(() => current.resolve(resolved(current, 'authored-current')));
    await ui.settle(() => old.resolve(resolved(old, 'authored-stale')));
    assert.ok(ui.document.body.textContent.includes('authored-current'));
    assert.equal(ui.document.body.textContent.includes('authored-stale'), false);
    assert.equal(ui.submit().disabled, true);
    await ui.click(ui.document.querySelector('form input[type="checkbox"]'));
    assert.equal(ui.submit().disabled, false);
    await ui.select('facility_hint', 'G');
    assert.equal(ui.submit().disabled, true);
    assert.equal(ui.document.body.textContent.includes('authored-current'), false);
    const next = f.resolutions.at(-1);
    await ui.settle(() => next.resolve(resolved(next)));
    await ui.click(ui.document.querySelector('form input[type="checkbox"]'));
    await ui.select('week_hint', '2026-01@2026-01-04~2026-01-10');
    assert.equal(ui.submit().disabled, true);
    assert.ok(ui.document.body.textContent.includes('適用日は対象週の期間内'));
    assert.equal(ui.document.querySelector('input[name="effective_date"]').value, '2026-01-03');
    assert.equal(f.posts.length, 0);
  } finally { await ui.close(); }
});

test('confirmed capture sent unchanged once, failed save shown without fabricating acceptance', async () => {
  const f = fixture(), ui = await mount(f.api);
  try {
    await conditions(ui);
    const request = f.resolutions.at(-1);
    await ui.settle(() => request.resolve(resolved(request)));
    await ui.click(ui.submit());
    assert.equal(f.posts.length, 0);
    await ui.click(ui.document.querySelector('form input[type="checkbox"]'));
    await ui.click(ui.submit());
    assert.equal(ui.submit().disabled, true);
    await ui.click(ui.submit());
    assert.equal(f.posts.length, 1);
    assert.equal(f.posts[0].fields.facility_hint, 'F');
    assert.equal(f.posts[0].fields.week_hint, '2026-01@2026-01-01~2026-01-03');
    assert.equal(f.posts[0].fields.effective_date, '2026-01-02');
    assert.equal(f.posts[0].fields.selected_version_id, 'authored-A');
    assert.equal(f.posts[0].fields.expected_canonical_revision, 'fixture-revision');
    await ui.settle(() => f.saves[0].reject({ response: { data: { detail: 'canonical_revision_changed' } } }));
    assert.ok(ui.document.body.textContent.includes('canonical_revision_changed'));
    assert.equal(ui.document.body.textContent.includes('受付しました。'), false);
    assert.equal(ui.document.querySelector('input[name="effective_date"]').value, '2026-01-02');
    assert.equal(f.posts.length, 1);
  } finally { await ui.close(); }
});

test('noncanonical or failed resolution never authorizes submit', async () => {
  for (const failure of ['foreign-source', 'missing-revision', 'rejected']) {
    const f = fixture(), ui = await mount(f.api);
    try {
      await conditions(ui);
      const request = f.resolutions.at(-1), response = resolved(request);
      if (failure === 'foreign-source') response.data.source = 'database';
      if (failure === 'missing-revision') response.data.revision = null;
      await ui.settle(() => failure === 'rejected' ? request.reject(new Error('unresolved')) : request.resolve(response));
      assert.equal(ui.submit().disabled, true);
      assert.equal(f.posts.length, 0);
      assert.equal(ui.document.querySelector('input[name="effective_date"]').value, '2026-01-02');
    } finally { await ui.close(); }
  }
});
