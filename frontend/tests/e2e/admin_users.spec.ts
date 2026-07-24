import { expect, test } from "@playwright/test";

type PortalUser = {
  id: string;
  account: string;
  role: "admin" | "operator";
  status: "active" | "inactive";
  systems: Array<"hospital" | "shift" | "school-lunch">;
};

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

const prepareSession = async (page: import("@playwright/test").Page) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });
};

test("admin sees the shared template and an explicit successful save result", async ({ page }) => {
  await prepareSession(page);
  const users: PortalUser[] = [
    {
      id: "admin-1",
      account: "admin@example.com",
      role: "admin",
      status: "active",
      systems: ["hospital", "shift", "school-lunch"],
    },
  ];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method().toUpperCase();

    if (path.endsWith("/auth/me") && method === "GET") {
      await route.fulfill({ status: 200, json: { role: "admin" } });
      return;
    }
    if (path.endsWith("/portal/users") && method === "GET") {
      await route.fulfill({ status: 200, json: { items: users } });
      return;
    }
    if (path.endsWith("/portal/users") && method === "POST") {
      const body = request.postDataJSON() as Omit<PortalUser, "id">;
      const saved: PortalUser = { id: "operator-1", ...body };
      users.push(saved);
      await route.fulfill({ status: 200, json: { user: saved } });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  await page.goto(`${baseUrl}/admin/users`);

  await expect(page.locator('[data-page-template="sawa"]')).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "共通ユーザー管理" })).toBeVisible();
  await expect(page.getByText("admin@example.com", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "ユーザーを追加" }).click();
  await page.getByLabel("メールアドレス").fill("operator@example.com");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("operator@example.com を保存しました。");
  await expect(page.getByText("operator@example.com", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "保存中…" })).toHaveCount(0);
});

test("admin sees an explicit save error and can retry without losing the form", async ({ page }) => {
  await prepareSession(page);

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method().toUpperCase();
    if (path.endsWith("/auth/me") && method === "GET") {
      await route.fulfill({ status: 200, json: { role: "admin" } });
      return;
    }
    if (path.endsWith("/portal/users") && method === "GET") {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith("/portal/users") && method === "POST") {
      await route.fulfill({ status: 409, json: { detail: "Account already exists" } });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  await page.goto(`${baseUrl}/admin/users`);
  await page.getByRole("button", { name: "ユーザーを追加" }).click();
  await page.getByLabel("メールアドレス").fill("duplicate@example.com");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect(
    page.getByText("ユーザーを保存できませんでした: Account already exists", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("メールアドレス")).toHaveValue("duplicate@example.com");
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
});

test("operator cannot see or request the user list", async ({ page }) => {
  await prepareSession(page);
  let userListRequests = 0;

  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 200, json: { role: "operator" } });
      return;
    }
    if (path.endsWith("/portal/users")) {
      userListRequests += 1;
      await route.fulfill({ status: 403, json: { detail: "Forbidden" } });
      return;
    }
    if (path.endsWith("/portal/auth/me")) {
      await route.fulfill({ status: 200, json: { role: "operator", systems: ["hospital"] } });
      return;
    }
    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/admin/users`);
  await expect(page.getByRole("heading", { name: "管理者権限が必要です" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "ユーザー一覧" })).toHaveCount(0);
  expect(userListRequests).toBe(0);

  await page.goto(`${baseUrl}/`);
  await expect(page.locator('a[href="/admin/users"]')).toHaveCount(0);
});
