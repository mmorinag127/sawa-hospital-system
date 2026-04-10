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
  const debug = await page.evaluate(() => {
    const summaries = Array.from(document.querySelectorAll('summary')).slice(0, 10).map((el) => ({
      text: el.textContent,
      className: el.className,
      outer: el.outerHTML,
      style: {
        bg: getComputedStyle(el).backgroundColor,
        border: getComputedStyle(el).borderColor,
        cursor: getComputedStyle(el).cursor,
      },
    }));
    return { summaries };
  });
  console.log(JSON.stringify(debug, null, 2));
  await browser.close();
})();
