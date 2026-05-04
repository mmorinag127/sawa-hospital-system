const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");

const orderId = process.argv[2] || "ORD4cfa1982";
const outDir = path.resolve(process.cwd(), "..", "tmp", "stg_order_page_capture");
const outPath = path.join(outDir, `${orderId}_order_page.png`);
const summaryPath = path.join(outDir, `${orderId}_order_page_summary.json`);

function loadOperatorCredentials() {
  const raw = execFileSync(
    "gcloud",
    [
      "run",
      "services",
      "describe",
      "worker-stg",
      "--project=sawahospitalsystem",
      "--region=asia-northeast2",
      "--format=json",
    ],
    { encoding: "utf8" },
  );
  const service = JSON.parse(raw);
  const containers = service?.spec?.template?.spec?.containers || [];
  const env = {};
  for (const container of containers) {
    for (const item of container.env || []) {
      if (Object.prototype.hasOwnProperty.call(item, "value")) {
        env[String(item.name)] = String(item.value);
      }
    }
  }
  if (!env.OPERATOR_USER || !env.OPERATOR_PASSWORD) {
    throw new Error("operator credentials are unavailable");
  }
  return { username: env.OPERATOR_USER, password: env.OPERATOR_PASSWORD };
}

async function main() {
  fs.mkdirSync(outDir, { recursive: true });
  const credentials = loadOperatorCredentials();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    httpCredentials: credentials,
    viewport: { width: 1920, height: 1200 },
  });
  const basicToken = Buffer.from(`${credentials.username}:${credentials.password}`, "utf8").toString("base64");
  await context.addInitScript((token) => {
    window.sessionStorage.setItem("auth_header", `Basic ${token}`);
    window.sessionStorage.setItem("auth_next", window.location.pathname + window.location.search);
  }, basicToken);
  const page = await context.newPage();
  const started = Date.now();
  await page.goto(`https://web-stg-avlnzjjrca-dt.a.run.app/orders/${orderId}`, {
    waitUntil: "domcontentloaded",
    timeout: 90000,
  });
  if (page.url().includes("/login")) {
    await page.locator('input[name="username"], input[autocomplete="username"]').first().fill(credentials.username);
    await page.locator('input[name="password"], input[type="password"]').first().fill(credentials.password);
    await page.getByRole("button", { name: /セッションで進む|ログイン|Sign in/i }).last().click();
    await page.waitForURL(`**/orders/${orderId}`, { timeout: 90000 }).catch(() => {});
  }
  await page.waitForLoadState("networkidle", { timeout: 90000 }).catch(() => {});
  const title = await page.title().catch(() => "");
  const bodyText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => "");
  await page.screenshot({ path: outPath, fullPage: false });
  await browser.close();
  const elapsedSeconds = (Date.now() - started) / 1000;
  const summary = {
    order_id: orderId,
    elapsed_seconds: Number(elapsedSeconds.toFixed(3)),
    title,
    url: page.url(),
    screenshot_path: outPath,
    body_has_loading: /\bLoading\b|Loading\.\.\./.test(bodyText),
    body_has_order_id: bodyText.includes(orderId),
    body_excerpt: bodyText.slice(0, 1000),
  };
  fs.writeFileSync(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(summary, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
