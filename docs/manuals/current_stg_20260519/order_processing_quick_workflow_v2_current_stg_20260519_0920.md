---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section { font-family: "Noto Sans JP", "Hiragino Sans", sans-serif; color: #17201d; background: #f7f8f5; }
  h1 { font-size: 34px; }
  h2 { font-size: 28px; }
  p, li { font-size: 19px; line-height: 1.55; }
  img { max-height: 520px; border: 1px solid #d6ddd9; border-radius: 6px; }
  .small { font-size: 14px; color: #53615c; }
  .warn { color: #9b3d12; font-weight: 700; }
  .ok { color: #1d5b45; font-weight: 700; }
---

# 注文処理 workflow-v2 簡易マニュアル
エラーがない前提の最短パス

- 対象URL: https://web-stg-avlnzjjrca-dt.a.run.app/orders/ORDb1702157/workflow-v2
- 注文ID: ORDb1702157
- 取得日時: 2026年5月19日火曜日 9:20:39 JST
- stg revision: web-stg-00200-xf6
- buildId: hU_EpWS8vP9LhGph2MCQk

---

## 最短フロー

```text
workflow-v2を開く
 -> Step1で施設・週・OCR方式を確認
 -> OCRを実行
 -> Step2でOCR結果を選択
 -> Step3でシート生成、異常チェック、シート保存
 -> Step4で出力確認を作成
 -> プレビュー確認
 -> 確定して一覧にもどる
```

---

## 1. workflow-v2を開く

![header](./screens/order_processing_workflow_v2/01_workflow_v2_header_marked.png)

- **注文処理 v2** と表示されていることを確認します。

---

## 2. Step1で施設と週を確認

![facility](./screens/order_processing_workflow_v2/03_facility_select_marked.png)

- 施設が注文書と一致していることを確認します。

---

## 3. 週を確認

![week](./screens/order_processing_workflow_v2/04_week_select_marked.png)

- 対象週が注文書と一致していることを確認します。

---

## 4. OCR方式を確認して実行

![ocr](./screens/order_processing_workflow_v2/09_run_ocr_disabled_marked.png)

- 通常は箱館方式のまま **OCRを実行** を押します。

---

## 5. OCR結果を確認

![step2](./screens/order_processing_workflow_v2/13_step2_screen_marked.png)

- Step2で採用するOCR結果を確認します。

---

## 6. シートを作成して保存

![step3](./screens/order_processing_workflow_v2/15_step3_screen_marked.png)

- **選択OCRからシート生成**、**異常チェック**、**シート確認**、**シートを保存** の順に進めます。

---

## 7. 出力確認を作成して確定

![step4](./screens/order_processing_workflow_v2/17_step4_screen_marked.png)

- **出力確認を作成** を押します。
- プレビューで問題なければ **確定して一覧にもどる** を押します。
