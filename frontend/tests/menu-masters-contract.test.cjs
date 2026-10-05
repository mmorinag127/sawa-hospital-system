const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../src/services/menuMasters.ts"), "utf8");
const context = { exports: {}, URLSearchParams };
const vm = require("node:vm");
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
const api = context.exports;
const plain = value => JSON.parse(JSON.stringify(value));

const draft = {
  name: "  鮭の塩焼き  ", unit_type: "g", qty_per_serving: 0, bag_max_qty: "12", bag_max_unit: "count",
  temp_type: "hot", daypart: "夕食", category: "主菜", condiments: [" レモン ", "", "醤油"],
};

test("menu master payload retains all nine fields plus null and zero semantics", () => {
  assert.deepEqual(plain(api.createMenuMasterPayload(draft)), {
    name: "鮭の塩焼き", unit_type: "g", qty_per_serving: 0, bag_max_qty: 12, bag_max_unit: "count",
    temp_type: "hot", daypart: "夕食", category: "主菜", condiments: ["レモン", "醤油"],
  });
  assert.deepEqual(plain(api.createMenuMasterPayload({ ...draft, qty_per_serving: "", bag_max_qty: null, unit_type: "" })), {
    name: "鮭の塩焼き", unit_type: null, qty_per_serving: null, bag_max_qty: null, bag_max_unit: "count",
    temp_type: "hot", daypart: "夕食", category: "主菜", condiments: ["レモン", "醤油"],
  });
});

test("menu master update carries the actually read integer revision and rejects invalid drafts", () => {
  assert.equal(api.updateMenuMasterPayload({ id: "MNU1", revision: 7, ...draft }, draft).revision, 7);
  assert.throws(() => api.updateMenuMasterPayload({ id: "MNU1", revision: 7.2, ...draft }, draft), /再読込/);
  assert.throws(() => api.createMenuMasterPayload({ ...draft, name: " " }), /必須/);
  assert.throws(() => api.createMenuMasterPayload({ ...draft, bag_max_qty: "nope" }), /数値/);
});

test("menu master list uses explicit bounded pages rather than a hidden all-record limit", () => {
  assert.deepEqual(plain(api.listMenuMasterParams(" 鮭 ", 2)), { q: "鮭", offset: 100, limit: 50, sort: "name", order: "asc" });
  assert.deepEqual(plain(api.listMenuMasterParams("", 0, 25)), { offset: 0, limit: 25, sort: "name", order: "asc" });
});

test("menu master search URL changes only q and preserves known route values", () => {
  assert.equal(api.menuMasterSearchUrl("/hospital/menu-masters?view=compact&q=old#list", " 鮭 "), "/hospital/menu-masters?view=compact&q=%E9%AE%AD#list");
  assert.equal(api.menuMasterSearchUrl("/hospital/menu-masters?view=compact&q=old", ""), "/hospital/menu-masters?view=compact");
});

test("menu master URL preserves valid paging and rejects malformed values", () => {
  assert.deepEqual(plain(api.parseMenuMasterUrl("/menu-masters?q=%E9%AE%AD&page=2&pageSize=25#list")), { q: "鮭", page: 2, pageSize: 25 });
  assert.equal(api.menuMasterListUrl("/menu-masters?view=compact#list", { q: "鮭", page: 2, pageSize: 25 }), "/menu-masters?view=compact&q=%E9%AE%AD&page=2&pageSize=25#list");
  assert.throws(() => api.parseMenuMasterUrl("/menu-masters?page=-1"), /ページ指定/);
  assert.throws(() => api.parseMenuMasterUrl("/menu-masters?pageSize=2000"), /ページ指定/);
});

test("menu master rejects malformed list and saved-record responses", () => {
  assert.throws(() => api.parseMenuMasterList({ items: [], total: "0", offset: 0, limit: 50 }), /応答/);
  assert.throws(() => api.parseMenuMaster({ id: "MNU1", name: "鮭", revision: 1.2 }), /保存結果/);
});

const validRecord = { ...plain(api.createMenuMasterPayload(draft)), id: "MNU1", revision: 1 };
for (const revision of [0, -1, undefined, 1.5]) {
  test("rejects response/update revision " + String(revision), () => {
    assert.throws(() => api.parseMenuMaster({ ...validRecord, revision }), /保存結果/);
    assert.throws(() => api.updateMenuMasterPayload({ ...validRecord, revision }, draft), /再読込/);
  });
}
for (const field of ["name", "unit_type", "qty_per_serving", "bag_max_qty", "bag_max_unit", "temp_type", "daypart", "category", "condiments"]) {
  test("rejects missing response field " + field, () => {
    const record = { ...validRecord }; delete record[field];
    assert.throws(() => api.parseMenuMaster(record), /保存結果/);
  });
}
for (const field of ["qty_per_serving", "bag_max_qty"]) {
  for (const value of [NaN, Infinity, -Infinity]) {
    test("rejects non-finite " + field + " " + String(value), () => {
      assert.throws(() => api.parseMenuMaster({ ...validRecord, [field]: value }), /保存結果/);
    });
  }
}
for (const condiments of [null, "塩", [1], [{}], [null]]) {
  test("rejects malformed condiments " + JSON.stringify(condiments), () => {
    assert.throws(() => api.parseMenuMaster({ ...validRecord, condiments }), /保存結果/);
  });
}
for (const page of [{ offset: -1, limit: 50 }, { offset: 0, limit: 0 }]) {
  test("rejects invalid list bounds " + JSON.stringify(page), () => {
    assert.throws(() => api.parseMenuMasterList({ items: [validRecord], total: 1, ...page }), /応答/);
  });
}

test("menu master errors never expose server English details", () => {
  const expected = { 401: "ログイン期限が切れました。ログインし直してください。", 403: "この操作を行う権限がありません。", 404: "対象が存在しません。一覧を確認してください。", 409: "新しい内容があります。再読込してください。", 422: "入力内容を確認してください。" };
  for (const [status, text] of Object.entries(expected)) assert.equal(api.menuMasterErrorMessage({ response: { status: Number(status), data: { detail: "internal English detail" } } }), text);
  assert.equal(api.menuMasterErrorMessage(new Error("untrusted English error")), "通信または保存に失敗しました。");
  assert.match(api.menuMasterErrorMessage(new Error("メニュー名は必須です。")), /メニュー名/);
  assert.match(api.menuMasterErrorMessage(new Error("1人前数量は数値で入力してください。")), /数量/);
});
