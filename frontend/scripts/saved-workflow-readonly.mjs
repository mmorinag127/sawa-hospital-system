import assert from 'node:assert/strict';

export function savedWorkflowContract({ workflow, inspection, order }, orderId) {
  assert.equal(workflow?.order_id, orderId);
  assert.equal(order?.id, orderId);
  assert.equal(inspection?.order_id, orderId);
  // Standalone workflow adds menu_amount_resolution/context_suggestion and OCR job detail.
  // Compare the shared persisted ownership/version fields, not those distinct projections.
  for (const key of ['order_id', 'revision', 'facility_id', 'week_start', 'week_end',
    'template_id', 'template_version_id', 'saved_sheet_id', 'selected_ocr_result_id', 'state']) {
    assert.deepEqual(inspection?.workflow?.[key], workflow[key]);
  }
  assert.ok(Number.isInteger(workflow.revision) && workflow.revision > 0);
  assert.ok(typeof workflow.facility_id === 'string' && workflow.facility_id.length);
  for (const date of [workflow.week_start, workflow.week_end]) {
    assert.match(date, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(new Date(date).toISOString().slice(0, 10), date);
  }
  assert.ok(workflow.week_start <= workflow.week_end);
  const saved = inspection.saved_sheet;
  assert.ok(saved && typeof saved.saved_sheet_id === 'string' && saved.saved_sheet_id.length);
  assert.equal(saved.saved_sheet_id, workflow.saved_sheet_id);
  const sheet = saved.sheet;
  assert.ok(sheet && Array.isArray(sheet.fields) && sheet.fields.length);
  assert.ok(Array.isArray(sheet.header) && sheet.header.length === sheet.fields.length);
  assert.ok(Array.isArray(sheet.rows) && sheet.rows.length);
  assert.ok(sheet.fields.every(value => typeof value === 'string'));
  assert.ok(sheet.header.every(value => typeof value === 'string'));
  assert.ok(sheet.rows.every(row => Array.isArray(row) && row.length === sheet.fields.length
    && row.every(cell => cell === null || ['string', 'number', 'boolean'].includes(typeof cell))));
  assert.ok(Array.isArray(order.versions));
  const document = order.current_version;
  assert.ok(document && typeof document.document_id === 'string' && document.document_id.length);
  assert.deepEqual(order.versions.filter(row => row.document_id === document.document_id), [document]);
  assert.equal(document.is_current, true);
  assert.equal(order.document_id, document.document_id);
  assert.ok(Number.isInteger(document.version_no) && document.version_no > 0);
  assert.equal(order.version_count, order.versions.length);
  return { saved, sheet, document };
}

// All listeners are armed before the one real navigation, including the later PDF GET.
export async function readSavedWorkflow({ page, origin, orderId, action, mark }) {
  assert.match(orderId, /^[A-Za-z0-9_-]+$/);
  const base = `/api/orders/${orderId}`;
  const specs = [[base + '/workflow-v2/display', 'workflow'],
    [base + '/workflow-v2/display', 'inspection'], [base + '/workflow-v2/display', 'order'], [base + '/document', 'pdf']];
  const pending = specs.map(([path, name]) => page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.origin === origin && url.pathname === path && response.request().method() === 'GET'
      && (name === 'pdf' || url.searchParams.getAll('scope').join() === (name === 'inspection' ? 'sheet' : 'initial'));
  }).then(async response => {
    mark(`saved-${name}-http`);
    assert.equal(response.status(), 200);
    mark(`saved-${name}-body`);
    if (name !== 'pdf') {
      const payload = await response.json();
      return [name, name === 'workflow' ? payload.workflow : name === 'order' ? payload.order_header : payload];
    }
    const body = await response.body();
    assert.ok(body.length > 5);
    assert.equal(body.subarray(0, 5).toString(), '%PDF-');
    return [name, { documentIds: new URL(response.url()).searchParams.getAll('document_id') }];
  }));
  const ready = Promise.all(pending).then(values => ({ values }), () => ({ failed: true }));
  await action();
  await pending[0];
  await page.locator('.step-nav button').filter({hasText: 'PDF/施設/週次'}).click();
  await pending[3];
  await page.locator('.step-nav button').filter({hasText: 'シート編集'}).click();
  const outcome = await ready;
  mark('saved-responses-complete');
  assert.equal(outcome.failed, undefined);
  const data = Object.fromEntries(outcome.values);
  mark('saved-contract');
  const contract = savedWorkflowContract(data, orderId);
  mark('saved-pdf-identity');
  assert.deepEqual(data.pdf.documentIds, [contract.document.document_id]);
  return { ...data, ...contract };
}

