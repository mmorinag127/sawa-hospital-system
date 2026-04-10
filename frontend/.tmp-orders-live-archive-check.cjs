const { chromium } = require('playwright');
(async() => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1800, height: 1400 } });
  const auth = 'Basic ' + Buffer.from('admin:sawa.test.admin.pswd.2026').toString('base64');
  await page.addInitScript((value) => sessionStorage.setItem('auth_header', value), auth);
  await page.goto('https://web-prod-avlnzjjrca-dt.a.run.app/orders', { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForSelector('.week-group', { timeout: 30000 });
  await page.waitForTimeout(4000);

  const before = await page.evaluate(() => ({
    bulkArchiveText: Array.from(document.querySelectorAll('button')).map((el) => el.textContent?.trim()).find((t) => t?.includes('表示中の週次を一括アーカイブ')) || null,
    toggleCount: document.querySelectorAll('.week-group-toggle').length,
    archiveButtons: Array.from(document.querySelectorAll('.week-group')).map((group) => ({
      title: group.querySelector('h3')?.textContent?.trim() || null,
      buttons: Array.from(group.querySelectorAll('.week-group-header button')).map((el) => el.textContent?.trim()),
      counts: Array.from(group.querySelectorAll('.week-count')).map((el) => el.textContent?.trim()),
    })),
  }));

  const targetTitle = '2026-03 (03/22-03/28)';
  let roundtrip = null;
  const targetGroup = page.locator('.week-group').filter({ hasText: targetTitle }).first();
  if (await targetGroup.count()) {
    page.once('dialog', (d) => d.accept());
    await targetGroup.locator('.week-group-action-archive').click();
    await page.waitForTimeout(1500);
    const afterArchive = await page.evaluate(() => ({
      notice: document.querySelector('.archive-feedback-success')?.textContent?.trim() || null,
      bulkRestoreText: Array.from(document.querySelectorAll('button')).map((el) => el.textContent?.trim()).find((t) => t?.includes('表示中の週次のアーカイブを解除')) || null,
    }));

    const archivedToggle = page.locator('label.checkbox').filter({ hasText: 'アーカイブ済みを表示' }).locator('input');
    if (!(await archivedToggle.isChecked())) await archivedToggle.check();
    await page.waitForTimeout(1000);

    const archivedGroup = page.locator('.week-group').filter({ hasText: targetTitle }).first();
    page.once('dialog', (d) => d.accept());
    await archivedGroup.locator('.week-group-action-restore').click();
    await page.waitForTimeout(1500);
    roundtrip = {
      afterArchive,
      restoreNotice: await page.locator('.archive-feedback-success').first().textContent(),
    };
  }

  await page.screenshot({ path: '/Users/mmorinag/Sawa/2025.12/out/orders-live-archive-all.png', fullPage: true });
  console.log(JSON.stringify({ before, roundtrip }, null, 2));
  await browser.close();
})();
