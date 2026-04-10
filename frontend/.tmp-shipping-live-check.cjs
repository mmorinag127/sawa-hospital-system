const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({ viewport: { width: 1440, height: 2000 } });
  await page.goto('https://web-prod-avlnzjjrca-dt.a.run.app/login', { waitUntil: 'networkidle', timeout: 120000 });
  await page.getByLabel('USERNAME').fill('admin');
  await page.getByLabel('PASSWORD').fill('sawa.test.admin.pswd.2026');
  await page.getByRole('button', { name: 'セッションで認証' }).click();
  await page.waitForURL('**/orders', { timeout: 120000 }).catch(() => {});
  await page.goto('https://web-prod-avlnzjjrca-dt.a.run.app/shipping-history', { waitUntil: 'networkidle', timeout: 120000 });
  const firstToggle = page.getByRole('button', { name: '開く' }).first();
  if (await firstToggle.count()) {
    await firstToggle.click();
    await page.waitForTimeout(1000);
  }
  await page.screenshot({ path: '/tmp/shipping-history-live-after.png', fullPage: true });
  const metrics = await page.evaluate(() => {
    const group = document.querySelector('[data-testid="shipping-date-group"]');
    const facility = document.querySelector('[data-testid="shipping-facility-card"]');
    const tracking = document.querySelector('[data-testid="shipping-tracking-card"]');
    const grid = document.querySelector('.facility-slot-grid');
    const facilityStyle = facility ? getComputedStyle(facility) : null;
    const trackingStyle = tracking ? getComputedStyle(tracking) : null;
    const gridStyle = grid ? getComputedStyle(grid) : null;
    return {
      groupClass: group?.className || null,
      gridAlignItems: gridStyle?.alignItems || null,
      gridAutoRows: gridStyle?.gridAutoRows || null,
      facilityBg: facilityStyle?.backgroundColor || null,
      facilityBorder: facilityStyle?.border || null,
      trackingMinHeight: trackingStyle?.minHeight || null,
      trackingPadding: trackingStyle?.padding || null,
      facilityCount: document.querySelectorAll('[data-testid="shipping-facility-card"]').length,
      trackingCount: document.querySelectorAll('[data-testid="shipping-tracking-card"]').length,
    };
  });
  console.log(JSON.stringify(metrics, null, 2));
  await browser.close();
})();
