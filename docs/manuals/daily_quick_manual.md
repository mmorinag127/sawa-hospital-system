---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section { font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; font-size: 26px; }
  h1 { font-size: 42px; }
  img { max-height: 430px; object-fit: contain; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }
  .shot img { width: 100%; max-height: 490px; }
---

# 最短: 日別出力

通常の出力だけを最短で進める版。迷ったら詳細版を開く。

---

# 1. 日別出力を開く

<div class="two"><div>

ダッシュボードの `日別出力へ` を押す。

</div><div class="shot">

![daily](./action_screenshots_v3/dashboard_daily_link.png)

</div></div>

---

# 2. 日付と状態を選ぶ

<div class="two"><div>

発送対象日を選び、通常は状態を `確定` にする。選んだら `取得`。

注文が出ない場合は日付・状態・注文確定状態を確認。

</div><div class="shot">

![filter](./action_screenshots_v3/daily_fetch_button.png)

</div></div>

---

# 3. 一括Excelを出す

<div class="two"><div>

通常は `当日一括Excel`。個別に必要なら `当日ラベルExcel` / `当日納品書Excel`。

一部失敗したら、当日注文一覧の個別ボタンで切り分ける。

</div><div class="shot">

![bundle](./action_screenshots_v3/daily_bundle_button.png)

</div></div>

---

# 4. 個別出力する

<div class="two"><div>

1注文だけ確認したい場合は、注文行の `ラベル` / `納品書` / `総量CSV` を使う。

数量が変なら `詳細` から注文詳細へ戻る。

</div><div class="shot">

![row label](./action_screenshots_v2/daily_order_label.png)

</div></div>

---

# 5. 迷ったら詳細へ戻る

<div class="two"><div>

注文行の `詳細` を押し、原因のSTEPに戻る。

- 施設/週: STEP1
- OCR/シート: STEP2
- 明細: STEP3
- 袋わけ/出力: STEP4/5

</div><div class="shot">

![detail](./action_screenshots_v2/daily_order_detail.png)

</div></div>
