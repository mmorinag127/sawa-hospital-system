const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");

const ORDERS = [
  "ORD7a83fd79",
  "ORD50ba2861",
  "ORD9d8f9c2b",
  "ORDf2b6d176",
  "ORDb0380b55",
  "ORD06a73697",
  "ORDc1523a34",
  "ORDd997314d",
  "ORD7499ca9f",
  "ORDddc8c84d",
  "ORD596231b6",
  "ORDc1d8897c",
  "ORDf494e220",
  "ORDad29ab76",
];

const outDir = path.resolve(process.cwd(), "tmp", "first_row_root_fix_stg_verify", "live_step2_screens");

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

async function captureOrder(context, orderId) {
  const page = await context.newPage();
  const outPath = path.join(outDir, `${orderId}_step2_overlay.png`);
  const overlayOnlyPath = path.join(outDir, `${orderId}_hakodate_overlay_element.png`);
  const started = Date.now();
  await page.goto(`https://web-stg-avlnzjjrca-dt.a.run.app/orders/${orderId}`, {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  if (page.url().includes("/login")) {
    await page.locator('input[name="username"], input[autocomplete="username"]').first().fill(context._credentials.username);
    await page.locator('input[name="password"], input[type="password"]').first().fill(context._credentials.password);
    await page.getByRole("button", { name: /セッションで進む|ログイン|Sign in/i }).last().click();
    await page.waitForURL(`**/orders/${orderId}`, { timeout: 120000 }).catch(() => {});
  }
  await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});
  const next = page.getByRole("button", { name: /次へ: OCR修正/ }).first();
  if (await next.count()) {
    await next.click({ timeout: 30000 });
  } else {
    await page.getByText("OCR修正", { exact: true }).first().click({ timeout: 30000 }).catch(() => {});
  }
  await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});
  const refreshButton = page.getByRole("button", { name: /OCRページを更新|OCR表示を再取得/ }).first();
  if (await refreshButton.count()) {
    await refreshButton.click({ timeout: 30000 }).catch(() => {});
    await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(15000);
  }
  await page.getByTestId("hakodate-overlay-preview").first().waitFor({ timeout: 120000 });
  const overlayWrapper = page
    .locator('[data-testid="ocr-preview-wrapper"][data-preview-mode="hakodate-overlay"]')
    .first();
  await overlayWrapper.waitFor({ timeout: 120000 });
  await overlayWrapper.scrollIntoViewIfNeeded({ timeout: 30000 });
  await page.locator('img[alt="Hakodate quantity overlay base"]').first().waitFor({ timeout: 120000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: outPath, fullPage: false });
  await overlayWrapper.screenshot({ path: overlayOnlyPath });
  const bodyText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => "");
  const overlayImageSrc = await page
    .locator('img[alt="Hakodate quantity overlay base"]')
    .first()
    .getAttribute("src")
    .catch(() => null);
  await page.close();
  return {
    order_id: orderId,
    elapsed_seconds: Number(((Date.now() - started) / 1000).toFixed(3)),
    screenshot_path: outPath,
    overlay_element_screenshot_path: overlayOnlyPath,
    overlay_image_src: overlayImageSrc,
    has_loading: /Loading\.\.\.|Loading/.test(bodyText),
    has_hakodate_overlay: /箱館オーバーレイ|箱館方式/.test(bodyText),
    has_ocr_confidence: /OCR信頼度表示/.test(bodyText),
    excerpt: bodyText.slice(0, 800),
  };
}

async function main() {
  fs.mkdirSync(outDir, { recursive: true });
  const credentials = loadOperatorCredentials();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    httpCredentials: credentials,
    viewport: { width: 1920, height: 1600 },
  });
  context._credentials = credentials;
  const basicToken = Buffer.from(`${credentials.username}:${credentials.password}`, "utf8").toString("base64");
  await context.addInitScript((token) => {
    window.sessionStorage.setItem("auth_header", `Basic ${token}`);
    window.sessionStorage.setItem("auth_next", window.location.pathname + window.location.search);
  }, basicToken);
  const summary = [];
  for (const orderId of ORDERS) {
    const item = await captureOrder(context, orderId);
    console.log(JSON.stringify(item));
    summary.push(item);
  }
  await browser.close();
  fs.writeFileSync(path.join(outDir, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`, "utf8");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
