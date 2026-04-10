const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({ viewport: { width: 1440, height: 2000 } });
  await page.goto('https://web-prod-avlnzjjrca-dt.a.run.app/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('sawa.test.admin.pswd.2026');
  await page.getByRole('button', { name: 'セッションで進む' }).click();
  await page.waitForTimeout(2000);
  await page.goto('https://web-prod-avlnzjjrca-dt.a.run.app/shipping-history', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(4000);
  const open = page.getByRole('button', { name: '開く' }).first();
  if (await open.count()) { await open.click(); await page.waitForTimeout(1000); }
  const summary = page.locator('summary', { hasText: '履歴' }).first();
  const before = await summary.evaluate((el) => {
    const s = getComputedStyle(el);
    return { bg: s.backgroundColor, border: s.borderColor, shadow: s.boxShadow, cursor: s.cursor };
  });
  await summary.hover();
  await page.waitForTimeout(300);
  const after = await summary.evaluate((el) => {
    const s = getComputedStyle(el);
    return { bg: s.backgroundColor, border: s.borderColor, shadow: s.boxShadow, cursor: s.cursor };
  });
  await page.screenshot({ path: '/tmp/shipping-hover-check.png', fullPage: true });
  console.log(JSON.stringify({ before, after }, null, 2));
  await browser.close();
})();
