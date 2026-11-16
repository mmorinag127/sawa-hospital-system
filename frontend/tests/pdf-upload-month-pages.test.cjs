// Real upload component with synthetic preview/API replies; not live OCR evidence.
const test = require('node:test'), assert = require('node:assert/strict');
const { mount, deferred } = require('./helpers/pdf-upload-selection.cjs');

function fixture() {
  const previews = [], uploads = [];
  return { previews, uploads, api: {
    get(url, options) {
      if (url === '/facilities') return Promise.resolve({ data: { facilities: [{ id: 'F', name: 'Component F' }] } });
      if (url === '/ingest/uploads') return Promise.resolve({ data: { items: [] } });
      return Promise.resolve({ data: { source: 'canonical_json', facility_id: 'F',
        effective_date: options.params.effective_date, revision: 'revision', version: { version_id: 'version' } } });
    },
    post(url, form) {
      const reply = deferred(), fields = Object.fromEntries(form.entries());
      if (url === '/ingest/upload-preview') previews.push({ fields, ...reply });
      else { assert.equal(url, '/ingest/upload'); uploads.push({ fields, ...reply }); }
      return reply.promise;
    },
  } };
}
const button = (ui, text) => [...ui.document.querySelectorAll('button')].find(node => node.textContent.includes(text));
const pages = ui => [...ui.document.querySelectorAll('fieldset.fax-pages input[type="checkbox"]')];
function preview(request) {
  const november = request.fields.week_hint.startsWith('2026-11');
  return { data: { facility_id: 'F', week_hint: request.fields.week_hint,
    date_from: november ? '2026-11-29' : '2026-12-01', date_to: november ? '2026-11-30' : '2026-12-05',
    calendar_from: '2026-11-29', calendar_to: '2026-12-05', cross_month: true, requires_review: true,
    files: [{ file_index: 0, filename: 'component.bin', sha256: 'a'.repeat(64), pages: [1, 2].map(number => ({
      page_number: number, sha256: String(number).repeat(64), image_url: 'data:image/jpeg;base64,component-only',
    })) }] } };
}
async function ready(ui) {
  await ui.change(ui.document.querySelector('input[type="month"]'), '2026-11');
  await ui.select('facility_hint', 'F');
  await ui.select('week_hint', '2026-11@2026-11-29~2026-11-30');
  await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-11-29');
  await ui.file();
  await ui.click(ui.document.querySelector('form input[type="checkbox"]'));
}

test('monthly pages are explicit and the exact original hashes and monthly context are submitted', async () => {
  const f = fixture(), ui = await mount(f.api);
  try {
    await ready(ui);
    assert.equal(ui.submit().disabled, true);
    await ui.click(button(ui, '原本ページを確認'));
    await ui.settle(() => f.previews[0].resolve(preview(f.previews[0])));
    assert.deepEqual(pages(ui).map(node => node.checked), [false, false]);
    await ui.click(ui.submit());
    assert.equal(f.uploads.length, 0);
    assert.ok(ui.document.body.textContent.includes('対象期間が記入されたページを選択'));
    await ui.click(pages(ui)[0]);
    await ui.click(ui.submit());
    assert.equal(f.uploads.length, 1);
    const plan = JSON.parse(f.uploads[0].fields.page_selection);
    assert.equal(plan.week_hint, '2026-11@2026-11-29~2026-11-30');
    assert.equal(plan.facility_id, 'F');
    assert.equal(plan.files[0].sha256, 'a'.repeat(64));
    assert.deepEqual(plan.files[0].pages, [
      { page_number: 1, sha256: '1'.repeat(64), include: true },
      { page_number: 2, sha256: '2'.repeat(64), include: false },
    ]);
    await ui.settle(() => f.uploads[0].reject({ response: { data: { detail: 'fax_existing_order_requires_review' } } }));
    assert.ok(ui.document.body.textContent.includes('原本を自動で差し替えず'));
    assert.equal(ui.document.body.textContent.includes('受付しました。'), false);
  } finally { await ui.close(); }
});

test('changing month or file invalidates page review, including a late preview response', async () => {
  const f = fixture(), ui = await mount(f.api);
  try {
    await ready(ui);
    await ui.click(button(ui, '原本ページを確認'));
    await ui.file();
    await ui.settle(() => f.previews[0].resolve(preview(f.previews[0])));
    assert.equal(pages(ui).length, 0);
    assert.equal(ui.submit().disabled, true);
    await ui.click(button(ui, '原本ページを確認'));
    await ui.settle(() => f.previews[1].resolve(preview(f.previews[1])));
    await ui.click(pages(ui)[0]);
    await ui.change(ui.document.querySelector('input[type="month"]'), '2026-12');
    await ui.select('week_hint', '2026-12@2026-12-01~2026-12-05');
    await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-12-01');
    assert.equal(pages(ui).length, 0);
    assert.equal(ui.submit().disabled, true);
    await ui.click(ui.document.querySelector('form input[type="checkbox"]'));
    await ui.click(button(ui, '原本ページを確認'));
    await ui.settle(() => f.previews[2].resolve(preview(f.previews[2])));
    assert.deepEqual(pages(ui).map(node => node.checked), [false, false]);
    await ui.click(pages(ui)[1]);
    await ui.click(ui.submit());
    assert.equal(f.uploads.length, 1);
    const plan = JSON.parse(f.uploads[0].fields.page_selection);
    assert.equal(plan.week_hint, '2026-12@2026-12-01~2026-12-05');
    assert.deepEqual(plan.files[0].pages.map(page => page.include), [false, true]);
    await ui.settle(() => f.uploads[0].reject(new Error('component-only-stop')));
  } finally { await ui.close(); }
});
