import { after } from 'node:test';
import { createRequire } from 'node:module';

const { webkit } = createRequire(import.meta.url)('@playwright/test');
let browser;

after(async () => {
  if (browser) await (await browser).close();
});

export async function isolatedPage(run) {
  browser ??= webkit.launch();
  const context = await (await browser).newContext();
  try {
    const page = await context.newPage();
    await page.route('**/*', route => route.abort());
    return await run(page);
  } finally {
    await context.close();
  }
}
