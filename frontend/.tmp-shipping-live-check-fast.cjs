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
  const firstToggle = page.getByRole('button', { name: '開く' }).first();
  if (await firstToggle.count()) {
    await firstToggle.click();
    await page.waitForTimeout(1000);
  }
  await page.screenshot({ path: '/tmp/shipping-history-live-after-fast.png', fullPage: true });
  const metrics = await page.evaluate(() => {
    const group = document.querySelector('[data-testid="shipping-date-group"]');
    const facility = document.querySelector('[data-testid="shipping-facility-card"]');
    const tracking = document.querySelector('[data-testid="shipping-tracking-card"]');
    const grid = document.querySelector('.facility-slot-grid');
    const facilityStyle = facility ? getComputedStyle(facility) : null;
    const trackingStyle = tracking ? getComputedStyle(tracking) : null;
    const gridStyle = grid ? getComputedStyle(grid) : null;
    return {
      title: document.querySelector('h1')?.textContent || null,
      url: location.href,
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