export async function assertSavedCells({ page, expect, sheet, mark }) {
  const table = page.locator('table.sheet-table');
  mark('saved-sheet-visible');
  await expect(table).toBeVisible();
  mark('saved-sheet-headers');
  assert.deepEqual(await table.locator('thead th').allTextContents(),
    ['#', ...sheet.header.map((label, index) => label || sheet.fields[index])]);
  mark('saved-sheet-row-count');
  await expect(table.locator('tbody tr')).toHaveCount(sheet.rows.length);
  mark('saved-sheet-cell-values');
  const actual = await table.locator('tbody tr').evaluateAll(rows => rows.map(row =>
    Array.from(row.querySelectorAll('input[data-sheet-row][data-sheet-col]'), input => input.value)));
  // HTML input values are strings; only the UI's explicit null/string conversion is applied.
  assert.deepEqual(actual, sheet.rows.map(row => row.map(cell => String(cell ?? ''))));
}

export function assertSavedInspectionDisplay(text, inspection) {
  // refreshAll removes only the transport sidecar before setting the displayed inspection.
  const displayed = { ...inspection };
  delete displayed.ocr_candidates;
  assert.deepEqual(JSON.parse(text), displayed);
}

export async function verifySavedWorkflowPages({ page, expect, origin, orderId, check, receipt, navigate, mark }) {
  async function open() {
    await receipt('/api/orders', () => navigate('/hospital/orders'));
    const toggles = page.locator('button.week-group-toggle');
    for (let i = 0; i < await toggles.count(); i++) {
      if (await toggles.nth(i).getAttribute('aria-expanded') === 'false') await toggles.nth(i).click();
    }
    mark('saved-order-link');
    const link = page.locator(`a[href="/orders/${orderId}/workflow-v2"]:visible`);
    await expect(link).toHaveCount(1);
    return readSavedWorkflow({ page, origin, orderId, mark, action: () => link.click() });
  }
  async function version(data) {
    mark('saved-sheet-version-visible');
    await expect(page.locator('.lineage-summary .summary-primary-card')
      .filter({ has: page.locator('span', { hasText: /^保存シート$/ }) }).locator('.summary-value'))
      .toHaveText(data.saved.saved_sheet_id);
    // Compare the existing read-only inspection display, without leaving raw URLs open in PNGs.
    const details = page.locator('details').filter({ has: page.locator('summary', { hasText: /^デバッグ用 raw JSON を開く$/ }) });
    const rawResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.origin === origin && url.pathname === `/api/orders/${orderId}/workflow-v2/inspection`
        && url.searchParams.getAll('history_details').join() === 'true' && response.request().method() === 'GET';
    });
    await details.locator('summary').click();
    try {
      const response = await rawResponse;
      assert.equal(response.status(), 200);
      const raw = await response.json();
      const rawContract = savedWorkflowContract({...data, inspection: raw}, orderId);
      assert.deepEqual(rawContract.saved, data.saved);
      mark('saved-inspection-display-exact');
      assertSavedInspectionDisplay(await details.locator('pre').innerText(), raw);
    } finally { await details.locator('summary').click(); }
  }
  await check('saved-workflow-step1', async () => {
    const data = await open();
    mark('saved-step1-select');
    await page.locator('nav[aria-label="workflow steps"] button').filter({ has: page.locator('span', { hasText: /^Step1$/ }) }).click();
    await expect(page.getByRole('heading', { name: '注文書 (FAX PDF)', exact: true })).toBeVisible();
    mark('saved-facility-value');
    await expect(page.getByLabel('施設 (Step1 必須)', { exact: true })).toHaveValue(data.workflow.facility_id);
    mark('saved-week-value');
    await expect(page.getByLabel('週 (Step1 必須)', { exact: true }))
      .toHaveValue(`${data.workflow.week_start.slice(0, 7)}@${data.workflow.week_start}~${data.workflow.week_end}`);
    mark('saved-pdf-visible');
    await expect(page.getByRole('link', { name: '原本を開く', exact: true })).toHaveAttribute('href', /^blob:/);
    mark('saved-pdf-version');
    await expect(page.locator('.summary-primary-card').filter({ has: page.locator('span', { hasText: /^FAX version$/ }) })
      .locator('.summary-value')).toHaveText(`v${data.document.version_no} / ${data.order.version_count}版`);
    await version(data);
  });
  await check('saved-workflow-step3', async () => {
    const data = await open();
    mark('saved-step3-select');
    await page.locator('nav[aria-label="workflow steps"] button').filter({ has: page.locator('span', { hasText: /^Step3$/ }) }).click();
    await assertSavedCells({ page, expect, sheet: data.sheet, mark });
    await version(data);
  });
}
